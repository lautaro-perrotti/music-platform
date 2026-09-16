"""SOURCE AUDIO TRACE V1 — exact Post Mixer audio for MIDI-active tracks during Main gap.

Reuses TapProtocol 3 / arrangement capture / route_host_post_mixer + restore.
Temporary capture-host routing only. Audible mix unchanged. MUSICAL_WRITES = 0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import math
import shutil

import numpy as np
import soundfile as sf

from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    capture_parallel_pass,
    restore_host_routing,
    route_host_post_mixer,
    _sends_already_silent,
)
from copilot.audio.live_capture import (
    MASTER_INDEX,
    SILENCE_PEAK,
    SILENCE_RMS,
    STAGING_BASS,
    AudioCaptureError,
    find_tap,
)
from copilot.audio.live3r_trust import _recorders
from copilot.audio.session_diagnose import preflight_session
from copilot.audio.tap_trust import (
    FAILED,
    FINALIZING,
    PREPARED,
    RECORDING,
    VERIFIED,
    CaptureJournal,
    inventory_taps,
    routing_claim,
    sha256_file,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.human_eval.store import now_iso
from copilot.schemas.observation import SignalPoint

NEAR_SILENCE_RMS = 1.0e-3
SOURCE_TRACKS = ("Strum", "Transform Seed", "Rainstorm")
REGION_ID = "REGION_C"
REGION_START_QN = 32.0
REGION_END_QN = 64.0
HOST_NAME = CAPTURE_BASS
HOST_SLOT = 2
TRACE_VERSION = "source-audio-trace-1"


def _mono(samples: np.ndarray) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim == 1:
        return data
    if data.shape[0] <= 8 and data.shape[0] < data.shape[1]:
        return np.mean(data, axis=0)
    return np.mean(data, axis=1)


def _db(num: float, den: float) -> float:
    n = max(float(num), 1e-12)
    d = max(float(den), 1e-12)
    return 20.0 * math.log10(n / d)


def _signal_class(rms: float, peak: float) -> str:
    if rms <= SILENCE_RMS and peak <= SILENCE_PEAK:
        return "SILENCE"
    if rms <= NEAR_SILENCE_RMS:
        return "NEAR_SILENCE"
    return "HAS_SIGNAL"


def _slice_stats(mono: np.ndarray, sample_rate: int, start_s: float, end_s: float) -> dict[str, float]:
    start_i = max(0, int(round(start_s * sample_rate)))
    end_i = min(len(mono), int(round(end_s * sample_rate)))
    if end_i <= start_i:
        return {"rms": 0.0, "peak": 0.0, "samples": 0.0}
    window = mono[start_i:end_i]
    rms = float(np.sqrt(np.mean(window * window))) if len(window) else 0.0
    peak = float(np.max(np.abs(window))) if len(window) else 0.0
    return {"rms": rms, "peak": peak, "samples": float(len(window))}


def analyze_source_event(
    wav_path: Path,
    *,
    event_audio_start_s: float,
    event_audio_end_s: float,
    context_pad_s: float = 0.5,
) -> dict[str, Any]:
    samples, sample_rate = sf.read(str(wav_path), always_2d=True)
    mono = _mono(samples)
    duration_s = float(len(mono) / float(sample_rate)) if sample_rate else 0.0
    event = _slice_stats(mono, sample_rate, event_audio_start_s, event_audio_end_s)
    before = _slice_stats(
        mono,
        sample_rate,
        max(0.0, event_audio_start_s - context_pad_s),
        event_audio_start_s,
    )
    after = _slice_stats(
        mono,
        sample_rate,
        event_audio_end_s,
        min(duration_s, event_audio_end_s + context_pad_s),
    )
    # Local reference = louder of before/after context (or whole-file RMS if both empty)
    ref_candidates = [before["rms"], after["rms"]]
    local_ref = max(ref_candidates) if max(ref_candidates) > 0 else float(
        np.sqrt(np.mean(mono * mono)) if len(mono) else 0.0
    )
    relative_drop_db = _db(event["rms"], local_ref) if local_ref > 0 else 0.0
    klass = _signal_class(event["rms"], event["peak"])
    return {
        "wav_path": str(wav_path),
        "audio_sha256": sha256_file(wav_path),
        "sample_rate": int(sample_rate),
        "duration_s": duration_s,
        "event_audio_start_s": float(event_audio_start_s),
        "event_audio_end_s": float(event_audio_end_s),
        "event_rms": event["rms"],
        "event_peak": event["peak"],
        "before_rms": before["rms"],
        "before_peak": before["peak"],
        "after_rms": after["rms"],
        "after_peak": after["peak"],
        "local_reference_rms": local_ref,
        "relative_drop_db": relative_drop_db,
        "signal_class": klass,
        "has_signal": klass == "HAS_SIGNAL",
    }


def _snapshot_host(daw: AbletonTcpAdapter, host_index: int) -> dict[str, Any]:
    info = daw.get_track_info(host_index)
    try:
        monitoring = daw.get_track_monitoring(host_index)
    except DawError:
        monitoring = {"monitoring": info.get("monitoring")}
    try:
        sends = daw.get_track_sends(host_index)
    except DawError:
        sends = list(info.get("sends") or [])
    return {
        "input": daw.get_track_input_routing(host_index),
        "output": daw.get_track_output_routing(host_index),
        "monitoring": monitoring,
        "sends": sends,
        "info": info,
    }


def _restore_host_full(
    daw: AbletonTcpAdapter,
    host_index: int,
    before: dict[str, Any],
) -> dict[str, Any]:
    errors = restore_host_routing(daw, host_index, before)
    try:
        mon = str((before.get("monitoring") or {}).get("monitoring") or (before.get("monitoring") or {}).get("value") or "")
        if mon:
            daw.set_track_monitoring(host_index, mon)
    except DawError:
        errors.append("host monitoring unrestored")
    # Restore send levels if we zeroed them.
    for row in before.get("sends") or []:
        try:
            idx = int(row.get("send_index", 0))
            level = float(row.get("value") if row.get("value") is not None else row.get("level") or 0.0)
            daw.set_send_level(host_index, idx, level)
        except Exception:
            errors.append(f"send:{row.get('send_index')} unrestored")
    after = _snapshot_host(daw, host_index)
    prev_in = str((before.get("input") or {}).get("input_routing_type") or "")
    now_in = str((after.get("input") or {}).get("input_routing_type") or "")
    prev_ch = str((before.get("input") or {}).get("input_routing_channel") or "")
    now_ch = str((after.get("input") or {}).get("input_routing_channel") or "")
    prev_out = str((before.get("output") or {}).get("output_routing_type") or "")
    now_out = str((after.get("output") or {}).get("output_routing_type") or "")
    ok = prev_in == now_in and prev_ch == now_ch and prev_out == now_out and not errors
    return {
        "ok": ok,
        "errors": errors,
        "before_input": before.get("input"),
        "after_input": after.get("input"),
        "before_output": before.get("output"),
        "after_output": after.get("output"),
    }


def _silence_sends(daw: AbletonTcpAdapter, host_index: int) -> list[str]:
    mutations: list[str] = []
    sends = daw.get_track_sends(host_index)
    if sends and not _sends_already_silent(sends):
        for row in sends:
            daw.set_send_level(host_index, int(row.get("send_index", 0)), 0.0)
            mutations.append(f"send:{row.get('send_index')}->0")
    return mutations


def _verify_off_mix_graph(daw: AbletonTcpAdapter, host_index: int, target_name: str) -> dict[str, Any]:
    info = daw.get_track_info(host_index)
    sends = list(info.get("sends") or []) or daw.get_track_sends(host_index)
    claim = routing_claim(
        input_type=str(info.get("input_routing_type") or ""),
        input_channel=str(info.get("input_routing_channel") or ""),
        output=str(info.get("output_routing_type") or ""),
        monitoring=str(info.get("monitoring") or ""),
        through_main=str(info.get("output_routing_type") or "").lower() in {"main", "master"},
        sends=sends,
        target_name=target_name,
    )
    return claim


def _load_primary_event(evidence: Path, source_run: str) -> dict[str, Any]:
    trace_path = evidence / f"{source_run}_causal_trace_region_c.json"
    if not trace_path.is_file():
        raise FileNotFoundError(f"causal trace missing: {trace_path}")
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    trace = payload.get("trace") or {}
    primary_id = trace.get("primary_gap_event_id")
    primary = next(
        (e for e in (trace.get("events") or []) if e.get("event_id") == primary_id),
        None,
    )
    if primary is None:
        raise ValueError("primary gap event missing from causal trace")
    midi = {
        m.get("track"): m
        for m in (trace.get("midi_coverage") or [])
        if m.get("track") in SOURCE_TRACKS
    }
    return {
        "primary": primary,
        "midi_by_track": midi,
        "project_token": trace.get("project_token") or payload.get("trace", {}).get("project_token"),
        "audible_token": trace.get("audible_token"),
        "causal_trace_artifact": str(trace_path),
    }


def _build_recorders(
    daw: AbletonTcpAdapter,
    *,
    host_index: int,
    target_name: str,
    input_channel: str,
) -> list[dict[str, Any]]:
    inventory = inventory_taps(daw)
    by_track = {int(row["track_index"]): row for row in inventory}
    main_tap = by_track.get(MASTER_INDEX) or {}
    host_tap = by_track.get(host_index) or {}
    if host_tap.get("device_index") is None:
        tap = find_tap(daw, host_index)
        if tap is None:
            raise AudioCaptureError("TAP_MISSING", f"{HOST_NAME} missing Copilot Audio Tap")
        host_device = int(tap["index"])
    else:
        host_device = int(host_tap["device_index"])
    main_device = main_tap.get("device_index")
    if main_device is None:
        mt = find_tap(daw, MASTER_INDEX)
        if mt is None:
            raise AudioCaptureError("TAP_MISSING", "Master missing Copilot Audio Tap")
        main_device = int(mt["index"])
    # Reuse production recorder template shape; only master + bass host.
    dummy = _recorders(
        kick_index=host_index,
        bass_index=host_index,
        kick_id=target_name,
        bass_id=target_name,
        kick_channel=input_channel,
        bass_channel=input_channel,
        main_point=SignalPoint.MAIN_FINAL,
    )
    master = dummy[0]
    master["device_index"] = int(main_device)
    master["rec_param_index"] = main_tap.get("rec_param_index")
    master["require_signal"] = True
    source = dummy[2]
    source["key"] = "source"
    source["tap_track_index"] = host_index
    source["staging"] = STAGING_BASS
    source["slot"] = HOST_SLOT
    source["source"] = target_name
    source["signal_point_label"] = f"{target_name} → {input_channel}"
    source["device_index"] = host_device
    source["rec_param_index"] = host_tap.get("rec_param_index")
    source["require_signal"] = False  # silence is a valid diagnostic outcome
    return [master, source]


def _midi_audio_relation(midi_active_count: int, signal_class: str) -> str:
    if midi_active_count <= 0:
        return "MIDI_INACTIVE"
    if signal_class == "HAS_SIGNAL":
        return "MIDI_ACTIVE_AUDIO_ACTIVE"
    if signal_class == "NEAR_SILENCE":
        return "MIDI_ACTIVE_AUDIO_NEAR_SILENT"
    return "MIDI_ACTIVE_AUDIO_SILENT"


def _derive_case(observations: list[dict[str, Any]]) -> dict[str, Any]:
    classes = {row["track"]: row["signal_class"] for row in observations}
    silentish = {"SILENCE", "NEAR_SILENCE"}
    all_quiet = all(c in silentish for c in classes.values()) and bool(classes)
    any_loud = any(c == "HAS_SIGNAL" for c in classes.values())
    main_classes = {row["track"]: row.get("main_event_signal_class") for row in observations}
    main_quiet = all(c in silentish for c in main_classes.values() if c)

    if all_quiet:
        return {
            "case": "A",
            "code": "ACTIVE_MIDI_SOURCES_QUIET",
            "detail": (
                "All MIDI-active target sources are SILENCE/NEAR_SILENCE during the Main gap."
            ),
        }
    if any_loud and main_quiet:
        return {
            "case": "B",
            "code": "SOURCE_AUDIO_MAIN_MISMATCH",
            "detail": (
                "One or more MIDI-active sources have HAS_SIGNAL during the event while Main remains quiet."
            ),
        }
    # Unique attenuation pattern vs Main
    candidates = []
    for row in observations:
        if row["signal_class"] in silentish and abs(float(row.get("relative_drop_db") or 0.0)) >= 12.0:
            candidates.append(row["track"])
    if len(candidates) == 1 and any_loud is False:
        return {
            "case": "C",
            "code": "SINGLE_SOURCE_ATTENUATION_CANDIDATE",
            "detail": f"{candidates[0]} uniquely shows strong attenuation during the event.",
            "candidate_track": candidates[0],
        }
    return {
        "case": "UNRESOLVED",
        "code": "SOURCE_AUDIO_PATTERN_UNRESOLVED",
        "detail": "Source audio states do not uniquely explain the Main gap.",
    }


def run_source_audio_trace(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    evidence.mkdir(parents=True, exist_ok=True)
    meta = _load_primary_event(evidence, source_run)
    primary = meta["primary"]
    event_start_s = float(primary["event_audio_start_s"])
    event_end_s = float(primary["event_audio_end_s"])
    event_start_qn = float(primary["event_start_qn"])
    event_end_qn = float(primary["event_end_qn"])
    event_id = str(primary["event_id"])

    preflight = preflight_session(
        daw,
        lab_track_exclusions=frozenset({"AI Test"}),
    )
    if not preflight.get("pass") or not (preflight.get("capture_hosts") or {}).get(CAPTURE_BASS):
        return {
            "status": "PRE-FLIGHT STOP",
            "preflight": {
                "pass": preflight.get("pass"),
                "missing": preflight.get("missing"),
                "lab_track_exclusions": preflight.get("lab_track_exclusions"),
                "lab_hits_excluded": preflight.get("lab_hits_excluded"),
                "live_set_path": preflight.get("live_set_path"),
            },
            "MUSICAL WRITES": 0,
            "MUSICPLAN_GATE": "CLOSED",
        }

    # Path identifies the working-copy file only; it does not override token mismatch.
    saved_token = meta.get("project_token")
    live_token = preflight.get("project_token")
    live_path = str(preflight.get("live_set_path") or "")
    saved_path = ""
    try:
        run_report = json.loads((evidence / f"{source_run}.json").read_text(encoding="utf-8"))
        saved_path = str(
            (run_report.get("PRE-FLIGHT") or {}).get("live_set_path")
            or (run_report.get("ARRANGEMENT ACTIVITY") or {}).get("als_path")
            or ""
        )
    except Exception:
        saved_path = ""

    def _base(path: str) -> str:
        return Path(path).name.lower() if path else ""

    if saved_path and live_path and _base(saved_path) != _base(live_path):
        return {
            "status": "PROJECT_STATE_CONFLICT",
            "reason": "live_set_path_basename_mismatch",
            "saved_project_token": saved_token,
            "live_project_token": live_token,
            "saved_path": saved_path,
            "live_path": live_path,
            "MUSICAL WRITES": 0,
            "MUSICPLAN_GATE": "CLOSED",
        }
    identity = {
        "status": "PATH_MATCH" if _base(saved_path) == _base(live_path) else "LIVE_ONLY",
        "saved_project_token": saved_token,
        "live_project_token": live_token,
        "token_drift": bool(saved_token and live_token and saved_token != live_token),
        "path_does_not_override_token_mismatch": True,
        "saved_path": saved_path,
        "live_path": live_path,
        "lab_track_exclusions": preflight.get("lab_track_exclusions"),
        "note": (
            "Source audio uses LIVE_STATE capture. CausalTrace MIDI remains SAVED_PROJECT. "
            "Path match identifies the working copy file only; token drift remains recorded."
        ),
    }

    hosts = preflight["capture_hosts"]
    bass_host = hosts[CAPTURE_BASS]
    host_index = int(bass_host["index"])
    pos = daw.get_playback_position()
    tempo = float(pos.get("tempo") or preflight.get("tempo") or 167.0)

    dest_root = Path(r"D:\MusicCopilot\captures")
    if not dest_root.is_dir():
        dest_root = Path("captures")
    dest_root.mkdir(parents=True, exist_ok=True)

    passes: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    capture_routing_mutations = 0
    audible_mix_mutations = 0

    production_before = _snapshot_host(daw, host_index)

    try:
        for track_name in SOURCE_TRACKS:
            pass_id = uuid4().hex[:12]
            journal = CaptureJournal(pass_id)
            journal.record(
                PREPARED,
                region=REGION_ID,
                start_beat=REGION_START_QN,
                end_beat=REGION_END_QN,
                target=track_name,
                host=HOST_NAME,
                mode="SOURCE_AUDIO_TRACE",
            )
            before = _snapshot_host(daw, host_index)
            routed = None
            restore_report = None
            try:
                routed = route_host_post_mixer(daw, host_index, track_name)
                capture_routing_mutations += 1
                send_muts = _silence_sends(daw, host_index)
                capture_routing_mutations += len(send_muts)
                claim = _verify_off_mix_graph(daw, host_index, track_name)
                if claim.get("claim") not in {"OFF_MIX_GRAPH", "OFF_DIRECT_MAIN"}:
                    raise AudioCaptureError(
                        "CAPTURE_ROUTING_UNSUPPORTED",
                        f"{track_name} host claim={claim.get('claim')} detail={claim}",
                    )
                if claim.get("through_main"):
                    raise AudioCaptureError(
                        "AUDIBLE_MIX_RISK",
                        f"{HOST_NAME} still routes through Main while tapping {track_name}",
                    )
                input_channel = str(routed.get("input_channel") or "")
                recorders = _build_recorders(
                    daw,
                    host_index=host_index,
                    target_name=track_name,
                    input_channel=input_channel,
                )
                journal.record(RECORDING, claim=claim.get("claim"), channel=input_channel)
                one = capture_parallel_pass(
                    daw,
                    start_beat=REGION_START_QN,
                    end_beat=REGION_END_QN,
                    fire_tracks=[],
                    tempo=tempo,
                    session_revision=int(preflight.get("revision") or 0),
                    pass_id=pass_id,
                    recorders=recorders,
                    transport="arrangement",
                )
                journal.record(FINALIZING)
                assets = one.get("assets") or {}
                source_asset = assets.get("source")
                main_asset = assets.get("master")
                if source_asset is None or main_asset is None:
                    raise AudioCaptureError(
                        "CAPTURE_MISSING_ASSET",
                        f"pass {track_name} missing source/main asset keys={list(assets)}",
                    )
                # Prefer analysis (region-trimmed) paths — t=0 == region start.
                source_wav = Path(
                    str(
                        getattr(source_asset, "analysis_file_path", None)
                        or source_asset.file_path
                    )
                )
                main_wav = Path(
                    str(
                        getattr(main_asset, "analysis_file_path", None)
                        or main_asset.file_path
                    )
                )
                # Persist durable copies
                src_dest = dest_root / f"source_trace_{REGION_ID}_{track_name.replace(' ', '_')}_{pass_id}.wav"
                main_dest = dest_root / f"source_trace_{REGION_ID}_Main_with_{track_name.replace(' ', '_')}_{pass_id}.wav"
                shutil.copy2(source_wav, src_dest)
                shutil.copy2(main_wav, main_dest)

                source_stats = analyze_source_event(
                    src_dest,
                    event_audio_start_s=event_start_s,
                    event_audio_end_s=event_end_s,
                )
                main_stats = analyze_source_event(
                    main_dest,
                    event_audio_start_s=event_start_s,
                    event_audio_end_s=event_end_s,
                )
                midi_row = (meta["midi_by_track"] or {}).get(track_name) or {}
                midi_active = len(midi_row.get("notes_active_at_event") or [])
                relation = _midi_audio_relation(midi_active, source_stats["signal_class"])
                obs = {
                    "observation_type": "SourceAudioObservation",
                    "track": track_name,
                    "event_id": event_id,
                    "event_qn_range": [event_start_qn, event_end_qn],
                    "event_audio_range_s": [event_start_s, event_end_s],
                    "signal_point": "Post Mixer",
                    "host": HOST_NAME,
                    "host_slot": HOST_SLOT,
                    "audio_asset_hash": source_stats["audio_sha256"],
                    "wav_path": str(src_dest),
                    "main_wav_path": str(main_dest),
                    "main_audio_asset_hash": main_stats["audio_sha256"],
                    "event_rms": source_stats["event_rms"],
                    "event_peak": source_stats["event_peak"],
                    "local_reference_rms": source_stats["local_reference_rms"],
                    "relative_drop_db": source_stats["relative_drop_db"],
                    "before_rms": source_stats["before_rms"],
                    "after_rms": source_stats["after_rms"],
                    "signal_class": source_stats["signal_class"],
                    "main_event_rms": main_stats["event_rms"],
                    "main_event_peak": main_stats["event_peak"],
                    "main_event_signal_class": main_stats["signal_class"],
                    "midi_active_count": midi_active,
                    "midi_audio_relation": relation,
                    "capture_provenance": {
                        "pass_id": pass_id,
                        "transport": "arrangement",
                        "requested_start_qn": REGION_START_QN,
                        "requested_end_qn": REGION_END_QN,
                        "tempo_bpm": tempo,
                        "claim": claim.get("claim"),
                        "input_type": routed.get("input_type"),
                        "input_channel": input_channel,
                        "output": routed.get("output"),
                        "through_main": routed.get("through_main"),
                        "analysis_start_offset": getattr(source_asset, "analysis_start_offset", None),
                        "transport_start_estimate_qn": getattr(
                            source_asset, "transport_start_estimate_qn", None
                        ),
                        "mapping": (
                            "analysis WAV t=0 corresponds to requested region start_qn; "
                            "event times reuse CausalTrace/FullMix audio mapping."
                        ),
                    },
                    "project_token": preflight.get("project_token"),
                    "audible_token": preflight.get("audible_token"),
                    "source_label": "LIVE_STATE+CAPTURE",
                    "limitations": [
                        "Post Mixer observation via temporary OFF_MIX_GRAPH capture host.",
                        "Does not identify device/macro cause of silence.",
                        "MIDI counts come from SAVED_PROJECT CausalTrace; audio from LIVE capture.",
                    ],
                }
                observations.append(obs)
                journal.record(
                    VERIFIED,
                    track=track_name,
                    signal_class=source_stats["signal_class"],
                    relation=relation,
                )
                passes.append(
                    {
                        "track": track_name,
                        "pass_id": pass_id,
                        "claim": claim.get("claim"),
                        "routed": {
                            "input_type": routed.get("input_type"),
                            "input_channel": input_channel,
                            "output": routed.get("output"),
                            "through_main": routed.get("through_main"),
                        },
                        "send_mutations": send_muts,
                        "status": "VERIFIED",
                    }
                )
            except Exception as exc:
                journal.record(FAILED, error=f"{type(exc).__name__}:{exc}")
                passes.append(
                    {
                        "track": track_name,
                        "pass_id": pass_id,
                        "status": "FAILED",
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                )
                raise
            finally:
                restore_report = _restore_host_full(daw, host_index, before)
                passes[-1]["restore"] = restore_report
                if not restore_report.get("ok"):
                    raise AudioCaptureError(
                        "ROUTING_RESTORE_FAILED",
                        f"failed to restore {HOST_NAME} after {track_name}: {restore_report}",
                    )
    finally:
        # Ensure production bass target restored even if a later pass failed mid-way.
        final_restore = _restore_host_full(daw, host_index, production_before)
        # If already restored per-pass, this should be a no-op / still correct.

    decision = _derive_case(observations)
    payload = {
        "status": "SOURCE AUDIO TRACE COMPLETE",
        "trace_version": TRACE_VERSION,
        "source_run": source_run,
        "region_id": REGION_ID,
        "gathered_at": now_iso(),
        "event_id": event_id,
        "event_qn_range": [event_start_qn, event_end_qn],
        "event_audio_range_s": [event_start_s, event_end_s],
        "project_token": preflight.get("project_token"),
        "audible_token": preflight.get("audible_token"),
        "live_set_path": preflight.get("live_set_path"),
        "identity_reconciliation": identity,
        "lab_track_warnings": preflight.get("lab_track_warnings"),
        "host": HOST_NAME,
        "host_slot": HOST_SLOT,
        "targets": list(SOURCE_TRACKS),
        "passes": passes,
        "observations": observations,
        "decision": decision,
        "final_host_restore": final_restore,
        "capture_routing_mutations": capture_routing_mutations,
        "AUDIBLE_MIX_MUTATIONS": audible_mix_mutations,
        "MUSICAL WRITES": 0,
        "ASTRA CALLS": 0,
        "MusicPlan": None,
        "NO LIVE-4": True,
        "causal_trace_artifact": meta.get("causal_trace_artifact"),
        "limitations": [
            "Temporary capture-host Audio From changes only; host remains Sends Only / off Main.",
            "Automation/device cause not investigated in this packet unless Case C uniquely nominates a track.",
        ],
    }
    out = evidence / f"{source_run}_source_audio_trace_{REGION_ID.lower()}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload
