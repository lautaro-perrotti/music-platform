from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from copilot.audio.live_capture import (
    STAGING_BASS,
    STAGING_KICK,
    STAGING_NAME,
    AudioAsset,
    AudioCaptureError,
    _clear_staging,
    _exclusive_open_ok,
    _fire_session_clips,
    _playing_clip_tracks,
    _restore_transport,
    _stop_session_clips,
    _transport_snapshot,
    _wait_for_wav,
    assert_constant_tempo,
    beats_to_seconds,
    capture_audio_segment,
    capture_dir,
    find_tap,
    set_tap_enabled,
    set_tap_recording,
    set_tap_slot,
    set_taps_recording,
    staging_path,
    take_staging,
    unique_capture_path,
    validate_wav,
    wait_until_wav_shared_readable,
    write_analysis_wav,
)
from copilot.audio.arrangement_seek import (
    PRE_ROLL_QN,
    TRANSPORT_PRIMITIVE_VERSION,
    start_arrangement_at_qn,
    transport_target_qn,
)
from copilot.audio.semantics import DEFAULT_PREROLL_BEATS, region_windows
from copilot.audio.views import (
    _pick_output,
    route_capture_from_track,
)
from copilot.audio.tap_trust import (
    assert_unique_slots,
    inventory_taps,
    reconcile_stale_taps,
    reserve_capture_dests,
    tap_instance_id,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.schemas.observation import CaptureView, ObservationSource, SignalPoint

CAPTURE_HOST = "Copilot Capture"
CAPTURE_KICK = "Copilot Capture Kick"
CAPTURE_BASS = "Copilot Capture Bass"
SLOT_FILES = {0: STAGING_NAME, 1: STAGING_KICK, 2: STAGING_BASS}


def mixer_fingerprint(daw: AbletonTcpAdapter) -> list[dict[str, object]]:
    count = int(daw.health().get("track_count") or 0)
    rows: list[dict[str, object]] = []
    for index in range(count):
        info = daw.get_track_info(index)
        rows.append(
            {
                "index": index,
                "name": info.get("name"),
                "mute": bool(info.get("mute", False)),
                "solo": bool(info.get("solo", False)),
                "output": info.get("output_routing_type"),
            }
        )
    return rows


def ensure_named_audio_track(
    daw: AbletonTcpAdapter, name: str, *, snapshot: bool = True
) -> dict[str, object]:
    existing_index = None
    if daw.last_track_infos:
        for index, info in daw.last_track_infos.items():
            if str(info.get("name") or "") == name:
                existing_index = int(index)
                break
    if existing_index is None:
        if snapshot or not daw.last_track_infos:
            session = daw.snapshot(include_notes=False)
            existing = session.track_by_name(name)
            if existing is not None:
                existing_index = existing.index
    if existing_index is not None:
        return {"created": False, "index": existing_index, "name": name}
    created = daw.create_audio_track(name, -1)
    index = int(created["index"])
    return {"created": True, "index": index, "name": name}


def load_tap_on_track(daw: AbletonTcpAdapter, track_index: int) -> dict[str, object]:
    from copilot.audio.live_capture import _find_tap_uri, install_audio_tap_device, tap_has_slot

    existing = find_tap(daw, track_index)
    if existing is not None and tap_has_slot(daw, track_index):
        return {"already_loaded": True, "device": existing, "has_slot": True}
    install_audio_tap_device()
    time.sleep(0.4)
    uri = _find_tap_uri(daw)
    if not uri:
        if existing is not None:
            return {"already_loaded": True, "device": existing, "has_slot": False}
        raise AudioCaptureError(
            "TAP_MISSING",
            "Copilot Audio Tap is not on this audio track and Live browser "
            "cannot load it. Drop the updated devices/Copilot Audio Tap.amxd "
            "onto this audio track.",
        )
    loaded = daw.load_instrument_or_effect(track_index, uri)
    if loaded.get("error"):
        loaded = daw.load_browser_item(track_index, uri)
    from copilot.audio.live_capture import find_taps_on_track

    # Browser load of the tap is asynchronous; poll (bounded) until a
    # Slot-enabled tap appears instead of sleeping once and giving up.
    slotted = None
    deadline = time.monotonic() + 15.0
    while slotted is None and time.monotonic() < deadline:
        for device in find_taps_on_track(daw, track_index, refresh=True):
            params = daw.get_device_parameters(track_index, int(device["index"]))
            names = [str(item.get("name") or "").lower() for item in params.get("parameters") or []]
            if "slot" in names:
                slotted = device
                break
        if slotted is None:
            time.sleep(0.5)
    found = find_taps_on_track(daw, track_index, refresh=True)
    if slotted is None:
        if existing is not None:
            for device in found:
                if int(device["index"]) != int(existing["index"]):
                    try:
                        daw.delete_device(track_index, int(device["index"]))
                    except DawError:
                        pass
            return {
                "already_loaded": True,
                "device": existing,
                "has_slot": False,
                "load": loaded,
            }
        raise AudioCaptureError(
            "TAP_MISSING",
            f"load reported {loaded} but track {track_index} has no Slot-enabled tap",
        )
    if existing is not None and int(existing["index"]) != int(slotted["index"]):
        try:
            daw.delete_device(track_index, int(existing["index"]))
        except DawError:
            pass
        found = find_taps_on_track(daw, track_index)
        for device in found:
            params = daw.get_device_parameters(track_index, int(device["index"]))
            names = [str(item.get("name") or "").lower() for item in params.get("parameters") or []]
            if "slot" in names:
                slotted = device
                break
    return {"already_loaded": False, "device": slotted, "has_slot": True, "load": loaded}


def route_host_post_mixer(
    daw: AbletonTcpAdapter,
    host_index: int,
    target_name: str,
) -> dict[str, object]:
    before = {
        "input": daw.get_track_input_routing(host_index),
        "output": daw.get_track_output_routing(host_index),
    }
    routed = route_capture_from_track(daw, host_index, target_name, "Post Mixer")
    try:
        daw.set_track_monitoring(host_index, "in")
    except DawError:
        pass
    try:
        monitor = daw.get_track_monitoring(host_index)
    except DawError:
        monitor = {}
    outs = list(daw.get_available_outputs(host_index).get("available_outputs") or [])
    off = _pick_output(outs, "No Output", "Sends Only")
    if not off:
        raise AudioCaptureError(
            "CAPTURE_ROUTING_UNSUPPORTED",
            f"capture host cannot leave Main; outputs={outs}",
        )
    set_out = daw.set_track_output_routing(host_index, off, "")
    check = daw.get_track_output_routing(host_index)
    out_type = str(check.get("output_routing_type") or set_out.get("output_routing_type") or "")
    through_main = out_type.lower() in {"main", "master"}
    return {
        "before": before,
        "input_type": routed.get("input_type"),
        "input_channel": routed.get("input_channel"),
        "signal_point": routed.get("signal_point"),
        "output": out_type,
        "through_main": through_main,
        "monitoring": monitor.get("monitoring") or monitor.get("value"),
        "available_outputs": outs,
    }


def restore_host_routing(daw: AbletonTcpAdapter, host_index: int, before: dict) -> list[str]:
    errors: list[str] = []
    try:
        prev_in = str((before.get("input") or {}).get("input_routing_type") or "")
        if prev_in:
            daw.set_track_input_routing(
                host_index,
                prev_in,
                str((before.get("input") or {}).get("input_routing_channel") or ""),
            )
    except DawError:
        errors.append("host input unrestored")
    try:
        prev_out = str((before.get("output") or {}).get("output_routing_type") or "")
        if prev_out:
            check = daw.set_track_output_routing(
                host_index,
                prev_out,
                str((before.get("output") or {}).get("output_routing_channel") or ""),
            )
            if str(check.get("output_routing_type") or "") != prev_out:
                errors.append("host output unrestored")
    except DawError:
        errors.append("host output restore failed")
    return errors


def _routing_already_correct(
    info: dict[str, Any],
    *,
    target_name: str,
    channel_substr: str = "post mixer",
) -> bool:
    input_type = str(info.get("input_routing_type") or "")
    channel = str(info.get("input_routing_channel") or "")
    output = str(info.get("output_routing_type") or "")
    monitor = str(info.get("monitoring") or "")
    from_target = target_name.lower() in input_type.lower()
    post = channel_substr in channel.lower()
    monitor_in = monitor.lower() in {"in", "in/in"}
    off_main = output.lower() in {"no output", "sends only"}
    return from_target and post and monitor_in and off_main


def _sends_already_silent(sends: list[dict[str, Any]]) -> bool:
    from copilot.audio.tap_trust import interpret_send_level

    if not sends:
        return False
    return all(interpret_send_level(row).get("semantic_silence") for row in sends)


def ensure_capture_host_ready(
    daw: AbletonTcpAdapter,
    *,
    name: str,
    target_name: str,
    slot: int,
    inventory: list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    """Read current host routing/slot; write only mismatches. Production path."""
    mutations: list[str] = []
    track = ensure_named_audio_track(daw, name, snapshot=False)
    host_index = int(track["index"])
    info = daw.last_track_infos.get(host_index) or daw.get_track_info(host_index)
    tap_row = None
    for row in inventory or []:
        if int(row.get("track_index", -999)) == host_index:
            tap_row = row
            break
    device_index = None if tap_row is None else tap_row.get("device_index")
    if device_index is None:
        tap = find_tap(daw, host_index)
        if tap is None:
            raise AudioCaptureError(
                "TAP_MISSING",
                f"{name} has no Copilot Audio Tap. Drop devices/Copilot Audio Tap.amxd.",
            )
        device_index = int(tap["index"])
    current_slot = None if tap_row is None else tap_row.get("slot")
    slot_set: dict[str, object] = {"slot": current_slot, "skipped": True}
    if current_slot != slot:
        slot_set = set_tap_slot(
            daw,
            host_index,
            slot,
            device_index=int(device_index),
            parameter_index=(
                None if tap_row is None else tap_row.get("slot_param_index")
            ),
        )
        mutations.append(f"slot->{slot}")
    routing = {
        "input_type": info.get("input_routing_type"),
        "input_channel": info.get("input_routing_channel"),
        "output": info.get("output_routing_type"),
        "monitoring": info.get("monitoring"),
        "through_main": str(info.get("output_routing_type") or "").lower()
        in {"main", "master"},
        "skipped": True,
    }
    if not _routing_already_correct(info, target_name=target_name):
        routing = route_host_post_mixer(daw, host_index, target_name)
        routing["skipped"] = False
        mutations.append("routing")
        info = daw.get_track_info(host_index)
    sends = list(info.get("sends") or []) or daw.get_track_sends(host_index)
    if sends and not _sends_already_silent(sends):
        for row in sends:
            daw.set_send_level(host_index, int(row.get("send_index", 0)), 0.0)
            mutations.append(f"send:{row.get('send_index')}")
        sends = daw.get_track_sends(host_index)
    from copilot.audio.semantics import classify_signal_point
    from copilot.schemas.observation import SignalPoint

    point = classify_signal_point(str(routing.get("input_channel") or ""))
    if point is SignalPoint.UNKNOWN:
        point = SignalPoint.TRACK_POST_MIXER
    return {
        "name": name,
        "index": host_index,
        "created": track["created"],
        "tap": {"index": device_index},
        "slot": slot_set,
        "staging": SLOT_FILES.get(slot, STAGING_NAME),
        "input_type": routing.get("input_type"),
        "input_channel": routing.get("input_channel"),
        "output": routing.get("output"),
        "monitoring": routing.get("monitoring"),
        "through_main": routing.get("through_main"),
        "signal_point": point,
        "sends": sends,
        "routing_mutations": len(mutations),
        "mutations": mutations,
    }


def setup_capture_host(
    daw: AbletonTcpAdapter,
    *,
    name: str,
    target_name: str,
    slot: int,
) -> dict[str, object]:
    track = ensure_named_audio_track(daw, name)
    host_index = int(track["index"])
    try:
        tap = load_tap_on_track(daw, host_index)
    except AudioCaptureError:
        # Leave the named audio track. Browser load is unreliable; the
        # empty host is the drop target for one manual .amxd drag.
        raise
    slot_set = set_tap_slot(daw, host_index, slot)
    routing = route_host_post_mixer(daw, host_index, target_name)
    return {
        "name": name,
        "index": host_index,
        "created": track["created"],
        "tap": tap.get("device"),
        "slot": slot_set,
        "staging": SLOT_FILES.get(slot, STAGING_NAME),
        **routing,
    }


def _mono_file(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), always_2d=True)
    return np.mean(np.asarray(data, dtype=np.float64), axis=1), int(sr)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    if n < 16:
        return 0.0
    left = a[:n] - float(np.mean(a[:n]))
    right = b[:n] - float(np.mean(b[:n]))
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denom <= 0:
        return 0.0
    return float(np.dot(left, right) / denom)


def kick_content_check(
    capture_path: str,
    *,
    kick_ref: str | None,
    master_ref: str | None,
) -> dict[str, Any]:
    from copilot.audio.lowend import detect_transients, prune_kick_attacks

    mono, sr = _mono_file(Path(capture_path))
    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(mono**2)))
    hits = detect_transients(mono, sr, role="kick")
    pruned = prune_kick_attacks(hits["attacks"], duration_s=len(mono) / float(sr))
    vs_kick = None
    vs_master = None
    if kick_ref and Path(kick_ref).is_file():
        ref, _ = _mono_file(Path(kick_ref))
        vs_kick = _corr(mono, ref)
    if master_ref and Path(master_ref).is_file():
        ref, _ = _mono_file(Path(master_ref))
        vs_master = _corr(mono, ref)
    more_kick_than_mix = (
        vs_kick is not None and vs_master is not None and vs_kick > vs_master + 0.05
    )
    master_rms = None
    if master_ref and Path(master_ref).is_file():
        master_mono, _ = _mono_file(Path(master_ref))
        master_rms = float(np.sqrt(np.mean(master_mono**2)))
    rms_vs_master = None if master_rms in (None, 0.0) else rms / master_rms
    looks_like_mix = vs_master is not None and vs_master >= 0.85
    quieter_than_mix = rms_vs_master is None or rms_vs_master < 0.85
    looks_like_kick = (
        peak > 1.0e-3
        and len(pruned) >= 2
        and not looks_like_mix
        and quieter_than_mix
    )
    return {
        "peak": peak,
        "rms": rms,
        "kick_events": len(pruned),
        "raw_onsets": hits.get("count"),
        "corr_vs_prior_kick_isolate": vs_kick,
        "corr_vs_prior_master": vs_master,
        "more_like_kick_than_mix": more_kick_than_mix,
        "rms_vs_prior_master": rms_vs_master,
        "looks_like_mix": looks_like_mix,
        "quieter_than_mix": quieter_than_mix,
        "looks_like_kick": looks_like_kick,
        "attack_times_s": [float(item["time_s"]) for item in pruned],
        "note": (
            "Prior kick isolate is THROUGH_MASTER_CHAIN; correlation vs that "
            "file is evidence, not proof of a clean stem."
        ),
    }


def probe_capture_track(
    daw: AbletonTcpAdapter,
    *,
    target_name: str,
    target_stable_id: str,
    start_beat: float,
    end_beat: float,
    fire_tracks: list[int],
    known_tempo: float | None = None,
    host_name: str = CAPTURE_HOST,
    kick_ref_wav: str | None = None,
    master_ref_wav: str | None = None,
    context=None,
) -> dict[str, Any]:
    """Minimal experiment: one capture host, no reroute of the rest of the set."""
    t0 = time.perf_counter()
    tcp_before = daw.tcp_stats()
    mixer_before = mixer_fingerprint(daw)
    master_before = daw.get_master_info()
    try:
        host = setup_capture_host(
            daw, name=host_name, target_name=target_name, slot=0
        )
    except AudioCaptureError as exc:
        return {
            "ok": False,
            "elapsed_s": time.perf_counter() - t0,
            "error": str(exc),
            "host": {"name": host_name},
            "wav": None,
            "content": {},
            "gates": {
                "wav_valid": False,
                "signal_present": False,
                "audio_from_kick": False,
                "post_mixer": False,
                "monitor_in": False,
                "no_output": False,
                "not_through_main": False,
                "other_tracks_not_rerouted": True,
                "master_devices_unchanged": True,
                "content_looks_like_kick": False,
                "tap_on_copilot_capture": False,
                "routing_mutations": 0,
            },
            "restore_errors": [],
            "asset": None,
        }
    readback = {
        "input_type": host.get("input_type"),
        "input_channel": host.get("input_channel"),
        "output": host.get("output"),
        "monitoring": host.get("monitoring"),
        "signal_point": (
            host["signal_point"].value
            if hasattr(host.get("signal_point"), "value")
            else str(host.get("signal_point"))
        ),
        "through_main": host["through_main"],
    }
    errors: list[str] = []
    asset = None
    content: dict[str, Any] = {}
    try:
        tempo = known_tempo
        if tempo is None:
            tempo = assert_constant_tempo(daw, start_beat, end_beat)
        asset = capture_audio_segment(
            daw,
            target_stable_id,
            start_beat,
            end_beat,
            fire_tracks=fire_tracks,
            fire_all=False,
            tap_track_index=int(host["index"]),
            isolate_solo=False,
            capture_view=CaptureView.TRACK_ISOLATED,
            observation_source=ObservationSource.TRACK_ISOLATED,
            signal_point=(
                SignalPoint.TRACK_POST_MIXER
                if not host["through_main"]
                and host.get("signal_point") is SignalPoint.TRACK_POST_MIXER
                else SignalPoint.TRACK_POST_MIXER_THROUGH_MASTER_CHAIN
                if host["through_main"]
                else (host.get("signal_point") or SignalPoint.TRACK_POST_MIXER)
            ),
            signal_point_label=str(host.get("input_channel") or host.get("input_type")),
            preroll_beats=DEFAULT_PREROLL_BEATS,
            known_tempo=float(tempo),
            broadcast_udp=False,
            staging_name=STAGING_NAME,
            context=context,
        )
        content = kick_content_check(
            asset.file_path, kick_ref=kick_ref_wav, master_ref=master_ref_wav
        )
    finally:
        errors.extend(restore_host_routing(daw, int(host["index"]), host["before"]))
    mixer_after = mixer_fingerprint(daw)
    others_before = [row for row in mixer_before if int(row["index"]) != int(host["index"])]
    others_after = [row for row in mixer_after if int(row["index"]) != int(host["index"])]
    main_unchanged = others_before == others_after
    master_after = daw.get_master_info()
    master_devices_unchanged = (master_before.get("devices") or []) == (
        master_after.get("devices") or []
    )
    tcp_after = daw.tcp_stats()
    routing_sets = int(tcp_after.get("by_type", {}).get("set_track_output_routing", 0)) - int(
        tcp_before.get("by_type", {}).get("set_track_output_routing", 0)
    )
    target_ok = target_name.lower() in str(readback["input_type"] or "").lower()
    post_mixer = "post mixer" in str(readback["input_channel"] or "").lower() or (
        host.get("signal_point") is SignalPoint.TRACK_POST_MIXER
    )
    monitor_in = str(readback["monitoring"] or "").lower() in {"in", "in/in"}
    off_main = str(readback["output"] or "").lower() in {"no output", "sends only"}
    provenance_ok = (
        target_ok
        and post_mixer
        and monitor_in
        and off_main
        and not host["through_main"]
        and main_unchanged
        and routing_sets <= 4
    )
    content_ok = bool(content.get("looks_like_kick"))
    ok = (
        asset is not None
        and bool(asset.finite_samples)
        and (asset.peak or 0) > 0
        and provenance_ok
        and content_ok
    )
    return {
        "ok": ok,
        "elapsed_s": time.perf_counter() - t0,
        "host": {
            "index": host["index"],
            "name": host["name"],
            "created": host["created"],
            "slot": host.get("slot"),
            **readback,
        },
        "wav": None
        if asset is None
        else {
            "path": asset.file_path,
            "capture_id": asset.capture_id,
            "duration": asset.duration,
            "rms": asset.rms,
            "peak": asset.peak,
            "finite_samples": asset.finite_samples,
            "channels": asset.channels,
            "sample_rate": asset.sample_rate,
            "signal_point": asset.signal_point.value if asset.signal_point else None,
            "signal_point_label": asset.signal_point_label,
        },
        "content": content,
        "gates": {
            "wav_valid": asset is not None and bool(asset.finite_samples),
            "signal_present": bool(asset and (asset.peak or 0) > 0),
            "audio_from_kick": target_ok,
            "post_mixer": post_mixer,
            "monitor_in": monitor_in,
            "no_output": off_main,
            "not_through_main": not host["through_main"],
            "other_tracks_not_rerouted": main_unchanged,
            "master_devices_unchanged": master_devices_unchanged,
            "content_looks_like_kick": content_ok,
            "routing_mutations": routing_sets,
        },
        "restore_errors": errors,
        "asset": asset,
    }


