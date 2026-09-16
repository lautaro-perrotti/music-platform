from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from copilot.audio.live_capture import (
    SOURCE_TRACK_NAME,
    AudioCaptureError,
    assert_master_tap_final,
    capture_dir,
    ensure_internal_source,
    ensure_master_tap,
    master_tap_position,
    write_analysis_wav,
)
from copilot.audio.views import (
    capture_master_context,
    capture_track_in_mix_context,
    capture_track_isolated,
    find_off_main_tap,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.schemas.observation import CaptureView, SignalPoint
from copilot.schemas.session import MidiNote


def _log(msg: str) -> None:
    print(msg, flush=True)


def _brief(asset) -> dict[str, Any]:
    return {
        "capture_id": asset.capture_id,
        "view": asset.capture_view.value if asset.capture_view else None,
        "signal_point": asset.signal_point.value if asset.signal_point else None,
        "signal_point_label": asset.signal_point_label,
        "duration": asset.analysis_duration,
        "rms": asset.rms,
        "quality": asset.capture_quality,
        "align_transport": True,
    }


def _load_effect(daw: AbletonTcpAdapter, track_index: int, query: str) -> dict[str, Any]:
    info = daw.get_track_info(track_index) if track_index >= 0 else daw.get_master_info()
    for device in info.get("devices") or []:
        if query.lower() in str(device.get("name") or "").lower():
            return {"already_present": True, "device": device}
    found = daw.search_browser(query, "audio_effects")
    for item in found.get("results") or []:
        if item.get("is_loadable") and item.get("uri"):
            try:
                loaded = daw.load_browser_item(track_index, str(item["uri"]))
            except DawError:
                loaded = daw.load_instrument_or_effect(track_index, str(item["uri"]))
            if not loaded.get("error"):
                return {"loaded": loaded, "query": query}
    return {"error": f"could not load {query}"}


def run_live22e(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "phase": "LIVE-2.2e",
        "LIVE_CAPTURE_BASELINE": "VERIFIED",
        "PRODUCTION_CAPTURE_ENVELOPE": "PARTIAL",
        "MASTER CHAIN": "NOT_STARTED",
        "SIDECHAIN": "NOT_STARTED",
        "GROUP RETURN": "NOT_STARTED",
        "LATENCY": "NOT_STARTED",
        "SILENT BOUNDARY": "NOT_STARTED",
        "TRACK ISOLATED PATH": "NOT_STARTED",
    }

    def persist() -> None:
        (evidence / "live22e_five_fixtures.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    try:
        ensure_master_tap(daw)
        source = ensure_internal_source(daw)
        session = daw.snapshot(include_notes=False)
        drift = session.track_by_name(source["track_name"] or SOURCE_TRACK_NAME)
        if drift is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "LIVE2 Source missing")
        report["session_path"] = daw.get_session_path()
        report["off_main_tap"] = find_off_main_tap(daw)
        report["master_tap_before"] = master_tap_position(daw)
    except (AudioCaptureError, DawError) as exc:
        report["error"] = str(exc)
        persist()
        return report

    # 1. MASTER CHAIN
    try:
        _log("1 MASTER CHAIN")
        before = master_tap_position(daw)
        loaded = _load_effect(daw, -1, "Utility")
        after_load = master_tap_position(daw)
        tap_final_ok = True
        tap_error = None
        try:
            assert_master_tap_final(daw)
        except AudioCaptureError as exc:
            tap_final_ok = False
            tap_error = str(exc)
        isolated = None
        try:
            isolated = capture_track_isolated(daw, drift.stable_id, 0.0, 8.0)
        except AudioCaptureError as exc:
            report["isolated_during_chain"] = str(exc)
        through_master = (
            isolated is not None
            and isolated.signal_point is SignalPoint.TRACK_POST_MIXER_THROUGH_MASTER_CHAIN
        )
        off_main = (
            isolated is not None
            and isolated.signal_point is SignalPoint.TRACK_POST_MIXER
        )
        invariant_caught = (not tap_final_ok) and after_load.get("is_last") is False
        report["MASTER CHAIN"] = "PARTIAL" if invariant_caught else "FAILED"
        if invariant_caught and (off_main or through_master):
            report["MASTER CHAIN"] = "PARTIAL"
        report["master_chain"] = {
            "tap_before": before,
            "utility": loaded,
            "tap_after_load": after_load,
            "tap_final": tap_final_ok,
            "tap_final_error": tap_error,
            "isolated": _brief(isolated) if isolated else None,
            "isolated_skips_main": off_main,
            "isolated_through_main": through_master,
            "invariant_refused_non_final_tap": invariant_caught,
            "note": (
                "Loading a device after the tap makes MASTER_CONTEXT_FINAL illegal. "
                "That refusal is required. Isolated is TRACK_POST_MIXER only with "
                "an audio-track tap off Main; otherwise THROUGH_MASTER_CHAIN."
            ),
        }
        _log(
            f"  MASTER CHAIN {report['MASTER CHAIN']} "
            f"tap_final={tap_final_ok} isolated={getattr(isolated, 'signal_point', None)}"
        )
    except (AudioCaptureError, DawError) as exc:
        report["MASTER CHAIN"] = "FAILED"
        report["master_chain_error"] = str(exc)
        _log(f"  MASTER CHAIN FAILED {exc}")
    finally:
        pos = master_tap_position(daw)
        names = list(pos.get("devices") or [])
        if names and "tap" not in str(names[-1]).lower():
            try:
                daw._command(
                    "delete_device",
                    {"track_index": -1, "device_index": int(pos["last_index"])},
                    side_effect=True,
                )
                report.setdefault("master_chain", {})["utility_removed_after_test"] = names[-1]
            except DawError as exc:
                report.setdefault("master_chain", {})["utility_remove_error"] = str(exc)
    persist()

    # 2. SIDECHAIN — use existing tracks only, do not LOM-create the link
    try:
        _log("2 SIDECHAIN (existing tracks only)")
        session = daw.snapshot(include_notes=False)
        kick = session.track_by_name("LIVE22 Kick")
        bass = session.track_by_name("LIVE22 Bass")
        if kick is None or bass is None:
            report["SIDECHAIN"] = "PARTIAL"
            report["sidechain"] = {
                "status": "NEEDS_FIXTURE_ALS",
                "note": (
                    "Save fixtures/ableton/fixture_sidechain.als with Kick sidechained "
                    "into Bass Compressor. Do not invent the link via LOM."
                ),
            }
            _log("  SIDECHAIN PARTIAL needs fixture_sidechain.als")
        else:
            bass_info = daw.get_track_info(bass.index)
            devices = [d.get("name") for d in bass_info.get("devices") or []]
            full = capture_master_context(daw, 0.0, 8.0, require_tap_final=False)
            bass_iso = capture_track_isolated(daw, bass.stable_id, 0.0, 8.0)
            mix_kick = capture_track_in_mix_context(
                daw, kick.stable_id, 0.0, 8.0, require_tap_final=False
            )
            mix_bass = capture_track_in_mix_context(
                daw, bass.stable_id, 0.0, 8.0, require_tap_final=False
            )
            report["SIDECHAIN"] = "PARTIAL"
            report["sidechain"] = {
                "bass_devices": devices,
                "full": _brief(full),
                "bass_isolated": _brief(bass_iso),
                "master_without_kick": _brief(mix_kick["full_mix_target_muted"]),
                "master_without_bass": _brief(mix_bass["full_mix_target_muted"]),
                "note": (
                    "LOM did not configure sidechain. Observe these four views on a "
                    "hand-wired fixture before inferring pumping."
                ),
            }
            _log(f"  SIDECHAIN PARTIAL devices={devices}")
    except (AudioCaptureError, DawError) as exc:
        report["SIDECHAIN"] = "PARTIAL"
        report["sidechain_error"] = str(exc)
        _log(f"  SIDECHAIN PARTIAL {exc}")
    persist()

    # 3. GROUP + RETURN using existing bus / Send A
    try:
        _log("3 GROUP + RETURN")
        session = daw.snapshot(include_notes=False)
        src = session.track_by_name(SOURCE_TRACK_NAME)
        bus = session.track_by_name("LIVE22 Bus")
        if src is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "source missing")
        daw.set_send_level(src.index, 0, 0.85)
        child = capture_track_isolated(daw, src.stable_id, 0.0, 8.0)
        master = capture_master_context(
            daw, 0.0, 8.0, fire_track=src.index, fire_all=False, require_tap_final=False
        )
        bus_iso = None
        if bus is not None:
            try:
                bus_iso = capture_track_isolated(
                    daw, bus.stable_id, 0.0, 8.0, require_signal=False
                )
            except AudioCaptureError as exc:
                report["group_bus_error"] = str(exc)
        daw.set_send_level(src.index, 0, 0.0)
        report["GROUP RETURN"] = "PARTIAL"
        report["group_return"] = {
            "child_isolated": _brief(child),
            "master": _brief(master),
            "bus_isolated": _brief(bus_iso) if bus_iso else None,
            "note": (
                "Child isolated is track Post Mixer: no group/bus devices, no return wet. "
                "Wet and group FX live in MASTER_CONTEXT / bus isolated. "
                "Live Group track API is unsupported; use fixture_group.als for a real Group."
            ),
        }
        _log("  GROUP RETURN PARTIAL")
    except (AudioCaptureError, DawError) as exc:
        report["GROUP RETURN"] = "PARTIAL"
        report["group_return_error"] = str(exc)
        _log(f"  GROUP RETURN PARTIAL {exc}")
    persist()

    # 4. LATENCY / monitoring
    try:
        _log("4 LATENCY")
        session = daw.snapshot(include_notes=False)
        src = session.track_by_name(SOURCE_TRACK_NAME)
        delays = []
        for i in range(int(daw.health().get("track_count") or 0)):
            try:
                delays.append({"index": i, **daw.get_track_delay(i)})
            except DawError:
                continue
        pos = daw.get_playback_position()
        asset = capture_master_context(
            daw,
            0.0,
            8.0,
            fire_track=src.index if src else None,
            fire_all=False,
            require_tap_final=False,
        )
        data, sr = sf.read(asset.raw_file_path, always_2d=True)
        mono = np.mean(data, axis=1)
        hits = np.where(np.abs(mono) > 3e-3)[0]
        onset = int(hits[0]) if len(hits) else None
        report["LATENCY"] = "PARTIAL"
        report["latency"] = {
            "track_delays": delays[:12],
            "delay_compensation": "unreadable via LOM this session",
            "reduced_latency_when_monitoring": "unreadable via LOM this session",
            "transport_start": asset.transport_start_observed,
            "transport_stop": asset.transport_stop_observed,
            "raw_onset_samples": onset,
            "recorder_control_latency_s": None if onset is None else onset / float(sr),
            "note": (
                "Monitor In on the isolated host can ignore delay compensation when "
                "Reduced Latency When Monitoring is on. Read that Live preference "
                "manually until LOM exposes it."
            ),
        }
        _log("  LATENCY PARTIAL LOM cannot read RLWM")
    except (AudioCaptureError, DawError) as exc:
        report["LATENCY"] = "PARTIAL"
        report["latency_error"] = str(exc)
        _log(f"  LATENCY PARTIAL {exc}")
    persist()

    # 5. SILENT / SUSTAINED BOUNDARY
    try:
        _log("5 SILENT BOUNDARY")
        session = daw.snapshot(include_notes=False)
        src = session.track_by_name(SOURCE_TRACK_NAME)
        if src is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "source missing")
        saved_notes = [
            MidiNote(pitch=48, start_time=0.0, duration=8.0, velocity=110),
            MidiNote(pitch=84, start_time=8.0, duration=8.0, velocity=110),
        ]
        daw.replace_clip_notes(
            src.index,
            0,
            [MidiNote(pitch=60, start_time=8.0, duration=8.0, velocity=110)],
        )
        try:
            silent = capture_master_context(
                daw,
                0.0,
                8.0,
                fire_track=src.index,
                fire_all=False,
                require_signal=False,
                require_tap_final=False,
            )
            voiced = capture_master_context(
                daw,
                8.0,
                16.0,
                fire_track=src.index,
                fire_all=False,
                require_tap_final=False,
            )
            onset_path = capture_dir() / f"capture_{silent.capture_id}_onset_wrong.wav"
            write_analysis_wav(
                Path(silent.raw_file_path),
                onset_path,
                start_beat=0.0,
                end_beat=8.0,
                tempo=silent.tempo or 120.0,
                play_offset_seconds=0.0,
                align="onset",
            )
            onset_data, _ = sf.read(str(onset_path), always_2d=True)
            onset_rms = float(np.sqrt(np.mean(onset_data**2)))
            transport_is_quiet = (silent.rms or 0) < 0.01
            onset_is_loud = onset_rms > 0.01
            proven = transport_is_quiet and onset_is_loud
            report["SILENT BOUNDARY"] = "VERIFIED" if proven else "PARTIAL"
            report["silent_boundary"] = {
                "transport_0_8": _brief(silent),
                "transport_8_16": _brief(voiced),
                "onset_aligned_0_8_rms": onset_rms,
                "onset_would_steal_the_note": proven,
                "note": (
                    "Region 0-8 is silence; note starts at quarter_note_position 8. "
                    "Transport trim keeps the silence. Onset trim would start at the note."
                ),
            }
            _log(
                f"  SILENT BOUNDARY {report['SILENT BOUNDARY']} "
                f"transport_rms={silent.rms} onset_rms={onset_rms}"
            )
        finally:
            daw.replace_clip_notes(src.index, 0, saved_notes)
    except (AudioCaptureError, DawError) as exc:
        report["SILENT BOUNDARY"] = "FAILED"
        report["silent_boundary_error"] = str(exc)
        _log(f"  SILENT BOUNDARY FAILED {exc}")
        try:
            session = daw.snapshot(include_notes=False)
            src = session.track_by_name(SOURCE_TRACK_NAME)
            if src is not None:
                daw.replace_clip_notes(
                    src.index,
                    0,
                    [
                        MidiNote(pitch=48, start_time=0.0, duration=8.0, velocity=110),
                        MidiNote(pitch=84, start_time=8.0, duration=8.0, velocity=110),
                    ],
                )
        except DawError:
            pass
    persist()

    off = find_off_main_tap(daw)
    report["TRACK ISOLATED PATH"] = (
        "VERIFIED" if off else "PARTIAL"
    )
    report["TRACK ISOLATED PATH note"] = (
        f"off-Main tap on {off}" if off else
        "only Master tap; isolated is TRACK_POST_MIXER_THROUGH_MASTER_CHAIN"
    )
    persist()
    _log("LIVE-2.2e done")
    for key in (
        "MASTER CHAIN",
        "SIDECHAIN",
        "GROUP RETURN",
        "LATENCY",
        "SILENT BOUNDARY",
        "TRACK ISOLATED PATH",
        "LIVE_CAPTURE_BASELINE",
        "PRODUCTION_CAPTURE_ENVELOPE",
    ):
        _log(f"  {key:<28} {report.get(key)}")
    return report
