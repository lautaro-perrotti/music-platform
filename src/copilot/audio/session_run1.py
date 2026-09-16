from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import shutil
import time

from copilot.audio.arrangement_seek import (
    PLAYBACK_START_TOLERANCE_QN,
    PRE_ROLL_QN,
    REGION_1_END_QN,
    REGION_1_START_QN,
    TRANSPORT_PRIMITIVE_VERSION,
    transport_target_qn,
)
from copilot.audio.arrangement_activity import inspect_arrangement_activity
from copilot.audio.batch_capture import CAPTURE_BASS, CAPTURE_HOST, capture_parallel_pass
from copilot.audio.capture_capability import CORE_CAPTURE_VERSION, CaptureMode
from copilot.audio.live3r_perf3 import _asset_card
from copilot.audio.live3r_trust import _recorders
from copilot.audio.live_capture import (
    EXPECTED_TAP_PROTOCOL,
    SILENCE_PEAK,
    SILENCE_RMS,
    AudioCaptureError,
    capture_dir,
    observe_asset,
)
from copilot.audio.lowend_features import ANALYZER_ID, analyzer_fingerprint, compute_lowend_features
from copilot.audio.session_diagnose import (
    BASS_TARGET,
    KICK_PAD,
    WORKING_COPY_CANDIDATE,
    human_label_template,
    preflight_session,
    write_preflight,
)
from copilot.audio.tap_trust import (
    FAILED,
    FINALIZING,
    PREPARED,
    RECORDING,
    VERIFIED,
    CaptureJournal,
    sha256_file,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.write import WriteInDoubt
from copilot.reasoning.from_dsp import pack_from_lowend_features
from copilot.schemas.observation import SignalPoint

REAL_REGIONS = (
    {
        "id": "REGION_A",
        "start_qn": 256.0,
        "end_qn": 288.0,
        "expected": {
            "main": "signal",
            "kick": "signal",
            "sub_sub_bass": "signal",
            "rose_bass": "active_not_isolated",
        },
    },
    {
        "id": "REGION_B",
        "start_qn": 96.0,
        "end_qn": 128.0,
        "expected": {
            "main": "signal",
            "kick": "signal",
            "sub_sub_bass": "signal",
            "rose_bass": "inactive_not_isolated",
        },
    },
    {
        "id": "REGION_C",
        "start_qn": 32.0,
        "end_qn": 64.0,
        "expected": {
            "main": "signal",
            "kick": "signal",
            "sub_sub_bass": "inactive",
            "rose_bass": "inactive_not_isolated",
        },
    },
)


def select_arrangement_regions(
    *,
    loop_start: float,
    loop_length: float,
    signature_numerator: int = 4,
) -> list[dict[str, Any]]:
    origin = float(loop_start or 0.0)
    length = float(loop_length) if loop_length and loop_length > 0 else 432.0
    bar = float(signature_numerator or 4)
    window = bar * 8.0
    if length < window * 3:
        window = bar * 4.0

    def snap(beat: float) -> float:
        return max(origin, (beat // bar) * bar)

    first = origin + (window if length >= window * 5 else 0.0)
    candidates = [
        snap(first),
        snap(origin + length * 0.40),
        snap(origin + length * 0.68),
    ]
    regions: list[dict[str, Any]] = []
    used_end = origin
    for i, start in enumerate(candidates, start=1):
        start = max(start, used_end)
        end = start + window
        if end > origin + length:
            start = max(origin, origin + length - window)
            end = start + window
        regions.append(
            {
                "id": f"REGION_{i}",
                "start_qn": float(start),
                "end_qn": float(end),
                "bars": int(round(window / bar)),
            }
        )
        used_end = end
    return regions


def _jsonable(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _observation_card(obs) -> dict[str, Any]:
    return {
        "source": obs.source,
        "region": obs.region,
        "signal": obs.signal.model_dump(),
        "claims": [claim.model_dump() for claim in obs.claims],
        "tempo_bpm": obs.tempo_bpm,
        "capture_view": obs.capture_view,
        "signal_point": obs.signal_point,
        "signal_point_label": obs.signal_point_label,
        "limitations": obs.limitations,
        "project_token": obs.project_token,
        "audible_token": obs.audible_token,
        "quality": obs.quality,
    }


def run_session_run1(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    t_all = time.perf_counter()
    evidence.mkdir(parents=True, exist_ok=True)
    preflight = preflight_session(daw)
    write_preflight(preflight, evidence)
    report: dict[str, Any] = {
        "phase": "REAL SESSION DIAGNOSIS — RUN 1",
        "PRE-FLIGHT": {
            "status": preflight.get("status"),
            "pass": preflight.get("pass"),
            "missing": preflight.get("missing"),
            "live_set_path": preflight.get("live_set_path"),
            "project_token": preflight.get("project_token"),
            "audible_token": preflight.get("audible_token"),
            "revision": preflight.get("revision"),
            "kick_source_class": preflight.get("kick_source_class"),
            "bass_source_class": preflight.get("bass_source_class"),
            "main": preflight.get("main"),
            "taps": preflight.get("taps"),
            "routing_mutations": 0,
        },
        "NO LIVE-4": True,
        "NO ASTRA": True,
        "NO MUSICPLAN": True,
        "NO ROUTING WRITES": True,
        "mode": CaptureMode.PRODUCTION.value,
        "NOT_CERTIFICATION_SUITE": True,
        "core_capture_version": CORE_CAPTURE_VERSION,
        "analyzer_id": ANALYZER_ID,
        "analyzer_sha256": analyzer_fingerprint(),
        "ASTRA CALLS": 0,
        "MUSICAL WRITES": 0,
        "model_calls": 0,
    }
    if not preflight.get("pass"):
        report["status"] = "PRE-FLIGHT STOP"
        report["instruction"] = "STOP. Do not capture. Do not call Astra."
        _write(report, evidence)
        return report

    report["TRANSPORT START PROVENANCE"] = {
        "TRANSPORT_START_PROVENANCE": "VERIFIED",
        "recertified_this_run": False,
        "transport_primitive_version": TRANSPORT_PRIMITIVE_VERSION,
        "pre_roll_qn": PRE_ROLL_QN,
        "prior_errors_qn": {"32": -0.024, "172": 0.022, "292": -0.016},
        "note": "Do not reopen transport certification. Capture uses start_playback_at_qn.",
    }

    pos = daw.get_playback_position()
    regions = [dict(row) for row in REAL_REGIONS]
    report["REGIONS SELECTED"] = {
        "loop_start": pos.get("loop_start"),
        "loop_length": pos.get("loop_length"),
        "tempo": pos.get("tempo"),
        "regions": regions,
        "labels": "none — no CLEAN/TEMPORAL/SPECTRAL/GOOD/BAD",
        "selection": "read-only mapped REGION_A/B/C",
        "rose_bass_isolated": False,
        "slot2": "Sub Sub Bass Post Mixer, not all bass",
    }
    als_path = Path(str(preflight.get("live_set_path") or preflight.get("project_path") or ""))
    if als_path.is_dir():
        candidate = als_path / "pista_copilot_eval.als"
        als_path = candidate if candidate.is_file() else Path(WORKING_COPY_CANDIDATE)
    elif not als_path.is_file():
        als_path = Path(WORKING_COPY_CANDIDATE)
    try:
        if not als_path.is_file():
            raise FileNotFoundError(f"working copy not readable: {als_path}")
        activity = inspect_arrangement_activity(als_path, regions)
    except Exception as exc:
        activity = {
            "ok": False,
            "error": str(exc),
            "als_path": str(als_path),
            "regions": [],
        }
    report["ARRANGEMENT ACTIVITY"] = activity
    activity_by_id = {row["id"]: row for row in activity.get("regions") or []}
    _write(report, evidence)

    recs = _production_recorders(preflight)
    recs[1]["require_signal"] = False
    recs[2]["require_signal"] = False

    captures: list[dict[str, Any]] = []
    t_cap = time.perf_counter()
    try:
        for region in regions:
            pass_id = uuid4().hex[:12]
            journal = CaptureJournal(pass_id)
            journal.record(
                PREPARED,
                region=region["id"],
                start_beat=region["start_qn"],
                end_beat=region["end_qn"],
                revision=preflight.get("revision"),
                mode=CaptureMode.PRODUCTION.value,
                live_set_path=preflight.get("live_set_path"),
                kick_source=KICK_PAD,
                bass_source=BASS_TARGET,
                tap_protocol=EXPECTED_TAP_PROTOCOL,
            )
            journal.record(RECORDING)
            one = capture_parallel_pass(
                daw,
                start_beat=float(region["start_qn"]),
                end_beat=float(region["end_qn"]),
                fire_tracks=[],
                tempo=float(pos.get("tempo") or preflight.get("tempo") or 120.0),
                session_revision=int(preflight.get("revision") or 0),
                pass_id=pass_id,
                recorders=deepcopy(recs),
                transport="arrangement",
            )
            timings = one.get("timings") or {}
            journal.record(FINALIZING, timings={"total_s": timings.get("total_s")})
            assets = one["assets"]
            hashes = {
                key: sha256_file(Path(asset.file_path)) for key, asset in assets.items()
            }
            restore = one.get("restore") or {}
            signals = {
                key: {
                    "class": _signal_class(asset.rms, asset.peak),
                    "rms": asset.rms,
                    "peak": asset.peak,
                    "duration": asset.duration,
                    "path": asset.file_path,
                }
                for key, asset in assets.items()
            }
            region_activity = activity_by_id.get(region["id"]) or {}
            source_class = {
                "drums": _presence_class(
                    signals["kick"]["class"],
                    (region_activity.get("drums") or {}).get("class"),
                ),
                "sub_sub_bass": _presence_class(
                    signals["bass"]["class"],
                    (region_activity.get("sub_sub_bass") or {}).get("class"),
                ),
                "rose_bass_arrangement": (region_activity.get("rose_bass") or {}).get("class"),
                "rose_bass_isolated": False,
            }
            master = assets["master"]
            target_qn = transport_target_qn(float(region["start_qn"]), pre_roll_qn=PRE_ROLL_QN)
            implied = float(
                master.verified_transport_start_qn
                if master.verified_transport_start_qn is not None
                else master.transport_start_estimate_qn
                if master.transport_start_estimate_qn is not None
                else master.transport_start_observed
                or -1.0
            )
            start_ok = abs(implied - float(target_qn)) <= PLAYBACK_START_TOLERANCE_QN
            mapping = (master.stage_timings or {}).get("analysis_window")
            unique = len({asset.file_path for asset in assets.values()}) == 3
            main_ok = signals["master"]["class"] == "HAS_SIGNAL"
            mismatch = _capture_source_mismatch(signals, region.get("expected") or {})
            provenance_ok = (
                start_ok
                and unique
                and bool(restore.get("ok"))
                and main_ok
                and mapping == "requested_region"
                and mismatch is None
            )
            if provenance_ok:
                journal.record(
                    VERIFIED,
                    hashes=hashes,
                    implied_start_qn=implied,
                    transport_target_qn=target_qn,
                    first_qn=master.first_poll_qn,
                    source_class=source_class,
                )
                claim = VERIFIED
            else:
                journal.record(
                    FAILED,
                    implied_start_qn=implied,
                    transport_target_qn=target_qn,
                    restore=restore,
                    mapping=mapping,
                    mismatch=mismatch,
                )
                claim = FAILED
            card = {
                "region": region,
                "pass_id": pass_id,
                "journal": str(journal.path),
                "status": claim,
                "restore_ok": restore.get("ok"),
                "wavs": {key: asset.file_path for key, asset in assets.items()},
                "hashes": hashes,
                "cards": {key: _asset_card(asset) for key, asset in assets.items()},
                "signals": signals,
                "source_class": source_class,
                "activity": region_activity,
                "transport_start_provenance": timings.get("transport_start_provenance"),
                "transport_observed": {
                    key: {
                        "requested_start_qn": asset.requested_start_qn,
                        "requested_end_qn": asset.requested_end_qn,
                        "transport_start_estimate_qn": asset.transport_start_estimate_qn,
                        "first_poll_qn": asset.first_poll_qn,
                        "capture_arm_time": asset.capture_arm_time,
                        "analysis_start_offset": asset.analysis_start_offset,
                        "analysis_duration": asset.analysis_duration,
                        "tempo": asset.tempo,
                        "stop": asset.transport_stop_observed,
                    }
                    for key, asset in assets.items()
                },
                "sample_rate": assets["master"].sample_rate,
                "tap_protocol": EXPECTED_TAP_PROTOCOL,
                "elapsed_s": timings.get("total_s"),
                "transport_target_qn": target_qn,
                "implied_start_qn": implied,
                "source_mismatch": mismatch,
                "assets": assets,
            }
            captures.append(card)
            if claim != VERIFIED:
                if mismatch:
                    report["status"] = "CAPTURE_SOURCE_MISMATCH"
                elif not start_ok:
                    report["status"] = "ARRANGEMENT_PLAYBACK_START_MISMATCH"
                else:
                    report["status"] = "CAPTURE FAILED"
                report["CAPTURE ASSETS"] = [_public_capture(item) for item in captures]
                report["instruction"] = "STOP / fail closed. Do not call Astra."
                report["ASTRA CALLS"] = 0
                report["MUSICAL WRITES"] = 0
                report["MusicDiagnosis"] = None
                report["MusicPlan"] = None
                _write(report, evidence)
                return report
    except WriteInDoubt as exc:
        report["status"] = "IN_DOUBT"
        report["error"] = str(exc)
        report["CAPTURE ASSETS"] = [_public_capture(item) for item in captures]
        report["instruction"] = "STOP / fail closed. Do not call Astra."
        report["ASTRA CALLS"] = 0
        report["MUSICAL WRITES"] = 0
        _write(report, evidence)
        return report
    except AudioCaptureError as exc:
        report["status"] = "CAPTURE FAILED"
        report["error"] = str(exc)
        report["CAPTURE ASSETS"] = [_public_capture(item) for item in captures]
        report["instruction"] = "STOP / fail closed. Do not call Astra."
        _write(report, evidence)
        return report

    capture_s = time.perf_counter() - t_cap
    listen = _write_listen_mains(captures)
    report["CAPTURE ASSETS"] = [_public_capture(item) for item in captures]
    report["HUMAN LISTEN MAIN"] = listen
    report["CAPTURE PROVENANCE"] = {
        "core_capture_version": CORE_CAPTURE_VERSION,
        "tap_protocol": EXPECTED_TAP_PROTOCOL,
        "transport": "arrangement",
        "routing_mutations": 0,
        "journals": [item["journal"] for item in captures],
    }
    report["STATE TOKENS"] = {
        "project_token": preflight.get("project_token"),
        "audible_token": preflight.get("audible_token"),
        "revision": preflight.get("revision"),
    }

    dsp_rows = []
    for item in captures:
        assets = item["assets"]
        region = item["region"]
        region_label = f"{region['start_qn']:g}->{region['end_qn']:g}qn"
        features = compute_lowend_features(assets)
        if not features.get("ok"):
            dsp_rows.append(
                {
                    "region": region,
                    "analyzer_id": ANALYZER_ID,
                    "error": features.get("missing"),
                    "ok": False,
                }
            )
            continue
        pack = pack_from_lowend_features(
            region_id=region["id"],
            region=region_label,
            features=features,
            views=assets,
            project_token=str(preflight.get("project_token") or ""),
            audible_token=str(preflight.get("audible_token") or ""),
            target_token=None,
            kick_name=KICK_PAD,
            bass_name=BASS_TARGET,
        )
        observations = {
            key: observe_asset(asset, region_label, float(asset.tempo or pos.get("tempo") or 0.0))
            for key, asset in assets.items()
        }
        for obs in observations.values():
            obs.project_token = preflight.get("project_token")
            obs.audible_token = preflight.get("audible_token")
            obs.limitations = [
                "ALIGNMENT_LIMITED ±52 ms",
                "DSP_PERSIST_IS_ENERGY",
                "FACTUAL_ONLY",
            ]
            if item.get("source_class", {}).get("sub_sub_bass") == "SOURCE_INACTIVE":
                obs.limitations.append("SOURCE_INACTIVE: Sub Sub Bass")
            if item.get("source_class", {}).get("drums") == "SOURCE_INACTIVE":
                obs.limitations.append("SOURCE_INACTIVE: Drums")
        dsp_rows.append(
            {
                "region": region,
                "analyzer_id": ANALYZER_ID,
                "analyzer_sha256": analyzer_fingerprint(),
                "features": _jsonable(
                    {
                        "ok": features.get("ok"),
                        "kick_event_source": features.get("kick_event_source"),
                        "duration_s": features.get("duration_s"),
                        "attacks": {
                            "count": (features.get("attacks") or {}).get("count"),
                            "raw_count": (features.get("attacks") or {}).get("raw_count"),
                            "source": (features.get("attacks") or {}).get("source"),
                        },
                        "temporal": features.get("temporal"),
                        "spectral": {
                            "events_with_co_concentration": (
                                features.get("spectral") or {}
                            ).get("events_with_co_concentration"),
                            "dominant_attack_band": (features.get("spectral") or {}).get(
                                "dominant_attack_band"
                            ),
                            "band_mean_joint": (features.get("spectral") or {}).get(
                                "band_mean_joint"
                            ),
                        },
                        "kick_rms": features.get("kick_rms"),
                        "bass_rms": features.get("bass_rms"),
                        "master_rms": features.get("master_rms"),
                        "kick_f0": features.get("kick_f0"),
                        "bass_f0": features.get("bass_f0"),
                    }
                ),
                "evidence_pack": pack.model_dump(mode="json"),
                "music_observation": {
                    key: _observation_card(obs) for key, obs in observations.items()
                },
            }
        )

    report["DSP OBSERVATIONS"] = dsp_rows
    report["LIMITATIONS"] = [
        "ALIGNMENT_LIMITED ±52 ms",
        "microtiming 5-10 ms is not supported",
        "DSP persist is normalized energy in a 50-200 ms window, not decay duration",
        "Kick source is Drum Rack pad Kick 808 Deep Post Mixer, not whole Drums",
        "Bass source is Sub Sub Bass Post Mixer, not all bass in the song",
        "MIDI unread except Arrangement clip presence from .als",
        "device params unread except Copilot taps",
        "SOURCE_INACTIVE is arrangement presence, not a capture-path failure",
    ]
    report["ANALYZER HASH"] = analyzer_fingerprint()
    report["TOTAL CAPTURE TIME"] = capture_s
    report["TOTAL RUN TIME"] = time.perf_counter() - t_all
    report["human_label_template"] = human_label_template(
        [region["id"] for region in regions]
    )
    report["HUMAN LISTEN"] = [
        {
            "region": item["region"]["id"],
            "start_qn": item["region"]["start_qn"],
            "end_qn": item["region"]["end_qn"],
            "main_listen_wav": next(
                (
                    row["path"]
                    for row in listen
                    if row["region"] == item["region"]["id"]
                ),
                item["wavs"].get("master"),
            ),
            "wavs": item["wavs"],
            "source_class": item["source_class"],
        }
        for item in captures
    ]
    report["ASTRA CALLS"] = 0
    report["MUSICAL WRITES"] = 0
    report["MusicDiagnosis"] = None
    report["MusicPlan"] = None
    report["CandidateActions"] = None
    report["status"] = "RUN 1 COMPLETE — STOP BEFORE ASTRA"
    report["instruction"] = (
        "Listen to HUMAN LISTEN MAIN paths and fill human_label_template. Do not call Astra yet."
    )
    _write(report, evidence)
    return report


def _public_capture(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "assets"}


def _write(report: dict[str, Any], evidence: Path) -> Path:
    path = evidence / "session_run1.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def _signal_class(rms: float | None, peak: float | None) -> str:
    rms_v = float(rms or 0.0)
    peak_v = float(peak or 0.0)
    if rms_v <= SILENCE_RMS and peak_v <= SILENCE_PEAK:
        return "RECORDED_SILENCE"
    return "HAS_SIGNAL"


def _presence_class(signal_class: str, activity_class: str | None) -> str:
    if signal_class == "HAS_SIGNAL":
        return "HAS_SIGNAL"
    if activity_class == "SOURCE_INACTIVE":
        return "SOURCE_INACTIVE"
    return signal_class


def _capture_source_mismatch(
    signals: dict[str, dict[str, Any]], expected: dict[str, Any]
) -> str | None:
    """Expected-active isolated sources must not be digital silence."""
    if expected.get("main") == "signal" and signals["master"]["class"] != "HAS_SIGNAL":
        return "Main expected signal, got digital silence"
    if expected.get("kick") == "signal" and signals["kick"]["class"] != "HAS_SIGNAL":
        return "Kick 808 Deep expected active, got digital silence"
    if expected.get("sub_sub_bass") == "signal" and signals["bass"]["class"] != "HAS_SIGNAL":
        return "Sub Sub Bass expected active, got digital silence"
    return None


def _write_listen_mains(captures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dest_root = capture_dir()
    dest_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in captures:
        region_id = str(item["region"]["id"])
        src = Path(item["wavs"]["master"])
        dest = dest_root / f"listen_{region_id}_main.wav"
        shutil.copy2(src, dest)
        rows.append(
            {
                "region": region_id,
                "start_qn": item["region"]["start_qn"],
                "end_qn": item["region"]["end_qn"],
                "path": str(dest),
            }
        )
    return rows


def _production_recorders(preflight: dict[str, Any]) -> list[dict[str, Any]]:
    hosts = preflight["capture_hosts"]
    kick_host = hosts[CAPTURE_HOST]
    bass_host = hosts[CAPTURE_BASS]
    drums = preflight["drums"]
    bass = preflight["bass"]
    recs = _recorders(
        kick_index=int(kick_host["index"]),
        bass_index=int(bass_host["index"]),
        kick_id=str(drums["stable_id"]),
        bass_id=str(bass["stable_id"]),
        kick_channel=str(kick_host["routing"]["input_channel"]),
        bass_channel=str(bass_host["routing"]["input_channel"]),
        main_point=SignalPoint.MAIN_FINAL,
    )
    recs[1]["signal_point_label"] = f"Drums → {kick_host['routing']['input_channel']}"
    recs[2]["signal_point_label"] = f"{BASS_TARGET} → {bass_host['routing']['input_channel']}"
    recs[0]["device_index"] = preflight["main"]["tap"]["device_index"]
    recs[0]["rec_param_index"] = preflight["main"]["tap"].get("rec_param_index")
    recs[1]["device_index"] = kick_host["tap"]["device_index"]
    recs[1]["rec_param_index"] = kick_host["tap"].get("rec_param_index")
    recs[2]["device_index"] = bass_host["tap"]["device_index"]
    recs[2]["rec_param_index"] = bass_host["tap"].get("rec_param_index")
    return recs


def run_region1_recapture(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    preflight = preflight_session(daw)
    write_preflight(preflight, evidence)
    report: dict[str, Any] = {
        "phase": "REGION_1 RECAPTURE",
        "NO ASTRA": True,
        "NO DSP_UNLESS_PROVENANCE": True,
        "routing_mutations": 0,
        "ASTRA CALLS": 0,
        "MUSICAL WRITES": 0,
        "preflight_pass": preflight.get("pass"),
        "live_set_path": preflight.get("live_set_path"),
        "STATE TOKENS": {
            "project_token": preflight.get("project_token"),
            "audible_token": preflight.get("audible_token"),
            "revision": preflight.get("revision"),
        },
        "region": {
            "id": "REGION_1",
            "start_qn": REGION_1_START_QN,
            "end_qn": REGION_1_END_QN,
            "unit": "quarter_note_position",
        },
    }
    if not preflight.get("pass"):
        report["status"] = "PRE-FLIGHT STOP"
        path = evidence / "region1_recapture.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        report["artifact"] = str(path)
        return report

    recs = _production_recorders(preflight)
    pos = daw.get_playback_position()
    pass_id = uuid4().hex[:12]
    journal = CaptureJournal(pass_id)
    journal.record(
        PREPARED,
        region="REGION_1",
        start_beat=REGION_1_START_QN,
        end_beat=REGION_1_END_QN,
        revision=preflight.get("revision"),
        mode=CaptureMode.PRODUCTION.value,
    )
    journal.record(RECORDING)
    try:
        one = capture_parallel_pass(
            daw,
            start_beat=REGION_1_START_QN,
            end_beat=REGION_1_END_QN,
            fire_tracks=[],
            tempo=float(pos.get("tempo") or preflight.get("tempo") or 167.0),
            session_revision=int(preflight.get("revision") or 0),
            pass_id=pass_id,
            recorders=deepcopy(recs),
            transport="arrangement",
        )
    except AudioCaptureError as exc:
        journal.record(FAILED, error=str(exc))
        report["status"] = "CAPTURE FAILED"
        report["error"] = str(exc)
        path = evidence / "region1_recapture.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        report["artifact"] = str(path)
        return report

    assets = one["assets"]
    restore = one.get("restore") or {}
    hashes = {key: sha256_file(Path(asset.file_path)) for key, asset in assets.items()}
    master = assets["master"]
    start_obs = float(master.transport_start_observed or -1.0)
    stop_obs = float(master.transport_stop_observed or -1.0)
    start_ok = abs(start_obs - REGION_1_START_QN) <= PLAYBACK_START_TOLERANCE_QN
    stop_ok = abs(stop_obs - REGION_1_END_QN) <= 4.0
    unique = len({asset.file_path for asset in assets.values()}) == 3
    signals = {
        key: {
            "class": _signal_class(asset.rms, asset.peak),
            "rms": asset.rms,
            "peak": asset.peak,
            "duration": asset.duration,
            "path": asset.file_path,
        }
        for key, asset in assets.items()
    }
    main_ok = signals["master"]["class"] == "HAS_SIGNAL"
    provenance_ok = start_ok and stop_ok and unique and bool(restore.get("ok")) and main_ok
    if provenance_ok:
        journal.record(VERIFIED, hashes=hashes, start_qn=start_obs, stop_qn=stop_obs)
        claim = VERIFIED
    else:
        journal.record(FAILED, start_qn=start_obs, stop_qn=stop_obs, restore=restore)
        claim = FAILED

    isolate_note = None
    if provenance_ok:
        if signals["kick"]["class"] == "RECORDED_SILENCE" or signals["bass"]["class"] == "RECORDED_SILENCE":
            isolate_note = (
                "Tap wrote a full-length file of digital silence while Main had signal. "
                "That is RECORDED_SILENCE, not a missing wav. MIDI unread, so this is not "
                "yet classified as VALID SILENCE IN SOURCE vs CAPTURE PATH FAILURE."
            )
    report.update(
        {
            "status": "REGION_1 TRUSTWORTHY" if provenance_ok else "REGION_1 NOT TRUSTWORTHY",
            "journal_status": claim,
            "journal": str(journal.path),
            "pass_id": pass_id,
            "playback_start_qn": start_obs,
            "playback_stop_qn": stop_obs,
            "start_ok": start_ok,
            "stop_ok": stop_ok,
            "unique_assets": unique,
            "restore_ok": restore.get("ok"),
            "signals": signals,
            "hashes": hashes,
            "cards": {key: _asset_card(asset) for key, asset in assets.items()},
            "isolate_note": isolate_note,
            "DSP_RAN": False,
            "instruction": "STOP before REGION_2/3. Do not call Astra.",
        }
    )
    path = evidence / "region1_recapture.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["artifact"] = str(path)
    return report
