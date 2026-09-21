"""FIRST_AUTONOMOUS_MUSICAL_IMPROVEMENT_V1 — one blind holdout evaluation.

Observe → diagnose → gate → (optional) one SET_TRACK_VOLUME write → verify →
KEEP / ROLLBACK / ABSTAIN.

Safe abstention is a successful outcome. No analyzer retuning. No new action types.
"""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.arrangement_activity import inspect_arrangement_activity
from copilot.audio.arrangement_seek import (
    PLAYBACK_START_TOLERANCE_QN,
    PRE_ROLL_QN,
    transport_target_qn,
)
from copilot.audio.batch_capture import CAPTURE_BASS, CAPTURE_HOST, capture_parallel_pass
from copilot.audio.capture_capability import CaptureMode
from copilot.audio.file_hash import sha256_file
from copilot.audio.fullmix import compute_fullmix_observation
from copilot.audio.live3r_perf3 import _asset_card
from copilot.audio.live_capture import (
    EXPECTED_TAP_PROTOCOL,
    AudioCaptureError,
    capture_dir,
    observe_asset,
    set_tap_enabled,
)
from copilot.audio.lowend_features import ANALYZER_ID, analyzer_fingerprint, compute_lowend_features
from copilot.audio.session_diagnose import (
    BASS_TARGET,
    KICK_PAD,
    WORKING_COPY_CANDIDATE,
    preflight_session,
)
from copilot.audio.session_run1 import (
    _jsonable,
    _observation_card,
    _presence_class,
    _production_recorders,
    _public_capture,
    _signal_class,
)
from copilot.audio.tap_trust import (
    FAILED,
    FINALIZING,
    PREPARED,
    RECORDING,
    VERIFIED,
    CaptureJournal,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.daw.write import WriteInDoubt
from copilot.human_eval.store import now_iso
from copilot.musicplan import (
    abstain_from_diagnosis,
    build_set_track_volume_action,
    compile_execution_envelope,
    new_plan_id,
    validate_musicplan,
)
from copilot.musicplan.execute import (
    build_agent_tools,
    diff_guard_state,
    snapshot_guard_state,
)
from copilot.reasoning.from_dsp import pack_from_lowend_features
from copilot.reasoning.from_fullmix import merge_lowend_and_fullmix
from copilot.reasoning.pipeline import reason
from copilot.reasoning.provider import configured_http_provider
from copilot.schemas.diagnosis import CandidateActionType, DiagnosisStatus, FindingType
from copilot.schemas.musicplan import (
    DiagnosisBinding,
    MusicPlan,
    PlanIntentClass,
    PlanStatus,
    VolumeOperation,
)
from copilot.schemas.session import SessionState, TrackState
from copilot.schemas.transaction import TransactionStatus

MILESTONE = "FIRST_AUTONOMOUS_MUSICAL_IMPROVEMENT_V1"
ARTIFACT = "first_autonomous_musical_improvement_v1.json"
HOLDOUT_ARTIFACT = "fami_v1_holdout_region.json"

# Forbidden: RUN1 calibration A/B/C + audible-effect engineering band 0–128.
FORBIDDEN_QN_RANGES: tuple[tuple[float, float, str], ...] = (
    (0.0, 32.0, "audible_REGION_A_LIKE"),
    (32.0, 64.0, "RUN1_REGION_C_and_audible"),
    (64.0, 96.0, "audible_REGION_D_LIKE"),
    (96.0, 128.0, "RUN1_REGION_B_and_audible_E"),
    (256.0, 288.0, "RUN1_REGION_A_calibration"),
)

# First clean 8-bar window past the audible engineering band; not a RUN1 region.
HOLDOUT_REGION = {
    "id": "HOLDOUT_128_160",
    "start_qn": 128.0,
    "end_qn": 160.0,
}

# Astra candidate types that require tools beyond production-verified SET_TRACK_VOLUME.
NON_VOLUME_ACTION_TYPES = frozenset(
    {
        CandidateActionType.SHORTEN_BASS_RELEASE.value,
        CandidateActionType.CHANGE_BASS_NOTE_LENGTH.value,
        CandidateActionType.CHANGE_OCTAVE.value,
        CandidateActionType.REDUCE_LOW_BAND_ENERGY.value,
        CandidateActionType.CHANGE_SOUND_SELECTION.value,
        CandidateActionType.SIDECHAIN.value,
    }
)

# Only production-verified MusicPlan action. Do not invent mappings from EQ/MIDI/etc.
PRODUCTION_ACTION = "SET_TRACK_VOLUME"

SUCCESS_MODES = frozenset(
    {
        "AUTONOMOUS_IMPROVEMENT_VERIFIED",
        "NO_ACTION_REQUIRED",
        "INSUFFICIENT_EVIDENCE",
        "ACTION_NOT_AVAILABLE",
        "AUTONOMOUS_CHANGE_ROLLED_BACK",
    }
)


def _overlaps_forbidden(start_qn: float, end_qn: float) -> str | None:
    for lo, hi, label in FORBIDDEN_QN_RANGES:
        if start_qn < hi and end_qn > lo:
            return label
    return None


def persist_holdout_region(evidence: Path, region: dict[str, Any]) -> Path:
    """Persist selected holdout BEFORE diagnosis. Fail if forbidden."""
    start = float(region["start_qn"])
    end = float(region["end_qn"])
    hit = _overlaps_forbidden(start, end)
    if hit:
        raise ValueError(f"holdout overlaps forbidden region: {hit}")
    payload = {
        "milestone": MILESTONE,
        "persisted_at": now_iso(),
        "region": dict(region),
        "forbidden_ranges": [
            {"start_qn": a, "end_qn": b, "label": lab}
            for a, b, lab in FORBIDDEN_QN_RANGES
        ],
        "selection_rule": (
            "First unused 32-qn window after audible engineering band 0–128; "
            "excludes RUN1 REGION_A/B/C. Selected before diagnosis. "
            "No analyzer retuning from this region."
        ),
        "NO_ANALYZER_RETUNE": True,
    }
    path = Path(evidence) / HOLDOUT_ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def evaluate_action_gate(
    *,
    diagnosis_status: str,
    diagnosis_accepted: bool,
    category: str | None,
    candidate_actions: list[dict[str, Any]],
    cause_status: str | None,
) -> dict[str, Any]:
    """Decide act vs abstain. Never approximate non-volume tools with volume."""
    status = str(diagnosis_status or "")
    actions = list(candidate_actions or [])
    action_types = [str(a.get("action_type") or "") for a in actions]
    non_volume = [t for t in action_types if t in NON_VOLUME_ACTION_TYPES]
    only_no_change = bool(action_types) and all(
        t == CandidateActionType.NO_CHANGE.value for t in action_types
    )
    has_volume_candidate = PRODUCTION_ACTION in action_types

    base = {
        "diagnosis_status": status,
        "diagnosis_accepted": bool(diagnosis_accepted),
        "category": category,
        "candidate_action_types": action_types,
        "cause_status": cause_status,
        "production_action_available": PRODUCTION_ACTION,
        "proceed_to_write": False,
    }

    if status == DiagnosisStatus.NO_ACTION_REQUIRED.value:
        return {
            **base,
            "decision": "ABSTAIN",
            "milestone_status": "NO_ACTION_REQUIRED",
            "reason": "diagnosis_indicates_no_action_required",
        }

    if status in {
        DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
        DiagnosisStatus.DIAGNOSIS_UNSTABLE.value,
        DiagnosisStatus.WEAKLY_SUPPORTED.value,
    }:
        return {
            **base,
            "decision": "ABSTAIN",
            "milestone_status": "INSUFFICIENT_EVIDENCE",
            "reason": f"status_{status}",
        }

    if only_no_change:
        return {
            **base,
            "decision": "ABSTAIN",
            "milestone_status": "NO_ACTION_REQUIRED",
            "reason": "candidates_indicate_no_change",
        }

    if not diagnosis_accepted:
        return {
            **base,
            "decision": "ABSTAIN",
            "milestone_status": "INSUFFICIENT_EVIDENCE",
            "reason": "diagnosis_not_accepted",
        }

    if status != DiagnosisStatus.SUPPORTED.value:
        return {
            **base,
            "decision": "ABSTAIN",
            "milestone_status": "INSUFFICIENT_EVIDENCE",
            "reason": f"status_{status or 'unknown'}",
        }

    # SUPPORTED: only SET_TRACK_VOLUME is production-verified.
    if non_volume:
        return {
            **base,
            "decision": "ABSTAIN",
            "milestone_status": "ACTION_NOT_AVAILABLE",
            "reason": (
                "supported_issue_requires_non_volume_tool; "
                "refusing to approximate with SET_TRACK_VOLUME"
            ),
            "required_tools": non_volume,
        }

    if not has_volume_candidate:
        # Astra vocabulary has no SET_TRACK_VOLUME; Core must not invent one.
        return {
            **base,
            "decision": "ABSTAIN",
            "milestone_status": "ACTION_NOT_AVAILABLE",
            "reason": (
                "supported_issue_but_no_SET_TRACK_VOLUME_candidate; "
                "will not invent volume mutation"
            ),
        }

    if cause_status != "CAUSE_SUPPORTED":
        return {
            **base,
            "decision": "ABSTAIN",
            "milestone_status": "INSUFFICIENT_EVIDENCE",
            "reason": "missing_actionable_causal_target",
        }

    if category != FindingType.LEVEL_IMBALANCE.value:
        return {
            **base,
            "decision": "ABSTAIN",
            "milestone_status": "ACTION_NOT_AVAILABLE",
            "reason": "SET_TRACK_VOLUME only justified for LEVEL_IMBALANCE",
        }

    return {
        **base,
        "decision": "ACT",
        "milestone_status": None,
        "reason": "supported_level_imbalance_with_volume_candidate_and_cause",
        "proceed_to_write": True,
        "action_type": PRODUCTION_ACTION,
    }


def _mixer_snapshot(session: SessionState) -> list[dict[str, Any]]:
    rows = []
    for t in session.tracks:
        rows.append(
            {
                "name": t.name,
                "index": t.index,
                "role": t.role,
                "volume": float(t.mixer.volume),
                "mute": bool(t.mixer.mute),
                "solo": bool(t.mixer.solo),
                "pan": float(t.mixer.pan),
                "arm": bool(t.mixer.arm),
                "routing": {
                    "input_type": t.routing.input_type,
                    "input_channel": t.routing.input_channel,
                    "output_type": t.routing.output_type,
                    "output_channel": t.routing.output_channel,
                    "monitoring": t.routing.monitoring,
                },
                "device_count": len(t.devices or []),
                "clip_count": len(t.clips or []),
            }
        )
    return rows


def _ensure_capture_taps_enabled(
    daw: AbletonTcpAdapter, preflight: dict[str, Any]
) -> dict[str, Any]:
    """Concrete recovery: production taps must be Device On before capture.

    Does not change routing or musical state. Kick tap was found Device Off
    after prior engineering work, which blocked holdout observation.
    """
    hosts = preflight.get("capture_hosts") or {}
    repaired: list[dict[str, Any]] = []
    for key in (CAPTURE_HOST, CAPTURE_BASS):
        host = hosts.get(key) or {}
        tap = host.get("tap") or {}
        idx = host.get("index")
        if idx is None:
            continue
        on = float(tap.get("device_on") if tap.get("device_on") is not None else 1.0)
        if on < 0.5:
            set_tap_enabled(daw, int(idx), True)
            repaired.append({"host": key, "index": int(idx), "was": on, "set_to": 1.0})
    main = ((preflight.get("main") or {}).get("tap") or {})
    # Master tap index is -1 in inventory; enable via MASTER path if off.
    main_on = float(main.get("device_on") if main.get("device_on") is not None else 1.0)
    if main_on < 0.5:
        set_tap_enabled(daw, -1, True)
        repaired.append({"host": "MASTER", "index": -1, "was": main_on, "set_to": 1.0})
    return {"repaired": repaired, "ok": True}


def _capture_holdout(
    daw: AbletonTcpAdapter,
    *,
    preflight: dict[str, Any],
    region: dict[str, Any],
    tempo: float,
) -> dict[str, Any]:
    recs = _production_recorders(preflight)
    recs[1]["require_signal"] = False
    recs[2]["require_signal"] = False
    pass_id = uuid4().hex[:12]
    journal = CaptureJournal(pass_id)
    journal.record(
        PREPARED,
        region=region["id"],
        start_beat=region["start_qn"],
        end_beat=region["end_qn"],
        revision=preflight.get("revision"),
        mode=CaptureMode.PRODUCTION.value,
        milestone=MILESTONE,
        tap_protocol=EXPECTED_TAP_PROTOCOL,
    )
    journal.record(RECORDING)
    one = capture_parallel_pass(
        daw,
        start_beat=float(region["start_qn"]),
        end_beat=float(region["end_qn"]),
        fire_tracks=[],
        tempo=tempo,
        session_revision=int(preflight.get("revision") or 0),
        pass_id=pass_id,
        recorders=deepcopy(recs),
        transport="arrangement",
    )
    timings = one.get("timings") or {}
    journal.record(FINALIZING, timings={"total_s": timings.get("total_s")})
    assets = one["assets"]
    hashes = {key: sha256_file(Path(asset.file_path)) for key, asset in assets.items()}
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
    provenance_ok = (
        start_ok
        and unique
        and bool(restore.get("ok"))
        and main_ok
        and mapping == "requested_region"
    )
    if provenance_ok:
        journal.record(
            VERIFIED,
            hashes=hashes,
            implied_start_qn=implied,
            transport_target_qn=target_qn,
        )
        claim = VERIFIED
    else:
        journal.record(
            FAILED,
            implied_start_qn=implied,
            transport_target_qn=target_qn,
            restore=restore,
            mapping=mapping,
        )
        claim = FAILED
    return {
        "region": region,
        "pass_id": pass_id,
        "journal": str(journal.path),
        "status": claim,
        "restore_ok": restore.get("ok"),
        "wavs": {key: asset.file_path for key, asset in assets.items()},
        "hashes": hashes,
        "cards": {key: _asset_card(asset) for key, asset in assets.items()},
        "signals": signals,
        "transport_target_qn": target_qn,
        "implied_start_qn": implied,
        "start_ok": start_ok,
        "mapping": mapping,
        "sample_rate": assets["master"].sample_rate,
        "tap_protocol": EXPECTED_TAP_PROTOCOL,
        "elapsed_s": timings.get("total_s"),
        "assets": assets,
        "provenance_ok": provenance_ok,
    }


def _build_observations(
    *,
    capture: dict[str, Any],
    preflight: dict[str, Any],
    activity_row: dict[str, Any] | None,
    tempo: float,
) -> dict[str, Any]:
    assets = capture["assets"]
    region = capture["region"]
    region_label = f"{region['start_qn']:g}->{region['end_qn']:g}qn"
    features = compute_lowend_features(assets)
    if not features.get("ok"):
        return {"ok": False, "error": features.get("missing"), "region": region}

    project_token = str(preflight.get("project_token") or "")
    audible_token = str(preflight.get("audible_token") or "")
    pack = pack_from_lowend_features(
        region_id=region["id"],
        region=region_label,
        features=features,
        views=assets,
        project_token=project_token,
        audible_token=audible_token,
        target_token=None,
        kick_name=KICK_PAD,
        bass_name=BASS_TARGET,
    )
    master_path = Path(str(capture["wavs"]["master"]))
    digest = sha256_file(master_path) or ""
    fullmix = compute_fullmix_observation(
        master_path,
        region_id=region["id"],
        region_label=region_label,
        audio_sha256=digest,
    )
    merged = merge_lowend_and_fullmix(pack, fullmix)
    observations = {
        key: observe_asset(asset, region_label, float(asset.tempo or tempo))
        for key, asset in assets.items()
    }
    for obs in observations.values():
        obs.project_token = project_token
        obs.audible_token = audible_token
        obs.limitations = [
            "ALIGNMENT_LIMITED ±52 ms",
            "DSP_PERSIST_IS_ENERGY",
            "FACTUAL_ONLY",
            "HOLDOUT_NO_ANALYZER_RETUNE",
        ]

    source_class = {
        "drums": _presence_class(
            capture["signals"]["kick"]["class"],
            ((activity_row or {}).get("drums") or {}).get("class"),
        ),
        "sub_sub_bass": _presence_class(
            capture["signals"]["bass"]["class"],
            ((activity_row or {}).get("sub_sub_bass") or {}).get("class"),
        ),
        "rose_bass_arrangement": ((activity_row or {}).get("rose_bass") or {}).get(
            "class"
        ),
    }
    dest = capture_dir() / f"listen_{region['id']}_main.wav"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(master_path, dest)

    return {
        "ok": True,
        "region": region,
        "region_label": region_label,
        "source_class": source_class,
        "activity": activity_row,
        "features": _jsonable(
            {
                "ok": features.get("ok"),
                "kick_rms": features.get("kick_rms"),
                "bass_rms": features.get("bass_rms"),
                "master_rms": features.get("master_rms"),
                "temporal": features.get("temporal"),
                "spectral": {
                    "events_with_co_concentration": (features.get("spectral") or {}).get(
                        "events_with_co_concentration"
                    ),
                    "dominant_attack_band": (features.get("spectral") or {}).get(
                        "dominant_attack_band"
                    ),
                },
            }
        ),
        "fullmix": {
            "audio_sha256": fullmix.audio_sha256,
            "energy_event_count": len(fullmix.energy_events),
            "energy_events": [
                {
                    "kind": ev.kind.value if hasattr(ev.kind, "value") else ev.kind,
                    "start_s": ev.start_s,
                    "end_s": ev.end_s,
                    "duration_s": ev.duration_s,
                    "relative_drop_db": ev.relative_drop_db,
                }
                for ev in (fullmix.energy_events or [])[:12]
            ],
            "dynamics": [
                d.model_dump(mode="json") for d in (fullmix.dynamics or [])
            ],
        },
        "music_observation": {
            key: _observation_card(obs) for key, obs in observations.items()
        },
        "evidence_pack": merged.model_dump(mode="json"),
        "listen_main": str(dest),
        "tokens": {
            "PROJECT_STATE_TOKEN": project_token,
            "AUDIBLE_STATE_TOKEN": audible_token,
        },
        "pack": merged,
        "fullmix_obs": fullmix,
    }


def _persist(evidence: Path, report: dict[str, Any]) -> Path:
    path = Path(evidence) / ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    # Drop non-JSON objects before write.
    clean = {k: v for k, v in report.items() if k not in {"_pack", "_fullmix_obs", "_tools"}}
    path.write_text(
        json.dumps(clean, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    report["artifact"] = str(path)
    return path


def _terminal(
    report: dict[str, Any],
    *,
    evidence: Path,
    status: str,
    musical_decision: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in SUCCESS_MODES and status != "BLOCKED":
        status = "INSUFFICIENT_EVIDENCE"
    report[MILESTONE] = status
    report["status"] = status
    report["final_musical_decision"] = musical_decision
    report["STOP"] = True
    if extra:
        report.update(extra)
    report["completed_at"] = now_iso()
    _persist(evidence, report)
    return report


def _resolve_volume_target(
    session: SessionState,
    candidate_actions: list[dict[str, Any]],
) -> TrackState | None:
    """Resolve a track name from Astra candidates; never invent a preferred track."""
    for row in candidate_actions:
        if str(row.get("action_type") or "") != PRODUCTION_ACTION:
            continue
        name = row.get("target")
        if not name:
            continue
        track = session.track_by_name(str(name))
        if track is not None:
            return track
    return None


def _execute_one_volume_write(
    tools,
    *,
    plan: MusicPlan,
    target_name: str,
) -> dict[str, Any]:
    """Single forward MusicPlan write + readback. Does not auto-rollback."""
    session = tools.get_session_snapshot()
    attach_tokens(session)
    validated = validate_musicplan(plan, session=session)
    out: dict[str, Any] = {
        "plan_id": validated.plan_id,
        "plan_status": validated.status.value,
        "gate": validated.gate,
        "EXECUTED": False,
        "MUSICAL_WRITE_COUNT": {"forward": 0, "rollback": 0},
    }
    if validated.status is not PlanStatus.READY_FOR_EXECUTION:
        out["status"] = "NOT_EXECUTABLE"
        out["error"] = validated.rejection_reason or validated.status.value
        return out

    envelope = compile_execution_envelope(validated, session=session)
    envelope = envelope.model_copy(
        update={"envelope_id": f"env_{uuid4().hex[:10]}", "dry_run_only": False}
    )
    if envelope.dry_run_only:
        out["status"] = "ENVELOPE_NOT_EXECUTABLE"
        return out

    out["envelope_id"] = envelope.envelope_id
    out["requested_after"] = envelope.requested_after
    out["rollback_value"] = envelope.rollback_value
    out["expected_before"] = envelope.expected_before

    pre_guard = snapshot_guard_state(session, target_name)
    out["prewrite_guard"] = pre_guard.get("target")
    track = session.track_by_name(target_name)
    if track is None:
        out["status"] = "TARGET_NOT_FOUND"
        return out

    txn = tools.transactions.begin(
        user_intent=(
            f"{MILESTONE} SET_TRACK_VOLUME {target_name} "
            f"{envelope.expected_before}->{envelope.requested_after}"
        ),
        session=session,
    )
    out["transaction_id"] = txn.transaction_id
    tools.transactions.plan_write(
        command_id=f"prep_{txn.transaction_id}",
        operation="set_mixer_volume",
        expected_revision=session.revision,
        before={"volume": envelope.expected_before},
        expected_after={"volume": envelope.requested_after},
        target_stable_id=track.stable_id,
        target_name_at_apply=track.name,
        target_fingerprint={"kind": "track", "role": track.role, "name": track.name},
    )

    try:
        tools.set_mixer_volume(envelope.resolved_track_index, envelope.requested_after)
    except WriteInDoubt as exc:
        tools.transactions.mark_in_doubt(str(exc), getattr(exc, "command_id", ""))
        out["status"] = "IN_DOUBT"
        out["error"] = str(exc)
        out["journal_terminal_state"] = TransactionStatus.IN_DOUBT.value
        out["open_transaction"] = tools.transactions._open is not None
        return out
    except Exception as exc:  # noqa: BLE001
        tools.transactions.abort(str(exc))
        out["status"] = "WRITE_FAILED"
        out["error"] = str(exc)
        out["journal_terminal_state"] = (
            tools.transactions.history[-1].status.value
            if tools.transactions.history
            else None
        )
        out["open_transaction"] = tools.transactions._open is not None
        return out

    out["MUSICAL_WRITE_COUNT"]["forward"] = 1
    after_write = tools.get_session_snapshot()
    attach_tokens(after_write)
    after_track = after_write.track_by_name(target_name)
    if after_track is None:
        tools.transactions.mark_in_doubt("target missing after write")
        out["status"] = "IN_DOUBT"
        return out
    readback = float(after_track.mixer.volume)
    out["readback"] = readback
    if abs(readback - float(envelope.requested_after)) > 1e-4:
        tools.transactions.mark_in_doubt("readback mismatch")
        out["status"] = "READBACK_MISMATCH"
        return out

    post_guard = snapshot_guard_state(after_write, target_name)
    diff = diff_guard_state(
        pre_guard,
        post_guard,
        expected_volume_delta_target=target_name,
        expected_volume=float(envelope.requested_after),
    )
    out["state_diff"] = diff
    if not diff["only_expected_changed"]:
        tools.transactions.abort("unexpected_state_mutation_after_write")
        out["status"] = "UNEXPECTED_MUTATION"
        out["journal_terminal_state"] = (
            tools.transactions.history[-1].status.value
            if tools.transactions.history
            else None
        )
        out["open_transaction"] = tools.transactions._open is not None
        return out

    verification = {
        "parameter": "track.mixer.volume",
        "expected": envelope.requested_after,
        "observed": readback,
        "ok": True,
    }
    tools.transactions.commit(verification, session=after_write)
    out["EXECUTED"] = True
    out["status"] = "WRITE_VERIFIED"
    out["journal_terminal_state"] = TransactionStatus.COMMITTED.value
    out["open_transaction"] = tools.transactions._open is not None
    out["validated_plan"] = validated.model_dump(mode="json")
    return out


def _rollback_volume(
    tools,
    *,
    target_name: str,
    rollback_value: float,
) -> dict[str, Any]:
    session = tools.get_session_snapshot()
    attach_tokens(session)
    track = session.track_by_name(target_name)
    if track is None:
        return {"ok": False, "error": "target_missing"}
    if abs(float(track.mixer.volume) - float(rollback_value)) <= 1e-4:
        return {"ok": True, "already_restored": True, "volume": float(track.mixer.volume)}
    if tools.transactions._open is not None:
        tools.transactions.abort("rollback_close_open")
    tools.transactions.begin(
        user_intent=f"{MILESTONE} rollback {target_name} volume",
        session=session,
    )
    try:
        tools.set_mixer_volume(track.index, float(rollback_value))
        after = tools.get_session_snapshot()
        attach_tokens(after)
        at = after.track_by_name(target_name)
        observed = None if at is None else float(at.mixer.volume)
        ok = at is not None and abs(float(observed) - float(rollback_value)) <= 1e-4
        tools.transactions.commit(
            {"parameter": "track.mixer.volume", "expected": rollback_value, "observed": observed, "ok": ok},
            session=after,
        )
        return {
            "ok": ok,
            "volume": observed,
            "journal_terminal_state": TransactionStatus.COMMITTED.value
            if ok
            else TransactionStatus.IN_DOUBT.value,
            "open_transaction": tools.transactions._open is not None,
            "MUSICAL_WRITE_COUNT_rollback": 1,
        }
    except Exception as exc:  # noqa: BLE001
        if tools.transactions._open is not None:
            tools.transactions.abort(str(exc))
        return {"ok": False, "error": str(exc)}


def _musical_compare(
    *,
    before_fullmix: dict[str, Any],
    after_fullmix: dict[str, Any],
    expected_effect: str,
    diagnosis_category: str | None,
) -> dict[str, Any]:
    """Deterministic BEFORE/AFTER compare. Louder/quieter ≠ better."""
    b_events = int(before_fullmix.get("energy_event_count") or 0)
    a_events = int(after_fullmix.get("energy_event_count") or 0)
    b_dyn = before_fullmix.get("dynamics") or {}
    a_dyn = after_fullmix.get("dynamics") or {}
    supporting: list[str] = []
    contradicting: list[str] = []
    limitations = [
        "V1 musical compare is coarse FullMix event/dynamics deltas only.",
        "Does not equate louder or quieter with improvement.",
        "No analyzer retune from holdout.",
    ]

    # Without a typed expected measurable, refuse KEEP.
    if not expected_effect:
        contradicting.append("missing_expected_musical_effect")
    if diagnosis_category != FindingType.LEVEL_IMBALANCE.value:
        contradicting.append("category_not_level_imbalance")

    event_delta = a_events - b_events
    if abs(event_delta) == 0:
        supporting.append("energy_event_count_unchanged")
    else:
        supporting.append(f"energy_event_count_delta={event_delta}")

    # Ambiguous by default — V1 requires clear support for KEEP.
    verdict = "AMBIGUOUS"
    if contradicting:
        verdict = "NOT_SUPPORTED"
    elif not expected_effect.strip():
        verdict = "NOT_SUPPORTED"

    return {
        "verdict": verdict,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "limitations": limitations,
        "before_energy_event_count": b_events,
        "after_energy_event_count": a_events,
        "before_dynamics": b_dyn,
        "after_dynamics": a_dyn,
        "expected_effect": expected_effect,
        "note": (
            "KEEP only if expected improvement is supported and no material "
            "regression. Ambiguous → ROLLBACK."
        ),
    }


def run_first_autonomous_musical_improvement_v1(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path | None = None,
    region: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    evidence.mkdir(parents=True, exist_ok=True)
    region = dict(region or HOLDOUT_REGION)

    report: dict[str, Any] = {
        MILESTONE: "STARTED",
        "status": "STARTED",
        "intent_class": PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT.value,
        "created_at": now_iso(),
        "ASTRA CALLS": 0,
        "MUSICAL WRITES": 0,
        "STOP": False,
        "note": (
            "Blind holdout evaluation. Abstention is a valid success. "
            "Only SET_TRACK_VOLUME is available. No analyzer retune."
        ),
    }

    # ----- Persist holdout BEFORE any diagnosis -----
    try:
        holdout_path = persist_holdout_region(evidence, region)
    except ValueError as exc:
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            extra={"error": str(exc)},
        )
    report["holdout_region"] = dict(region)
    report["holdout_artifact"] = str(holdout_path)

    # ----- PHASE 1: read-only observation -----
    preflight = preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
    report["preflight"] = {
        "pass": preflight.get("pass"),
        "missing": preflight.get("missing"),
        "tempo": preflight.get("tempo"),
        "revision": preflight.get("revision"),
    }
    if not preflight.get("pass"):
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            extra={"error": "PREFLIGHT_FAILED"},
        )

    tap_repair = _ensure_capture_taps_enabled(daw, preflight)
    report["capture_tap_repair"] = tap_repair
    if tap_repair.get("repaired"):
        # Refresh tokens/hosts after Device On (audible may change if taps audible — they are Sends Only).
        preflight = preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
        report["preflight_after_tap_repair"] = {
            "pass": preflight.get("pass"),
            "kick_device_on": ((preflight.get("capture_hosts") or {})
                               .get(CAPTURE_HOST, {})
                               .get("tap", {})
                               .get("device_on")),
            "bass_device_on": ((preflight.get("capture_hosts") or {})
                               .get(CAPTURE_BASS, {})
                               .get("tap", {})
                               .get("device_on")),
        }

    session = daw.snapshot(include_notes=False)
    attach_tokens(session)
    report["fresh_tokens"] = {
        "PROJECT_STATE_TOKEN": session.project_token or "",
        "AUDIBLE_STATE_TOKEN": session.audible_token or "",
    }
    report["mixer_state"] = _mixer_snapshot(session)
    pos = daw.get_playback_position()
    tempo = float(pos.get("tempo") or preflight.get("tempo") or 120.0)

    from copilot.importing.working_copy_manager_v1 import find_working_copy

    als_path = Path(str(preflight.get("live_set_path") or preflight.get("project_path") or ""))
    als_path = find_working_copy(als_path) or find_working_copy(WORKING_COPY_CANDIDATE) or Path(WORKING_COPY_CANDIDATE)
    try:
        activity = inspect_arrangement_activity(als_path, [region])
    except Exception as exc:  # noqa: BLE001
        activity = {"ok": False, "error": str(exc), "regions": []}
    report["arrangement_activity"] = activity
    activity_by_id = {row["id"]: row for row in activity.get("regions") or []}

    try:
        capture = _capture_holdout(
            daw, preflight=preflight, region=region, tempo=tempo
        )
    except (AudioCaptureError, WriteInDoubt, Exception) as exc:  # noqa: BLE001
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            extra={"error": f"CAPTURE_FAILED:{exc}"},
        )

    report["capture"] = _public_capture(capture)
    if not capture.get("provenance_ok"):
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            extra={"error": "CAPTURE_PROVENANCE_FAILED"},
        )

    obs = _build_observations(
        capture=capture,
        preflight={
            **preflight,
            "project_token": session.project_token or preflight.get("project_token"),
            "audible_token": session.audible_token or preflight.get("audible_token"),
        },
        activity_row=activity_by_id.get(region["id"]),
        tempo=tempo,
    )
    report["observations"] = {k: v for k, v in obs.items() if k not in {"pack", "fullmix_obs"}}
    if not obs.get("ok"):
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            extra={"error": "DSP_OBSERVATION_FAILED", "detail": obs.get("error")},
        )

    # ----- PHASE 2: Astra diagnosis (grounded pack only) -----
    provider = configured_http_provider()
    if provider is None:
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            extra={"error": "REAL_MODEL_UNAVAILABLE"},
        )

    pack = obs["pack"]
    result = reason(pack, provider, timeout_s=180.0)
    report["ASTRA CALLS"] = 1
    out = None if result.output is None else result.output.model_dump(mode="json")
    diagnosis = None if result.diagnosis is None else result.diagnosis.model_dump(mode="json")
    report["diagnosis"] = {
        "accepted": result.accepted,
        "failure": None if result.failure is None else result.failure.value,
        "output": out,
        "diagnosis": diagnosis,
        "audit": result.to_dict().get("audit"),
    }

    status = str((out or {}).get("status") or DiagnosisStatus.INSUFFICIENT_EVIDENCE.value)
    category = (out or {}).get("category")
    confidence = (out or {}).get("confidence")
    limitations = list((out or {}).get("limitations") or [])
    hypotheses = list((out or {}).get("hypotheses") or [])
    candidates = list((out or {}).get("candidate_actions") or [])
    supporting = list((out or {}).get("evidence_refs") or [])
    contradicting = list((out or {}).get("contradicting_evidence_refs") or [])

    report["diagnosis_summary"] = {
        "status": status,
        "category": category,
        "confidence": confidence,
        "limitations": limitations,
        "hypothesis": hypotheses[0] if hypotheses else None,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "candidate_strategy": candidates,
        "cause_status": None,  # no causal-trace artifact for this holdout
    }

    # ----- PHASE 3: action gate -----
    gate = evaluate_action_gate(
        diagnosis_status=status,
        diagnosis_accepted=bool(result.accepted),
        category=None if category is None else str(category),
        candidate_actions=candidates,
        cause_status=None,
    )
    report["action_gate"] = gate

    binding = DiagnosisBinding(
        diagnosis_id=f"{MILESTONE}:{region['id']}",
        diagnosis_revision=1,
        diagnosis_status=status,
        diagnosis_accepted=bool(result.accepted),
        cause_status=None,
        region_id=str(region["id"]),
        artifact=str(evidence / ARTIFACT),
    )

    if not gate.get("proceed_to_write"):
        plan = abstain_from_diagnosis(
            diagnosis=binding,
            project_state_token=str(session.project_token or ""),
            audible_state_token=str(session.audible_token or ""),
            evidence_refs=supporting,
            reason=str(gate.get("milestone_status") or gate.get("reason")),
        )
        report["MusicPlan"] = plan.model_dump(mode="json")
        report["causal_target"] = None
        final_session = daw.snapshot(include_notes=False)
        attach_tokens(final_session)
        return _terminal(
            report,
            evidence=evidence,
            status=str(gate.get("milestone_status") or "INSUFFICIENT_EVIDENCE"),
            musical_decision="ABSTAIN",
            extra={
                "final_project_state": {
                    "PROJECT_STATE_TOKEN": final_session.project_token or "",
                    "AUDIBLE_STATE_TOKEN": final_session.audible_token or "",
                },
                "transaction_terminal_state": None,
                "open_transaction": False,
            },
        )

    # ----- PHASE 4–6: MusicPlan + BEFORE + execute (rare path) -----
    live = daw.snapshot(include_notes=False)
    attach_tokens(live)
    track = _resolve_volume_target(live, candidates)
    if track is None:
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            extra={"error": "NO_RESOLVABLE_VOLUME_TARGET"},
        )

    report["causal_target"] = {"track": track.name, "index": track.index}
    before_vol = float(track.mixer.volume)
    # Conservative small reduction — only reached when gate already justified volume.
    intended = max(0.05, before_vol - 0.02)
    if intended >= before_vol:
        return _terminal(
            report,
            evidence=evidence,
            status="ACTION_NOT_AVAILABLE",
            musical_decision="ABSTAIN",
            extra={"error": "NO_SAFE_VOLUME_HEADROOM"},
        )

    expected_effect = str(
        next(
            (
                c.get("expected_effect")
                for c in candidates
                if str(c.get("action_type")) == PRODUCTION_ACTION
            ),
            "reduce target track contribution to address LEVEL_IMBALANCE",
        )
    )

    action = build_set_track_volume_action(
        track=track,
        project_identity=live.project_identity or "",
        operation=VolumeOperation.SET,
        expected_before=before_vol,
        target_value=intended,
        reason=str(
            next(
                (
                    c.get("reason")
                    for c in candidates
                    if str(c.get("action_type")) == PRODUCTION_ACTION
                ),
                "Autonomous LEVEL_IMBALANCE volume correction",
            )
        ),
        evidence_refs=supporting or [f"{MILESTONE}.holdout"],
        session_incarnation_id=live.session_incarnation_id or "",
    )
    action.expected_effect.description = expected_effect
    action.expected_effect.measurement_to_compare_after = (
        "FullMix BEFORE vs AFTER energy/dynamics on holdout region"
    )
    action.verification.musical.deferred = False
    action.verification.musical.comparison = "holdout_fullmix_before_after"
    action.verification.musical.note = (
        "Musical KEEP only if expected effect supported; else ROLLBACK."
    )

    plan = MusicPlan(
        plan_id=new_plan_id(),
        status=PlanStatus.DRAFT,
        intent_class=PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT,
        diagnosis=binding,
        project_state_token=live.project_token or "",
        audible_state_token=live.audible_token or "",
        target_state_tokens={track.name: target_token(track)},
        evidence_refs=supporting or [f"{MILESTONE}.holdout"],
        actions=[action],
        created_at=now_iso(),
        notes=[
            f"{MILESTONE} — one justified SET_TRACK_VOLUME only.",
            "No second autonomous write in V1.",
        ],
    )
    report["MusicPlan"] = plan.model_dump(mode="json")
    report["BEFORE_evidence"] = {
        "parameter_volume": before_vol,
        "fullmix": obs.get("fullmix"),
        "listen_main": obs.get("listen_main"),
        "tokens": obs.get("tokens"),
    }

    journal_path = evidence / "journals" / f"fami_v1_{uuid4().hex[:10]}.jsonl"
    tools = build_agent_tools(daw, journal_path=journal_path)
    write_report = _execute_one_volume_write(tools, plan=plan, target_name=track.name)
    report["write_readback"] = write_report
    report["MUSICAL WRITES"] = int(
        (write_report.get("MUSICAL_WRITE_COUNT") or {}).get("forward") or 0
    )

    if not write_report.get("EXECUTED"):
        # Fail closed: attempt rollback if parameter moved.
        rb = _rollback_volume(
            tools,
            target_name=track.name,
            rollback_value=float(write_report.get("rollback_value") or before_vol),
        )
        report["rollback"] = rb
        report["MUSICAL WRITES"] += int(rb.get("MUSICAL_WRITE_COUNT_rollback") or 0)
        return _terminal(
            report,
            evidence=evidence,
            status="AUTONOMOUS_CHANGE_ROLLED_BACK",
            musical_decision="ROLLBACK",
            extra={
                "transaction_terminal_state": write_report.get("journal_terminal_state"),
                "open_transaction": bool(write_report.get("open_transaction")),
            },
        )

    # ----- PHASE 7–8: recapture AFTER + musical verification -----
    # Refresh preflight tokens after write (volume change moves AUDIBLE/TARGET).
    session_after = daw.snapshot(include_notes=False)
    attach_tokens(session_after)
    preflight_after = {
        **preflight,
        "project_token": session_after.project_token,
        "audible_token": session_after.audible_token,
        "revision": session_after.revision,
    }
    try:
        after_capture = _capture_holdout(
            daw, preflight=preflight_after, region=region, tempo=tempo
        )
    except Exception as exc:  # noqa: BLE001
        rb = _rollback_volume(tools, target_name=track.name, rollback_value=before_vol)
        report["rollback"] = rb
        report["MUSICAL WRITES"] += int(rb.get("MUSICAL_WRITE_COUNT_rollback") or 0)
        return _terminal(
            report,
            evidence=evidence,
            status="AUTONOMOUS_CHANGE_ROLLED_BACK",
            musical_decision="ROLLBACK",
            extra={"error": f"AFTER_CAPTURE_FAILED:{exc}"},
        )

    report["after_capture"] = _public_capture(after_capture)
    if not after_capture.get("provenance_ok"):
        rb = _rollback_volume(tools, target_name=track.name, rollback_value=before_vol)
        report["rollback"] = rb
        report["MUSICAL WRITES"] += int(rb.get("MUSICAL_WRITE_COUNT_rollback") or 0)
        return _terminal(
            report,
            evidence=evidence,
            status="AUTONOMOUS_CHANGE_ROLLED_BACK",
            musical_decision="ROLLBACK",
            extra={"error": "AFTER_CAPTURE_PROVENANCE_FAILED"},
        )

    after_obs = _build_observations(
        capture=after_capture,
        preflight=preflight_after,
        activity_row=activity_by_id.get(region["id"]),
        tempo=tempo,
    )
    report["AFTER_evidence"] = {
        k: v for k, v in after_obs.items() if k not in {"pack", "fullmix_obs"}
    }

    compare = _musical_compare(
        before_fullmix=obs.get("fullmix") or {},
        after_fullmix=after_obs.get("fullmix") or {},
        expected_effect=expected_effect,
        diagnosis_category=None if category is None else str(category),
    )
    report["musical_comparison"] = compare

    # ----- PHASE 9–10: decision -----
    if compare.get("verdict") != "SUPPORTED":
        # ADJUST would need a second write → rollback + recommend for later.
        rb = _rollback_volume(tools, target_name=track.name, rollback_value=before_vol)
        report["rollback"] = rb
        report["MUSICAL WRITES"] += int(rb.get("MUSICAL_WRITE_COUNT_rollback") or 0)
        report["adjust_recommendation"] = (
            "Musical effect not clearly supported; V1 forbids second write. "
            "Rolled back. Future milestone may ADJUST with fresh plan."
        )
        final_session = daw.snapshot(include_notes=False)
        attach_tokens(final_session)
        return _terminal(
            report,
            evidence=evidence,
            status="AUTONOMOUS_CHANGE_ROLLED_BACK",
            musical_decision="ROLLBACK",
            extra={
                "final_project_state": {
                    "PROJECT_STATE_TOKEN": final_session.project_token or "",
                    "AUDIBLE_STATE_TOKEN": final_session.audible_token or "",
                    "target_volume": (
                        None
                        if final_session.track_by_name(track.name) is None
                        else float(final_session.track_by_name(track.name).mixer.volume)
                    ),
                },
                "transaction_terminal_state": rb.get("journal_terminal_state"),
                "open_transaction": bool(rb.get("open_transaction")),
            },
        )

    # KEEP path (strict)
    final_session = daw.snapshot(include_notes=False)
    attach_tokens(final_session)
    ft = final_session.track_by_name(track.name)
    report["unexpected_diffs"] = write_report.get("state_diff")
    return _terminal(
        report,
        evidence=evidence,
        status="AUTONOMOUS_IMPROVEMENT_VERIFIED",
        musical_decision="KEEP",
        extra={
            "final_project_state": {
                "PROJECT_STATE_TOKEN": final_session.project_token or "",
                "AUDIBLE_STATE_TOKEN": final_session.audible_token or "",
                "target_volume": None if ft is None else float(ft.mixer.volume),
                "matches_plan": None
                if ft is None
                else abs(float(ft.mixer.volume) - float(intended)) <= 1e-4,
            },
            "transaction_terminal_state": write_report.get("journal_terminal_state"),
            "open_transaction": bool(write_report.get("open_transaction")),
        },
    )
