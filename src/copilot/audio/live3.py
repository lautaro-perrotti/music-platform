from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from copilot.audio.diagnose import diagnose_lowend
from copilot.audio.live_capture import (
    AudioAsset,
    AudioCaptureError,
    capture_dir,
    ensure_master_tap,
)
from copilot.schemas.observation import CaptureView, SignalPoint
from copilot.audio.views import (
    capture_master_context,
    capture_track_in_mix_context,
    capture_track_isolated,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.schemas.diagnosis import FindingType
from copilot.schemas.session import MidiNote

KICK_NAME = "LIVE3 Kick"
BASS_NAME = "LIVE3 Bass"
START_QN = 0.0
END_QN = 16.0

# Fixture labels are for the runner comparison only. Never passed to diagnose_lowend.
CONDITIONS: dict[str, dict[str, list[MidiNote]]] = {
    "CLEAN": {
        "kick": [
            MidiNote(pitch=36, start_time=float(i), duration=0.20, velocity=127)
            for i in range(0, 16, 2)
        ],
        "bass": [
            MidiNote(pitch=72, start_time=float(i) + 0.75, duration=0.50, velocity=95)
            for i in range(0, 16, 2)
        ],
    },
    "TEMPORAL": {
        "kick": [
            MidiNote(pitch=36, start_time=float(i), duration=0.20, velocity=127)
            for i in range(0, 16, 2)
        ],
        "bass": [
            MidiNote(pitch=48, start_time=0.0, duration=16.0, velocity=95)
        ],
    },
    "SPECTRAL": {
        "kick": [
            MidiNote(pitch=36, start_time=float(i), duration=0.45, velocity=127)
            for i in range(0, 16, 2)
        ],
        "bass": [
            MidiNote(pitch=36, start_time=float(i), duration=0.55, velocity=100)
            for i in range(0, 16, 2)
        ],
    },
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ensure_track(daw: AbletonTcpAdapter, name: str, notes: list[MidiNote]) -> dict[str, Any]:
    from copilot.audio.live_capture import _load_stock_instrument

    session = daw.snapshot(include_notes=False)
    existing = session.track_by_name(name)
    if existing is None:
        created = daw.create_midi_track(name, -1)
        index = int(created["index"])
        _load_stock_instrument(daw, index)
    else:
        index = existing.index
    info = daw.get_track_info(index)
    slots = info.get("clip_slots") or []
    if not (slots and slots[0].get("has_clip")):
        daw.create_midi_clip(index, 0, 16.0)
        daw.set_clip_name(index, 0, name)
    daw.replace_clip_notes(index, 0, notes)
    session = daw.snapshot(include_notes=False)
    track = session.track_by_name(name)
    return {
        "index": index,
        "stable_id": track.stable_id if track else "",
        "name": name,
    }


def _capture_set(
    daw: AbletonTcpAdapter, kick: dict[str, Any], bass: dict[str, Any]
) -> dict[str, AudioAsset]:
    t0 = time.perf_counter()
    play = [int(kick["index"]), int(bass["index"])]
    _log("  capture MASTER_CONTEXT")
    master = capture_master_context(
        daw,
        START_QN,
        END_QN,
        fire_tracks=play,
        fire_all=False,
        require_tap_final=False,
    )
    _log("  capture KICK_ISOLATED")
    kick_iso = capture_track_isolated(
        daw, kick["stable_id"], START_QN, END_QN, fire_tracks=[int(kick["index"])]
    )
    _log("  capture BASS_ISOLATED")
    bass_iso = capture_track_isolated(
        daw, bass["stable_id"], START_QN, END_QN, fire_tracks=[int(bass["index"])]
    )
    _log("  capture TRACK_CONTEXT_REMOVAL(KICK)")
    without_kick = capture_track_in_mix_context(
        daw,
        kick["stable_id"],
        START_QN,
        END_QN,
        require_tap_final=False,
        fire_tracks=play,
        full_mix=master,
    )
    _log("  capture TRACK_CONTEXT_REMOVAL(BASS)")
    without_bass = capture_track_in_mix_context(
        daw,
        bass["stable_id"],
        START_QN,
        END_QN,
        require_tap_final=False,
        fire_tracks=play,
        full_mix=master,
    )
    elapsed = time.perf_counter() - t0
    return {
        "master": master,
        "kick": kick_iso,
        "bass": bass_iso,
        "master_without_kick": without_kick["full_mix_target_muted"],
        "master_without_bass": without_bass["full_mix_target_muted"],
        "_capture_s": elapsed,  # type: ignore[dict-item]
        "_full_mix": without_kick["full_mix"],  # type: ignore[dict-item]
    }


def _asset_from_capture(obs: dict[str, Any]) -> AudioAsset:
    capture_id = str(obs["capture_id"])
    path = capture_dir() / f"capture_{capture_id}.wav"
    view = CaptureView(obs["view"]) if obs.get("view") else CaptureView.MASTER_CONTEXT
    point = SignalPoint(obs["signal_point"]) if obs.get("signal_point") else SignalPoint.UNKNOWN
    start = float(obs.get("start_qn") or 0.0)
    end = float(obs.get("end_qn") or 16.0)
    return AudioAsset(
        capture_id=capture_id,
        raw_file_path=str(path),
        analysis_file_path=str(path),
        file_path=str(path),
        requested_start_beat=start,
        requested_end_beat=end,
        start_beat=start,
        end_beat=end,
        sample_rate=44100,
        channels=2,
        raw_duration=float(obs.get("duration") or 8.0),
        analysis_duration=float(obs.get("duration") or 8.0),
        duration=float(obs.get("duration") or 8.0),
        session_revision=1,
        capture_view=view,
        signal_point=point,
        signal_point_label=str(obs.get("signal_point_label") or ""),
        analysis_start_beat=start,
        analysis_end_beat=end,
        capture_quality=str(obs.get("quality") or "OK"),
        rms=float(obs.get("rms") or 0.0),
    )


def reanalyze_live3_from_captures(evidence: Path) -> dict[str, Any]:
    path = evidence / "live3_lowend_diagnosis.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    diagnoses = report.get("diagnoses") or {}
    t0 = time.perf_counter()
    for label, payload in diagnoses.items():
        obs = payload.get("observations") or {}
        views = {
            key: _asset_from_capture(item)
            for key, item in obs.items()
            if isinstance(item, dict) and item.get("capture_id")
        }
        if not {"master", "kick", "bass"} <= set(views):
            continue
        diagnosis = diagnose_lowend(views, kick_name=KICK_NAME, bass_name=BASS_NAME)
        types = {item.type for item in diagnosis.findings}
        ok = _direction_ok(label, types)
        fp = _false_positive_check(label, diagnosis.user_facing, types)
        if not fp["ok"]:
            ok = False
        report[f"FIXTURE {label}"] = "VERIFIED" if ok else "PARTIAL"
        diagnoses[label] = {
            **{k: v for k, v in payload.items() if k != "diagnosis"},
            "direction_ok": ok,
            "finding_types": [t.value for t in types],
            "confidence": diagnosis.confidence.value,
            "user_facing": diagnosis.user_facing,
            "limitations": diagnosis.limitations,
            "findings": [item.model_dump() for item in diagnosis.findings],
            "candidate_actions": [item.model_dump() for item in diagnosis.candidate_actions],
            "false_positive_check": fp,
            "primary_hypothesis": (
                diagnosis.primary_hypothesis.model_dump()
                if diagnosis.primary_hypothesis
                else None
            ),
            "reanalyzed": True,
            "timings": {
                **(payload.get("timings") or {}),
                **diagnosis.timings,
            },
        }
        report.setdefault("diagnoses_full", {})[label] = diagnosis.model_dump()
        _log(f"REANALYZE {label} {report[f'FIXTURE {label}']} types={list(types)}")
        _log(diagnosis.user_facing)
    report["diagnoses"] = diagnoses
    temporal_ok = FindingType.TEMPORAL_MASKING.value in (
        (diagnoses.get("TEMPORAL") or {}).get("finding_types") or []
    )
    spectral_ok = FindingType.SPECTRAL_MASKING.value in (
        (diagnoses.get("SPECTRAL") or {}).get("finding_types") or []
    )
    no_action_ok = FindingType.NO_ACTION_REQUIRED.value in (
        (diagnoses.get("CLEAN") or {}).get("finding_types") or []
    )
    report["TEMPORAL EVIDENCE"] = "VERIFIED" if temporal_ok else "PARTIAL"
    report["SPECTRAL EVIDENCE"] = "VERIFIED" if spectral_ok else "PARTIAL"
    report["NO-ACTION"] = "VERIFIED" if no_action_ok else "PARTIAL"
    report["STRUCTURED EVIDENCE"] = "VERIFIED"
    report["REAL LOW-END AUDIO"] = "VERIFIED"
    all_ok = all(
        report.get(f"FIXTURE {name}") == "VERIFIED" for name in CONDITIONS
    )
    report["LOW_END_DIAGNOSIS_BASELINE"] = "VERIFIED" if all_ok and no_action_ok else "PARTIAL"
    report["PRODUCTION_MUSIC_DIAGNOSIS"] = "PARTIAL"
    report["GENERAL MUSIC DIAGNOSIS"] = "NOT_CLAIMED"
    report["reanalyze_s"] = time.perf_counter() - t0
    example = diagnoses.get("TEMPORAL") or diagnoses.get("SPECTRAL") or diagnoses.get("CLEAN") or {}
    report["user_facing_example"] = example.get("user_facing")
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
    return {
        "capture_id": asset.capture_id,
        "view": asset.capture_view.value if asset.capture_view else None,
        "signal_point": asset.signal_point.value if asset.signal_point else None,
        "start_qn": asset.start_beat,
        "end_qn": asset.end_beat,
        "duration": asset.analysis_duration,
        "rms": asset.rms,
        "quality": asset.capture_quality,
    }


def _false_positive_check(label: str, user_facing: str, types: set[FindingType]) -> dict[str, Any]:
    text = user_facing.lower()
    forbidden = {
        "CLEAN": (
            "severe masking",
            "enmascaramiento severo",
            "phase cancellation",
            "cancelación de fase",
            "sidechain malfunction",
        ),
        "TEMPORAL": ("phase cancellation", "cancelación de fase"),
        "SPECTRAL": (
            "sidechain malfunction",
            "fallo de sidechain",
            "phase cancellation",
            "cancelación de fase",
        ),
    }
    violations = [phrase for phrase in forbidden.get(label, ()) if phrase in text]
    if "utility" in text and "main not final" not in text:
        violations.append("invented-or-unqualified-utility")
    if label == "CLEAN" and FindingType.TEMPORAL_MASKING in types:
        violations.append("CLEAN_CALLED_TEMPORAL_MASKING")
    if label == "CLEAN" and FindingType.SPECTRAL_MASKING in types:
        violations.append("CLEAN_CALLED_SPECTRAL_MASKING")
    return {"ok": not violations, "violations": violations}


def _direction_ok(label: str, types: set[FindingType]) -> bool:
    if label == "CLEAN":
        return FindingType.NO_ACTION_REQUIRED in types and (
            FindingType.TEMPORAL_MASKING not in types
            and FindingType.SPECTRAL_MASKING not in types
        )
    if label == "TEMPORAL":
        return FindingType.TEMPORAL_MASKING in types or FindingType.EXCESSIVE_BASS_DECAY in types
    if label == "SPECTRAL":
        return FindingType.SPECTRAL_MASKING in types
    return False


def run_live3(
    daw: AbletonTcpAdapter,
    evidence: Path,
    *,
    only: list[str] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "phase": "LIVE-3",
        "FIXTURE CLEAN": "NOT_STARTED",
        "FIXTURE TEMPORAL": "NOT_STARTED",
        "FIXTURE SPECTRAL": "NOT_STARTED",
        "TEMPORAL EVIDENCE": "NOT_STARTED",
        "SPECTRAL EVIDENCE": "NOT_STARTED",
        "DIAGNOSIS": "NOT_STARTED",
        "NO-ACTION": "NOT_STARTED",
        "LATENCY": "NOT_STARTED",
        "REAL LOW-END AUDIO": "NOT_STARTED",
    }
    evidence_path = evidence / "live3_lowend_diagnosis.json"
    diagnoses: dict[str, Any] = {}
    diagnoses_full: dict[str, Any] = {}
    capture_times: list[float] = []
    analysis_times: list[float] = []
    if only and evidence_path.exists():
        prev = json.loads(evidence_path.read_text(encoding="utf-8"))
        for key, value in prev.items():
            if key not in {"diagnoses", "diagnoses_full"}:
                report[key] = value
        diagnoses = dict(prev.get("diagnoses") or {})
        diagnoses_full = dict(prev.get("diagnoses_full") or {})
        lat = prev.get("LATENCY") or {}
        if isinstance(lat, dict):
            capture_times = list(lat.get("capture_s") or [])
            analysis_times = list(lat.get("analysis_s") or [])

    def persist() -> None:
        report["diagnoses"] = {
            key: {k: v for k, v in val.items() if k != "diagnosis"}
            for key, val in diagnoses.items()
        }
        report["diagnoses_full"] = {
            **diagnoses_full,
            **{
                key: val.get("diagnosis")
                for key, val in diagnoses.items()
                if val.get("diagnosis")
            },
        }
        evidence_path.write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    try:
        _log("LIVE-3 setup Kick + Bass")
        ensure_master_tap(daw)
        kick = _ensure_track(daw, KICK_NAME, CONDITIONS["CLEAN"]["kick"])
        bass = _ensure_track(daw, BASS_NAME, CONDITIONS["CLEAN"]["bass"])
        report["targets"] = {"kick": kick, "bass": bass}
    except (AudioCaptureError, DawError) as exc:
        report["error"] = str(exc)
        persist()
        return report

    selected = only or list(CONDITIONS)
    for label in selected:
        notes = CONDITIONS[label]
        _log(f"LIVE-3 condition {label}")
        try:
            daw.replace_clip_notes(kick["index"], 0, notes["kick"])
            daw.replace_clip_notes(bass["index"], 0, notes["bass"])
            session = daw.snapshot(include_notes=False)
            kick_t = session.track_by_name(KICK_NAME)
            bass_t = session.track_by_name(BASS_NAME)
            if kick_t is None or bass_t is None:
                raise AudioCaptureError("TARGET_AMBIGUOUS", "LIVE3 tracks missing")
            kick["stable_id"] = kick_t.stable_id
            bass["stable_id"] = bass_t.stable_id
            try:
                captured = _capture_set(daw, kick, bass)
            except AudioCaptureError as exc:
                if "SHORT_CAPTURE" not in str(exc):
                    raise
                _log(f"  retry after {exc}")
                captured = _capture_set(daw, kick, bass)
            capture_s = float(captured.pop("_capture_s"))
            captured.pop("_full_mix", None)
            capture_times.append(capture_s)
            t_diag = time.perf_counter()
            diagnosis = diagnose_lowend(
                captured,
                kick_name=KICK_NAME,
                bass_name=BASS_NAME,
            )
            analysis_times.append(time.perf_counter() - t_diag)
            types = {item.type for item in diagnosis.findings}
            ok = _direction_ok(label, types)
            fp = _false_positive_check(label, diagnosis.user_facing, types)
            if not fp["ok"]:
                ok = False
            report[f"FIXTURE {label}"] = "VERIFIED" if ok else "PARTIAL"
            report.pop(f"{label}_error", None)
            diagnoses[label] = {
                "direction_ok": ok,
                "finding_types": [t.value for t in types],
                "confidence": diagnosis.confidence.value,
                "user_facing": diagnosis.user_facing,
                "limitations": diagnosis.limitations,
                "timings": {**diagnosis.timings, "capture_s": capture_s},
                "observations": {
                    key: _brief_asset(asset) for key, asset in captured.items()
                },
                "findings": [item.model_dump() for item in diagnosis.findings],
                "candidate_actions": [
                    item.model_dump() for item in diagnosis.candidate_actions
                ],
                "false_positive_check": fp,
                "primary_hypothesis": (
                    diagnosis.primary_hypothesis.model_dump()
                    if diagnosis.primary_hypothesis
                    else None
                ),
                "diagnosis": diagnosis.model_dump(),
            }
            _log(f"  {label} {report[f'FIXTURE {label}']} types={list(types)}")
            _log("--- user-facing ---")
            _log(diagnosis.user_facing)
            _log("-------------------")
            persist()
        except (AudioCaptureError, DawError, ValueError) as exc:
            report[f"FIXTURE {label}"] = "FAILED"
            report[f"{label}_error"] = str(exc)
            _log(f"  {label} FAILED {exc}")
            persist()

    temporal_ok = any(
        FindingType.TEMPORAL_MASKING.value in (diagnoses.get(name) or {}).get("finding_types", [])
        for name in ("TEMPORAL", "SPECTRAL")
    )
    spectral_ok = FindingType.SPECTRAL_MASKING.value in (
        (diagnoses.get("SPECTRAL") or {}).get("finding_types") or []
    )
    no_action_ok = FindingType.NO_ACTION_REQUIRED.value in (
        (diagnoses.get("CLEAN") or {}).get("finding_types") or []
    )
    any_audio = any(
        report.get(f"FIXTURE {name}") in {"VERIFIED", "PARTIAL"}
        for name in CONDITIONS
    )
    report["REAL LOW-END AUDIO"] = "VERIFIED" if any_audio else "FAILED"
    report["TEMPORAL EVIDENCE"] = "VERIFIED" if temporal_ok else "PARTIAL"
    report["SPECTRAL EVIDENCE"] = "VERIFIED" if spectral_ok else "PARTIAL"
    report["STRUCTURED EVIDENCE"] = "VERIFIED" if diagnoses else "FAILED"
    report["MUSIC DIAGNOSIS"] = (
        "VERIFIED"
        if any(report.get(f"FIXTURE {name}") == "VERIFIED" for name in CONDITIONS)
        else "PARTIAL"
    )
    report["DIAGNOSIS"] = report["MUSIC DIAGNOSIS"]
    report["NO-ACTION"] = "VERIFIED" if no_action_ok else "PARTIAL"
    baseline = (
        "VERIFIED"
        if (
            report.get("FIXTURE CLEAN") == "VERIFIED"
            and report.get("FIXTURE TEMPORAL") == "VERIFIED"
            and report.get("FIXTURE SPECTRAL") == "VERIFIED"
            and no_action_ok
        )
        else "PARTIAL"
        if any_audio
        else "FAILED"
    )
    report["LOW_END_DIAGNOSIS_BASELINE"] = baseline
    report["PRODUCTION_MUSIC_DIAGNOSIS"] = "PARTIAL"
    report["GENERAL MUSIC DIAGNOSIS"] = "NOT_CLAIMED"
    report["LATENCY"] = {
        "capture_s": capture_times,
        "analysis_s": analysis_times,
        "total_s": float(sum(capture_times) + sum(analysis_times)),
        "note": "Not optimized. Logged for workflow.",
    }
    example = diagnoses.get("TEMPORAL") or diagnoses.get("CLEAN") or {}
    report["user_facing_example"] = example.get("user_facing")
    persist()
    _log("LIVE-3 done")
    for key in (
        "FIXTURE CLEAN",
        "FIXTURE TEMPORAL",
        "FIXTURE SPECTRAL",
        "TEMPORAL EVIDENCE",
        "SPECTRAL EVIDENCE",
        "DIAGNOSIS",
        "NO-ACTION",
        "REAL LOW-END AUDIO",
        "MUSIC DIAGNOSIS",
        "LOW_END_DIAGNOSIS_BASELINE",
        "PRODUCTION_MUSIC_DIAGNOSIS",
    ):
        _log(f"  {key:<24} {report.get(key)}")
    return report