def _finalize_staging(
    *,
    staging: Path,
    start_beat: float,
    end_beat: float,
    tempo: float,
    play_offset: float,
    live_sr: int | None,
    require_signal: bool,
    source: str,
    source_type: str,
    capture_view: CaptureView,
    observation_source: ObservationSource,
    signal_point: SignalPoint,
    signal_point_label: str | None,
    session_revision: int,
    t_play: float,
    t_rec_stop: float,
    transport_start: float | None,
    transport_stop: float | None,
    dest_path: Path | None = None,
    capture_id: str | None = None,
    pass_id: str | None = None,
    slot: int | None = None,
    routing: str | None = None,
    returns_included: bool | None = None,
    lock_play_offset: bool = False,
) -> AudioAsset:
    expected = beats_to_seconds(end_beat - start_beat, tempo)
    _wait_for_wav(
        staging,
        timeout=12.0,
        min_duration_s=expected,
        expected_sr=live_sr,
    )
    if dest_path is None:
        analysis_path = unique_capture_path()
        capture_id = analysis_path.stem.removeprefix("capture_")
    else:
        analysis_path = Path(dest_path)
        if analysis_path.exists():
            raise AudioCaptureError("CAPTURE_PATH_COLLISION", str(analysis_path))
        capture_id = capture_id or analysis_path.stem.removeprefix("capture_")
    raw_path = capture_dir() / f"{analysis_path.stem}_raw.wav"
    if raw_path.exists():
        raise AudioCaptureError("CAPTURE_PATH_COLLISION", str(raw_path))
    take_staging(staging, raw_path)
    raw_check = validate_wav(
        raw_path,
        expected_sr=live_sr,
        expected_duration=expected,
        require_signal=False,
    )
    if not raw_check.exists or (raw_check.size_bytes or 0) <= 0:
        raise AudioCaptureError("ZERO_BYTE", str(raw_path))
    offset = play_offset
    if not lock_play_offset and t_play and raw_check.duration:
        t_rec_start = t_rec_stop - float(raw_check.duration)
        offset = max(0.0, t_play - t_rec_start)
    trim = write_analysis_wav(
        raw_path,
        analysis_path,
        start_beat=start_beat,
        end_beat=end_beat,
        tempo=tempo,
        play_offset_seconds=offset,
    )
    analysis_check = validate_wav(
        analysis_path,
        expected_sr=int(trim["sample_rate"]),
        expected_duration=expected,
        require_signal=require_signal,
    )
    windows = region_windows(start_beat, end_beat, preroll_beats=DEFAULT_PREROLL_BEATS)
    return AudioAsset(
        capture_id=capture_id,
        raw_file_path=str(raw_path),
        analysis_file_path=str(analysis_path),
        file_path=str(analysis_path),
        source=source,
        source_type=source_type,
        source_stable_id=source if source_type == "TRACK" else None,
        requested_start_beat=start_beat,
        requested_end_beat=end_beat,
        start_beat=start_beat,
        end_beat=end_beat,
        sample_rate=int(analysis_check.sample_rate or 0),
        channels=int(analysis_check.channels or 0),
        raw_duration=float(raw_check.duration or 0.0),
        analysis_duration=float(analysis_check.duration or 0.0),
        duration=float(analysis_check.duration or 0.0),
        session_revision=session_revision,
        tempo=tempo,
        rms=analysis_check.rms,
        peak=analysis_check.peak,
        size_bytes=analysis_check.size_bytes,
        capture_view=capture_view,
        observation_source=observation_source,
        signal_point=signal_point,
        signal_point_label=signal_point_label,
        request_start_beat=float(windows["request_start_beat"]),
        request_end_beat=float(windows["request_end_beat"]),
        capture_start_beat=float(windows["capture_start_beat"]),
        capture_end_beat=float(windows["capture_end_beat"]),
        analysis_start_beat=float(windows["analysis_start_beat"]),
        analysis_end_beat=float(windows["analysis_end_beat"]),
        preroll_beats=float(windows["preroll_beats"]),
        session_revision_at_start=session_revision,
        session_revision_at_end=session_revision,
        finite_samples=bool(analysis_check.finite_samples),
        expected_frames=int(round(expected * int(analysis_check.sample_rate or 0))),
        actual_frames=analysis_check.frames,
        transport_start_observed=transport_start,
        transport_stop_observed=transport_stop,
        slot=slot,
        pass_id=pass_id or (capture_id if dest_path is not None else None),
        routing=routing,
        returns_included=returns_included,
    )


def capture_parallel_pass(
    daw: AbletonTcpAdapter,
    *,
    start_beat: float,
    end_beat: float,
    fire_tracks: list[int],
    recorders: list[dict[str, Any]],
    tempo: float,
    session_revision: int,
    pass_id: str | None = None,
    transport: str = "session",
) -> dict[str, Any]:
    """One transport start/stop. Several taps record at once."""
    timings: dict[str, Any] = {}
    t_all = time.perf_counter()
    from uuid import uuid4

    pass_id = pass_id or uuid4().hex[:12]
    reserved = reserve_capture_dests(recorders, pass_id=pass_id)
    for rec in recorders:
        rec["dest"] = reserved[str(rec["key"])]
    saved = _transport_snapshot(daw)
    live_sr = int(saved["sample_rate"]) if saved.get("sample_rate") else None
    if not daw.last_track_infos:
        daw.snapshot(include_notes=False)
    inventory = inventory_taps(daw)
    assert_unique_slots(inventory)
    recorder_indexes = {int(rec["tap_track_index"]) for rec in recorders}
    recorder_ids = {
        tap_instance_id(int(rec["tap_track_index"]), int(rec.get("device_index", 0)))
        for rec in recorders
    }
    for rec in recorders:
        if rec.get("device_index") is not None:
            continue
        for row in inventory:
            if int(row["track_index"]) == int(rec["tap_track_index"]):
                rec["device_index"] = row["device_index"]
                recorder_ids.add(
                    tap_instance_id(int(rec["tap_track_index"]), int(row["device_index"]))
                )
                break
    stale = reconcile_stale_taps(daw, inventory, recorder_ids)
    timings["tap_inventory"] = inventory
    timings["stale_taps"] = stale
    by_track = {int(row["track_index"]): row for row in inventory}
    breakdown: dict[str, Any] = {}
    timings["fixed_sleeps_remaining"] = ["region_duration_wait"]

    def _mark(name: str, started: float, **extra: Any) -> None:
        breakdown[name] = {"s": time.perf_counter() - started, **extra}

    def _wait_staging_free(*, timeout_s: float = 2.0, fail_closed: bool) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        last: list[dict[str, Any]] = []
        waited = 0.0
        t0 = time.perf_counter()
        while time.time() < deadline:
            free = True
            last = []
            for rec in recorders:
                status = dict(_exclusive_open_ok(staging_path(str(rec["staging"]))))
                last.append({"key": rec.get("key"), **status})
                if status.get("exists") and not status.get("exclusive"):
                    free = False
            if free:
                waited = time.perf_counter() - t0
                return {"ok": True, "waited_s": waited, "rows": last}
            time.sleep(0.02)
        waited = time.perf_counter() - t0
        payload = {"ok": False, "waited_s": waited, "rows": last, "timeout_s": timeout_s}
        if fail_closed:
            raise AudioCaptureError(
                "TAP_STALE_ARMED",
                "staging handles not released: " + str(payload),
            )
        return payload

    t_mut = time.perf_counter()
    t0 = time.perf_counter()
    daw.stop_playback()
    _mark("stop_playback", t0)
    try:
        t0 = time.perf_counter()
        rec0 = set_taps_recording(daw, recorders, False, by_track=by_track)
        _mark("rec_0", t0, **{k: rec0.get(k) for k in ("mode", "skipped", "ok")})
        t0 = time.perf_counter()
        free1 = _wait_staging_free(fail_closed=False)
        _mark("staging_handle_release_pre", t0, **free1)
        t0 = time.perf_counter()
        for rec in recorders:
            _clear_staging(str(rec["staging"]))
        _mark("clear_staging", t0)
        playing_clips = _playing_clip_tracks(daw, fire_tracks)
        if transport == "arrangement" and daw.last_track_infos:
            playing_clips = _playing_clip_tracks(
                daw, [int(index) for index in daw.last_track_infos]
            )
        t0 = time.perf_counter()
        if playing_clips:
            stop_pre = _stop_session_clips(daw, playing_clips)
            _mark(
                "stop_clip_prepare",
                t0,
                mode=stop_pre.get("mode"),
                tracks=playing_clips,
            )
        else:
            _mark("stop_clip_prepare", t0, mode="skipped", tracks=[])
        t0 = time.perf_counter()
        if transport == "arrangement":
            target_qn = transport_target_qn(float(start_beat), pre_roll_qn=PRE_ROLL_QN)
            _mark(
                "set_song_time",
                t0,
                requested_qn=float(start_beat),
                transport_target_qn=target_qn,
                pre_roll_qn=PRE_ROLL_QN,
                parked="atomic_start_will_seek",
            )
        else:
            daw.set_current_song_time(0.0)
            _mark("set_song_time", t0, beat=0.0, transport=transport)
        t0 = time.perf_counter()
        rec1 = set_taps_recording(daw, recorders, True, by_track=by_track)
        armed_at = rec1.get("armed_at") or {}
        _mark("rec_1", t0, mode=rec1.get("mode"), ok=rec1.get("ok"))
        timings["recorder_start_s"] = time.perf_counter() - t_mut
        timings["prepare_breakdown"] = breakdown
        t_fire = time.perf_counter()
        if transport == "arrangement":
            target_qn = transport_target_qn(float(start_beat), pre_roll_qn=PRE_ROLL_QN)
            play = start_arrangement_at_qn(daw, float(target_qn))
            fire_result = {"mode": "arrangement_start_playback_at_qn", "result": play}
            t_play = float(play["t_play_command_wall"])
            transport_start = float(play["implied_start_qn"])
            t_audio_start = t_play
            polls = 0
            timings["transport_start_provenance"] = {
                "TRANSPORT_START_PROVENANCE": play.get("TRANSPORT_START_PROVENANCE"),
                "requested_qn": float(start_beat),
                "transport_target_qn": float(target_qn),
                "verified_transport_start_qn": play.get("implied_start_qn"),
                "observed_qn": play.get("observed_qn"),
                "expected_progress_qn": play.get("expected_progress_qn"),
                "poll_delay_s": play.get("poll_delay_s"),
                "t_play_command": play.get("t_play_command"),
                "t_play_return": play.get("t_play_return"),
                "t_first_poll": play.get("t_first_poll"),
                "t_play_command_wall": play.get("t_play_command_wall"),
                "verification_wall_time": play.get("t_tick_wall"),
                "tolerance_qn": play.get("tolerance_qn"),
                "pre_roll_qn": PRE_ROLL_QN,
                "transport_primitive_version": play.get("transport_primitive_version"),
                "analysis_anchor": "verified_transport_start not first TCP poll",
            }
        else:
            fire_result = _fire_session_clips(daw, fire_tracks)
            t_audio_start = time.time()
            t_play = None
            transport_start = None
            polls = 0
            deadline = time.time() + 2.0
            while time.time() < deadline:
                polls += 1
                pos = daw.get_playback_position()
                if pos.get("is_playing"):
                    t_play = time.time()
                    transport_start = float(pos.get("current_song_time", 0.0))
                    break
                time.sleep(0.02)
            if t_play is None:
                raise AudioCaptureError("SHORT_CAPTURE", "transport did not start")
        timings["pre_roll_s"] = time.perf_counter() - t_fire
        timings["pre_roll_breakdown"] = {
            "fire": fire_result.get("mode"),
            "wait_playing_polls": polls,
        }
        region_beats = (
            float(end_beat) - float(start_beat)
            if transport == "arrangement"
            else float(end_beat)
        )
        region_s = beats_to_seconds(region_beats, tempo)
        if transport == "arrangement":
            skip_qn = max(0.0, float(start_beat) - float(transport_start or 0.0))
            record_s = beats_to_seconds(skip_qn, tempo) + region_s
        else:
            skip_qn = 0.0
            record_s = region_s
        remaining = record_s - (time.time() - t_play)
        if remaining > 0:
            time.sleep(remaining)
        timings["record_s"] = record_s
        timings["region_s"] = region_s
        timings["pre_roll_qn"] = skip_qn if transport == "arrangement" else 0.0
        t_stop = time.perf_counter()
        transport_stop = float(daw.get_playback_position().get("current_song_time", 0.0))
        t_rec_stop = time.time()
        stop_bd: dict[str, Any] = {}
        t0 = time.perf_counter()
        rec_stop = set_taps_recording(
            daw,
            [{**rec, "force_rec": True} for rec in recorders],
            False,
            by_track=by_track,
        )
        stop_bd["rec_0"] = {
            "s": time.perf_counter() - t0,
            "mode": rec_stop.get("mode"),
        }
        stop_rtt = time.perf_counter() - t_stop
        t0 = time.perf_counter()
        daw.stop_playback()
        stop_bd["stop_playback"] = {"s": time.perf_counter() - t0}
        t0 = time.perf_counter()
        stop_post = _stop_session_clips(daw, fire_tracks)
        stop_bd["stop_clips"] = {
            "s": time.perf_counter() - t0,
            "mode": stop_post.get("mode"),
        }
        t0 = time.perf_counter()
        free2 = wait_until_wav_shared_readable(
            [staging_path(str(rec["staging"])) for rec in recorders],
            timeout_s=12.0,
        )
        stop_bd["staging_handle_release"] = {
            "s": time.perf_counter() - t0,
            **{k: free2.get(k) for k in ("ok", "waited_s")},
            "exclusive_required": False,
        }
        timings["recorder_stop_s"] = time.perf_counter() - t_stop
        timings["stop_command_rtt_s"] = stop_rtt
        timings["stop_breakdown"] = stop_bd
        t_fin = time.perf_counter()
        assets: dict[str, AudioAsset] = {}
        timings["staging_release"] = {}
        for rec in recorders:
            staging = staging_path(str(rec["staging"]))
            t_arm = armed_at.get(str(rec["key"]))
            ref = t_audio_start if t_audio_start else t_play
            if t_arm and ref:
                rec_offset = max(0.0, float(ref) - float(t_arm))
                lock_offset = True
            else:
                rec_offset = 0.0
                lock_offset = False
            if transport == "arrangement":
                skip_qn = max(0.0, float(start_beat) - float(transport_start or 0.0))
                rec_offset = rec_offset + beats_to_seconds(skip_qn, tempo)
                lock_offset = True
            trim_start = 0.0 if transport == "arrangement" else float(start_beat)
            trim_end = (
                float(end_beat) - float(start_beat)
                if transport == "arrangement"
                else float(end_beat)
            )
            asset = _finalize_staging(
                staging=staging,
                start_beat=trim_start,
                end_beat=trim_end,
                tempo=tempo,
                play_offset=rec_offset,
                live_sr=live_sr,
                require_signal=bool(rec.get("require_signal", True)),
                source=str(rec["source"]),
                source_type=str(rec["source_type"]),
                capture_view=rec["capture_view"],
                observation_source=rec["observation_source"],
                signal_point=rec["signal_point"],
                signal_point_label=rec.get("signal_point_label"),
                session_revision=session_revision,
                t_play=t_play,
                t_rec_stop=t_rec_stop,
                transport_start=transport_start,
                transport_stop=transport_stop,
                dest_path=reserved[str(rec["key"])],
                capture_id=f"{pass_id}_slot{rec.get('slot')}"
                if rec.get("slot") is not None
                else f"{pass_id}_{rec['key']}",
                pass_id=pass_id,
                slot=rec.get("slot"),
                routing=rec.get("routing"),
                returns_included=rec.get("returns_included"),
                lock_play_offset=lock_offset,
            )
            if transport == "arrangement":
                asset.requested_start_beat = float(start_beat)
                asset.requested_end_beat = float(end_beat)
                asset.start_beat = float(start_beat)
                asset.end_beat = float(end_beat)
                asset.request_start_beat = float(start_beat)
                asset.request_end_beat = float(end_beat)
                asset.capture_start_beat = float(start_beat)
                asset.capture_end_beat = float(end_beat)
                asset.analysis_start_beat = float(start_beat)
                asset.analysis_end_beat = float(end_beat)
                asset.requested_start_qn = float(start_beat)
                asset.requested_end_qn = float(end_beat)
                asset.transport_start_estimate_qn = float(transport_start)
                asset.capture_arm_time = None if t_arm is None else float(t_arm)
                asset.analysis_start_offset = float(rec_offset)
                asset.capture_file_start_time = None if t_arm is None else float(t_arm)
                asset.transport_target_qn = transport_target_qn(
                    float(start_beat), pre_roll_qn=PRE_ROLL_QN
                )
                asset.verified_transport_start_qn = float(transport_start)
                play_meta = (fire_result.get("result") or {}) if isinstance(fire_result, dict) else {}
                asset.verification_wall_time = (
                    None
                    if play_meta.get("t_tick_wall") is None
                    else float(play_meta.get("t_tick_wall"))
                )
                asset.transport_primitive_version = str(
                    play_meta.get("transport_primitive_version") or TRANSPORT_PRIMITIVE_VERSION
                )
                asset.first_poll_qn = (
                    None if play_meta.get("observed_qn") is None else float(play_meta["observed_qn"])
                )
                asset.stage_timings = {
                    **dict(asset.stage_timings or {}),
                    "t_play_command": play_meta.get("t_play_command"),
                    "t_play_return": play_meta.get("t_play_return"),
                    "t_first_poll": play_meta.get("t_first_poll"),
                    "expected_progress_qn": play_meta.get("expected_progress_qn"),
                    "poll_delay_s": play_meta.get("poll_delay_s"),
                    "transport_target_qn": asset.transport_target_qn,
                    "verified_transport_start_qn": asset.verified_transport_start_qn,
                    "pre_roll_qn": PRE_ROLL_QN,
                    "analysis_window": "requested_region",
                    "mapping": (
                        "analysis wav = raw[(t_play_command_wall-t_arm) "
                        "+ beats_to_seconds(requested_start-verified_start)]; "
                        "not first TCP poll"
                    ),
                }
            assets[str(rec["key"])] = asset
            timings["staging_release"][str(rec["key"])] = (
                "copied_while_locked" if staging.exists() else "moved"
            )
        timings["wav_finalize_s"] = time.perf_counter() - t_fin
        t_rest = time.perf_counter()
        restore = _restore_transport(daw, saved, fire_tracks[0] if fire_tracks else None)
        timings["restore_s"] = time.perf_counter() - t_rest
        timings["restore"] = restore
        timings["total_s"] = time.perf_counter() - t_all
        return {
            "assets": assets,
            "timings": timings,
            "pass_id": pass_id,
            "inventory": inventory,
            "stale_taps": stale,
            "restore": restore,
            "pre_state": saved,
        }
    finally:
        try:
            set_taps_recording(
                daw,
                [{**rec, "force_rec": True} for rec in recorders],
                False,
                by_track=by_track,
            )
        except (DawError, AudioCaptureError):
            for rec in recorders:
                try:
                    set_tap_recording(
                        daw,
                        False,
                        int(rec["tap_track_index"]),
                        broadcast_udp=False,
                        device_index=rec.get("device_index"),
                        rec_param_index=rec.get("rec_param_index"),
                    )
                except (DawError, AudioCaptureError):
                    pass


def assets_from_wav_card(
    card: dict[str, Any],
    *,
    start: float,
    end: float,
) -> dict[str, AudioAsset]:
    views: dict[str, AudioAsset] = {}
    mapping = {
        "master": (CaptureView.MASTER_CONTEXT, SignalPoint.MAIN_NOT_FINAL),
        "kick": (CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER_THROUGH_MASTER_CHAIN),
        "bass": (CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER_THROUGH_MASTER_CHAIN),
        "master_without_kick": (
            CaptureView.TRACK_CONTEXT_REMOVAL,
            SignalPoint.MAIN_NOT_FINAL,
        ),
        "master_without_bass": (
            CaptureView.TRACK_CONTEXT_REMOVAL,
            SignalPoint.MAIN_NOT_FINAL,
        ),
    }
    for key, info in (card.get("wavs") or {}).items():
        path = Path(str(info["path"]))
        if not path.is_file():
            continue
        wavinfo = sf.info(str(path))
        view, point = mapping.get(
            key, (CaptureView.MASTER_CONTEXT, SignalPoint.UNKNOWN)
        )
        if info.get("view") == "MASTER_CONTEXT":
            view = CaptureView.MASTER_CONTEXT
        views[key] = AudioAsset(
            capture_id=str(info.get("capture_id") or path.stem),
            raw_file_path=str(path),
            analysis_file_path=str(path),
            file_path=str(path),
            requested_start_beat=start,
            requested_end_beat=end,
            start_beat=start,
            end_beat=end,
            sample_rate=int(wavinfo.samplerate),
            channels=int(wavinfo.channels),
            raw_duration=float(wavinfo.duration),
            analysis_duration=float(wavinfo.duration),
            duration=float(wavinfo.duration),
            session_revision=1,
            capture_view=view,
            signal_point=point,
            analysis_start_beat=start,
            analysis_end_beat=end,
        )
    return views
