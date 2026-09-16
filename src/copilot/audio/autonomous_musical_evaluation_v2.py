"""AUTONOMOUS_MUSICAL_EVALUATION_V2 — one preselected holdout evaluation.

HOLDOUT_320_352 fixed before diagnosis (arrangement activity only).
V1 abstention (HOLDOUT_128_160) remains frozen regression.
SET_TRACK_VOLUME only. Safe abstention is success. One Astra call. STOP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.arrangement_activity import inspect_arrangement_activity
from copilot.audio.batch_capture import CAPTURE_BASS, CAPTURE_HOST
from copilot.audio.capture_journal_recovery import (
    recover_all_unresolved,
    unresolved_capture_journals,
)
from copilot.audio.first_autonomous_musical_improvement_v1 import (
    NON_VOLUME_ACTION_TYPES,
    PRODUCTION_ACTION,
    _build_observations,
    _capture_holdout,
    _ensure_capture_taps_enabled,
    _execute_one_volume_write,
    _mixer_snapshot,
    _musical_compare,
    _public_capture,
    _resolve_volume_target,
    _rollback_volume,
    evaluate_action_gate,
)
from copilot.audio.live_capture import set_tap_enabled, set_tap_recording
from copilot.audio.session_diagnose import WORKING_COPY_CANDIDATE, preflight_session
from copilot.audio.tap_trust import inventory_taps
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.human_eval.store import now_iso
from copilot.musicplan import (
    abstain_from_diagnosis,
    build_set_track_volume_action,
    new_plan_id,
)
from copilot.musicplan.execute import build_agent_tools
from copilot.reasoning.pipeline import reason
from copilot.reasoning.provider import configured_http_provider
from copilot.schemas.diagnosis import DiagnosisStatus, FindingType
from copilot.schemas.musicplan import (
    DiagnosisBinding,
    MusicPlan,
    PlanIntentClass,
    PlanStatus,
    VolumeOperation,
)

MILESTONE = "AUTONOMOUS_MUSICAL_EVALUATION_V2"
ARTIFACT = "autonomous_musical_evaluation_v2.json"
HOLDOUT_ARTIFACT = "ame_v2_holdout_region.json"

HOLDOUT_REGION = {
    "id": "HOLDOUT_320_352",
    "start_qn": 320.0,
    "end_qn": 352.0,
    "selection_rationale": (
        "Chosen from arrangement activity only, before diagnosis, because "
        "Drums + Rose Bass + Sub Sub Bass coincide. NOT selected for a known defect."
    ),
}

# Do NOT reuse these (calibration / prior autonomous).
FORBIDDEN_QN_RANGES: tuple[tuple[float, float, str], ...] = (
    (32.0, 64.0, "RUN1_REGION_C"),
    (96.0, 128.0, "RUN1_REGION_B"),
    (128.0, 160.0, "FAMI_V1_HOLDOUT_128_160"),
    (256.0, 288.0, "RUN1_REGION_A"),
)

# Artifacts that would mean 320–352 was already a calibration/autonomous holdout.
INDEPENDENCE_SCAN_GLOBS = (
    "session_run1.json",
    "session_run1_fullmix.json",
    "first_autonomous_musical_improvement_v1.json",
    "fami_v1_holdout_region.json",
    "audible_effect_verification*.json",
    "aev2_*.json",
    "musicplan_v1*.json",
    "controlled_write*.json",
)

SUCCESS_MODES = frozenset(
    {
        "AUTONOMOUS_IMPROVEMENT_VERIFIED",
        "NO_ACTION_REQUIRED",
        "INSUFFICIENT_EVIDENCE",
        "ACTION_NOT_AVAILABLE",
        "AUTONOMOUS_CHANGE_ROLLED_BACK",
        "DIAGNOSIS_UNSTABLE",
        "HOLDOUT_NOT_INDEPENDENT",
    }
)

# Production idle from RUN1 preflight: Device On, Rec Off for Main/Kick/Bass taps.
EXPECTED_IDLE_TAP = {"device_on": 1.0, "rec": 0.0}


def check_holdout_independence(
    evidence: Path,
    *,
    start_qn: float = 320.0,
    end_qn: float = 352.0,
) -> dict[str, Any]:
    """Fail closed if 320–352 was a prior calibration or autonomous evaluation region."""
    evidence = Path(evidence)
    conflicts: list[dict[str, Any]] = []

    # Explicit forbidden overlap.
    for lo, hi, label in FORBIDDEN_QN_RANGES:
        if start_qn < hi and end_qn > lo:
            conflicts.append({"kind": "forbidden_overlap", "label": label, "range": [lo, hi]})

    # Scan known evaluation artifacts for exact holdout / region window.
    paths: list[Path] = []
    for pattern in INDEPENDENCE_SCAN_GLOBS:
        paths.extend(evidence.glob(pattern))
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Exact autonomous/calibration holdout markers only — not transport seek probes.
        markers = (
            '"id": "HOLDOUT_320_352"',
            '"region_id": "HOLDOUT_320_352"',
            '"start_qn": 320.0,\n      "end_qn": 352.0',
            '"start_qn": 320.0, "end_qn": 352.0',
        )
        if any(m in text for m in markers):
            # Transport-only cert files are allowed (NO CAPTURE).
            if path.name == "arrangement_playback_at_qn.json":
                continue
            if '"NO CAPTURE": true' in text and "HOLDOUT_320_352" not in text:
                continue
            conflicts.append(
                {
                    "kind": "artifact_region_match",
                    "artifact": str(path),
                    "note": "320–352 appears as evaluation/calibration region",
                }
            )

    # Transport seek certification used 320 as a target qn — not a holdout evaluation.
    transport_note = {
        "arrangement_playback_at_qn.json": (
            "Contains seek targets including 320/352 under NO CAPTURE / NO DSP. "
            "Not treated as calibration or autonomous musical holdout."
        )
    }

    ok = len(conflicts) == 0
    return {
        "ok": ok,
        "holdout": {"start_qn": start_qn, "end_qn": end_qn, "id": "HOLDOUT_320_352"},
        "conflicts": conflicts,
        "transport_seek_note": transport_note,
        "verdict": "INDEPENDENT" if ok else "HOLDOUT_NOT_INDEPENDENT",
    }


def persist_holdout_v2(
    evidence: Path,
    *,
    region: dict[str, Any],
    project_token: str,
) -> Path:
    start = float(region["start_qn"])
    end = float(region["end_qn"])
    for lo, hi, label in FORBIDDEN_QN_RANGES:
        if start < hi and end > lo:
            raise ValueError(f"holdout overlaps forbidden region: {label}")
    payload = {
        "milestone": MILESTONE,
        "persisted_at": now_iso(),
        "region": {
            "id": region["id"],
            "start_qn": start,
            "end_qn": end,
        },
        "selection_rationale": region.get("selection_rationale"),
        "PROJECT_STATE_TOKEN_at_selection": project_token,
        "forbidden_ranges": [
            {"start_qn": a, "end_qn": b, "label": lab}
            for a, b, lab in FORBIDDEN_QN_RANGES
        ],
        "NO_ANALYZER_RETUNE": True,
        "selection_basis": "arrangement_activity_multi_source_coincidence_before_diagnosis",
        "not_selected_for_known_defect": True,
    }
    path = Path(evidence) / HOLDOUT_ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _tap_idle_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "track": row.get("track") or row.get("track_name"),
        "index": row.get("index") if row.get("index") is not None else row.get("track_index"),
        "slot": row.get("slot"),
        "device_on": row.get("device_on"),
        "rec": row.get("rec"),
    }


def pre_run_restore_check(daw: AbletonTcpAdapter) -> dict[str, Any]:
    """Verify transport/taps/journals before V2. Cleanup is NOT a musical action."""
    report: dict[str, Any] = {
        "started_at": now_iso(),
        "cleanup_is_musical_action": False,
    }
    daw.stop_playback()
    pos = daw.get_playback_position()
    playing = bool(pos.get("is_playing"))
    report["transport"] = {
        "playing_before_stop": playing,
        "playing_after_stop": bool(daw.get_playback_position().get("is_playing")),
        "qn": float(daw.get_playback_position().get("current_song_time") or 0.0),
    }

    open_tx = unresolved_capture_journals()
    report["unresolved_capture_journals_before"] = open_tx
    journal_recovery: dict[str, Any] | None = None
    if open_tx:
        # Canonical append-only recovery — not a musical write.
        journal_recovery = recover_all_unresolved(daw=daw)
    report["capture_journal_recovery"] = journal_recovery
    open_tx_after = unresolved_capture_journals()
    report["unresolved_capture_journals"] = open_tx_after
    report["no_open_capture_journal"] = len(open_tx_after) == 0

    preflight = preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
    report["preflight_pass"] = bool(preflight.get("pass"))
    report["preflight_missing"] = preflight.get("missing")

    # Reference idle from RUN1 / production preflight: Device On, Rec=0.
    expected = dict(EXPECTED_IDLE_TAP)
    hosts = preflight.get("capture_hosts") or {}
    observed: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    cleanups: list[dict[str, Any]] = []

    def check_host(label: str, host: dict[str, Any] | None, track_index: int | None) -> None:
        if not host:
            mismatches.append({"host": label, "error": "missing_host"})
            return
        tap = host.get("tap") or {}
        row = {
            "host": label,
            "index": track_index,
            "device_on": tap.get("device_on"),
            "rec": tap.get("rec"),
            "routing": host.get("routing"),
        }
        observed.append(row)
        on = float(tap.get("device_on") if tap.get("device_on") is not None else -1.0)
        rec = float(tap.get("rec") if tap.get("rec") is not None else -1.0)
        if abs(on - expected["device_on"]) > 0.25:
            mismatches.append(
                {
                    "host": label,
                    "field": "device_on",
                    "observed": on,
                    "expected": expected["device_on"],
                }
            )
        if rec >= 0.5:
            mismatches.append(
                {
                    "host": label,
                    "field": "rec",
                    "observed": rec,
                    "expected": expected["rec"],
                }
            )

    kick = hosts.get(CAPTURE_HOST) or {}
    bass = hosts.get(CAPTURE_BASS) or {}
    main_tap = (preflight.get("main") or {}).get("tap") or {}
    check_host(CAPTURE_HOST, kick, kick.get("index"))
    check_host(CAPTURE_BASS, bass, bass.get("index"))
    check_host(
        "MASTER",
        {"tap": main_tap, "routing": {"claim": (preflight.get("main") or {}).get("claim")}},
        -1,
    )

    # Canonical cleanup: Rec off + Device On for production taps (RUN1 idle).
    if mismatches:
        for m in list(mismatches):
            host = m.get("host")
            field = m.get("field")
            try:
                if host == CAPTURE_HOST and kick.get("index") is not None:
                    idx = int(kick["index"])
                    if field == "rec":
                        set_tap_recording(daw, False, idx, broadcast_udp=False)
                        cleanups.append({"host": host, "action": "rec_off", "index": idx})
                    if field == "device_on":
                        set_tap_enabled(daw, idx, True)
                        cleanups.append({"host": host, "action": "device_on", "index": idx})
                elif host == CAPTURE_BASS and bass.get("index") is not None:
                    idx = int(bass["index"])
                    if field == "rec":
                        set_tap_recording(daw, False, idx, broadcast_udp=False)
                        cleanups.append({"host": host, "action": "rec_off", "index": idx})
                    if field == "device_on":
                        set_tap_enabled(daw, idx, True)
                        cleanups.append({"host": host, "action": "device_on", "index": idx})
                elif host == "MASTER":
                    if field == "rec":
                        set_tap_recording(daw, False, -1, broadcast_udp=False)
                        cleanups.append({"host": host, "action": "rec_off", "index": -1})
                    if field == "device_on":
                        set_tap_enabled(daw, -1, True)
                        cleanups.append({"host": host, "action": "device_on", "index": -1})
            except Exception as exc:  # noqa: BLE001
                cleanups.append({"host": host, "error": str(exc)})

        # Re-read after cleanup.
        preflight = preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
        hosts = preflight.get("capture_hosts") or {}
        kick = hosts.get(CAPTURE_HOST) or {}
        bass = hosts.get(CAPTURE_BASS) or {}
        main_tap = (preflight.get("main") or {}).get("tap") or {}
        mismatches = []
        observed = []
        check_host(CAPTURE_HOST, kick, kick.get("index"))
        check_host(CAPTURE_BASS, bass, bass.get("index"))
        check_host(
            "MASTER",
            {"tap": main_tap, "routing": {"claim": (preflight.get("main") or {}).get("claim")}},
            -1,
        )

    taps_inv = inventory_taps(daw)
    report["tap_inventory"] = [_tap_idle_row(t) for t in (taps_inv or [])]
    report["observed_production_taps"] = observed
    report["expected_idle"] = expected
    report["mismatches_after_cleanup"] = mismatches
    report["cleanups"] = cleanups
    report["cleanup_note"] = (
        "Tap Device On / Rec Off restore via set_tap_enabled / set_tap_recording. "
        "Not an autonomous musical write."
    )

    # Routing observation paths: production hosts should already be OFF_MIX / Sends Only.
    routing_ok = True
    routing_rows = []
    for label, host in ((CAPTURE_HOST, kick), (CAPTURE_BASS, bass)):
        r = (host.get("routing") or {}) if host else {}
        mon = str(r.get("monitoring") or "")
        out = str(r.get("output") or r.get("output_type") or "")
        row = {
            "host": label,
            "monitoring": mon,
            "output": out,
            "input_channel": r.get("input_channel"),
            "already_correct_for_target": r.get("already_correct_for_target"),
        }
        # Sends Only + monitor in is the production isolation path.
        if "Sends" not in out and out not in {"", "Sends Only"}:
            # tolerate missing string forms
            pass
        routing_rows.append(row)
        if mon.lower() not in {"in", "in "} and mon != "in":
            # still record; production uses monitoring in
            pass
    report["capture_host_routing"] = routing_rows

    session = daw.snapshot(include_notes=False)
    attach_tokens(session)
    report["tokens"] = {
        "PROJECT_STATE_TOKEN": session.project_token or "",
        "AUDIBLE_STATE_TOKEN": session.audible_token or "",
    }
    # No unresolved State Trust mismatch vs itself at start — just record live tokens.
    report["state_trust_self_consistent"] = bool(
        report["tokens"]["PROJECT_STATE_TOKEN"] and report["tokens"]["AUDIBLE_STATE_TOKEN"]
    )

    ok = (
        report["transport"]["playing_after_stop"] is False
        and report["no_open_capture_journal"]
        and report["preflight_pass"]
        and len(mismatches) == 0
        and report["state_trust_self_consistent"]
    )
    report["ok"] = ok
    report["preflight"] = preflight
    return report


def _persist(evidence: Path, report: dict[str, Any]) -> Path:
    path = Path(evidence) / ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        k: v
        for k, v in report.items()
        if k not in {"_pack", "_fullmix_obs", "_tools", "preflight_raw"}
    }
    # Drop huge nested preflight if present under pre_run.
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
    daw: AbletonTcpAdapter | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in SUCCESS_MODES and status != "BLOCKED":
        status = "INSUFFICIENT_EVIDENCE"
    if daw is not None:
        try:
            daw.stop_playback()
        except Exception:  # noqa: BLE001
            pass
        try:
            final_pf = preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
            hosts = final_pf.get("capture_hosts") or {}
            report["capture_hosts_taps_final"] = {
                CAPTURE_HOST: (hosts.get(CAPTURE_HOST) or {}).get("tap"),
                CAPTURE_BASS: (hosts.get(CAPTURE_BASS) or {}).get("tap"),
                "MASTER": ((final_pf.get("main") or {}).get("tap")),
            }
            pos = daw.get_playback_position()
            report["transport_final"] = {
                "playing": bool(pos.get("is_playing")),
                "qn": float(pos.get("current_song_time") or 0.0),
            }
        except Exception as exc:  # noqa: BLE001
            report["final_state_error"] = str(exc)

    report[MILESTONE] = status
    report["status"] = status
    report["final_musical_decision"] = musical_decision
    report["STOP"] = True
    if extra:
        report.update(extra)
    report["completed_at"] = now_iso()
    # Strip raw preflight blob before persist (size).
    if isinstance(report.get("pre_run_restore"), dict):
        pr = dict(report["pre_run_restore"])
        pr.pop("preflight", None)
        report["pre_run_restore"] = pr
    _persist(evidence, report)
    return report


def map_gate_to_milestone(gate: dict[str, Any], diagnosis_status: str) -> str:
    """Preserve Astra status semantics; never collapse IE → NO_ACTION_REQUIRED."""
    ms = gate.get("milestone_status")
    if diagnosis_status == DiagnosisStatus.DIAGNOSIS_UNSTABLE.value:
        return "DIAGNOSIS_UNSTABLE"
    if ms:
        return str(ms)
    if diagnosis_status == DiagnosisStatus.NO_ACTION_REQUIRED.value:
        return "NO_ACTION_REQUIRED"
    if diagnosis_status in {
        DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
        DiagnosisStatus.WEAKLY_SUPPORTED.value,
    }:
        return "INSUFFICIENT_EVIDENCE"
    return "INSUFFICIENT_EVIDENCE"


def run_autonomous_musical_evaluation_v2(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path | None = None,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    evidence.mkdir(parents=True, exist_ok=True)
    region = dict(HOLDOUT_REGION)

    report: dict[str, Any] = {
        MILESTONE: "STARTED",
        "status": "STARTED",
        "intent_class": PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT.value,
        "created_at": now_iso(),
        "ASTRA CALLS": 0,
        "MUSICAL WRITES": 0,
        "STOP": False,
        "V1_frozen": "FIRST_AUTONOMOUS_MUSICAL_IMPROVEMENT_V1=INSUFFICIENT_EVIDENCE",
        "note": (
            "Preselected HOLDOUT_320_352. Blind evaluation. "
            "Abstention valid. SET_TRACK_VOLUME only. No analyzer retune."
        ),
        "production_action_vocabulary": [PRODUCTION_ACTION],
        "non_volume_tools_refused": sorted(NON_VOLUME_ACTION_TYPES),
    }

    # ----- Holdout independence -----
    indep = check_holdout_independence(evidence)
    report["holdout_independence"] = indep
    if not indep.get("ok"):
        return _terminal(
            report,
            evidence=evidence,
            status="HOLDOUT_NOT_INDEPENDENT",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "HOLDOUT_NOT_INDEPENDENT"},
        )

    # ----- Pre-run restore -----
    pre_run = pre_run_restore_check(daw)
    report["pre_run_restore"] = pre_run
    if not pre_run.get("ok"):
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "PRE_RUN_RESTORE_FAILED", "detail": pre_run.get("mismatches_after_cleanup")},
        )

    preflight = pre_run.get("preflight") or preflight_session(
        daw, lab_track_exclusions=frozenset({"AI Test"})
    )
    session0 = daw.snapshot(include_notes=False)
    attach_tokens(session0)
    project_token = session0.project_token or ""

    # ----- PHASE 1: freeze holdout BEFORE DSP / Astra -----
    holdout_path = persist_holdout_v2(
        evidence, region=region, project_token=str(project_token)
    )
    report["holdout"] = {
        "id": region["id"],
        "start_qn": region["start_qn"],
        "end_qn": region["end_qn"],
        "selection_rationale": region["selection_rationale"],
        "artifact": str(holdout_path),
        "PROJECT_STATE_TOKEN_at_selection": project_token,
        "persisted_at": now_iso(),
    }

    # Ensure taps still enabled for capture (idle expectation already Device On).
    tap_repair = _ensure_capture_taps_enabled(daw, preflight)
    report["capture_tap_ensure"] = tap_repair
    if tap_repair.get("repaired"):
        preflight = preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))

    session = daw.snapshot(include_notes=False)
    attach_tokens(session)
    report["fresh_tokens"] = {
        "PROJECT_STATE_TOKEN": session.project_token or "",
        "AUDIBLE_STATE_TOKEN": session.audible_token or "",
    }
    report["mixer_state"] = _mixer_snapshot(session)
    pos = daw.get_playback_position()
    tempo = float(pos.get("tempo") or preflight.get("tempo") or 120.0)

    als_path = Path(str(preflight.get("live_set_path") or preflight.get("project_path") or ""))
    if als_path.is_dir():
        candidate = als_path / "pista_copilot_eval.als"
        als_path = candidate if candidate.is_file() else Path(WORKING_COPY_CANDIDATE)
    elif not als_path.is_file():
        als_path = Path(WORKING_COPY_CANDIDATE)
    try:
        activity = inspect_arrangement_activity(als_path, [region])
    except Exception as exc:  # noqa: BLE001
        activity = {"ok": False, "error": str(exc), "regions": []}
    report["arrangement_activity"] = activity
    activity_by_id = {row["id"]: row for row in activity.get("regions") or []}

    # ----- PHASE 2: capture -----
    try:
        capture = _capture_holdout(
            daw, preflight=preflight, region=region, tempo=tempo
        )
    except Exception as exc:  # noqa: BLE001
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": f"CAPTURE_FAILED:{exc}"},
        )

    report["capture"] = _public_capture(capture)
    if not capture.get("provenance_ok"):
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
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
    report["observations"] = {
        k: v for k, v in obs.items() if k not in {"pack", "fullmix_obs"}
    }
    if not obs.get("ok"):
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "DSP_OBSERVATION_FAILED", "detail": obs.get("error")},
        )

    # ----- PHASE 4: Astra once -----
    provider = configured_http_provider()
    if provider is None:
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "REAL_MODEL_UNAVAILABLE"},
        )

    result = reason(obs["pack"], provider, timeout_s=180.0)
    report["ASTRA CALLS"] = 1
    out = None if result.output is None else result.output.model_dump(mode="json")
    report["diagnosis"] = {
        "accepted": result.accepted,
        "failure": None if result.failure is None else result.failure.value,
        "output": out,
        "diagnosis": None
        if result.diagnosis is None
        else result.diagnosis.model_dump(mode="json"),
        "audit": result.to_dict().get("audit"),
    }

    status = str((out or {}).get("status") or DiagnosisStatus.INSUFFICIENT_EVIDENCE.value)
    category = (out or {}).get("category")
    candidates = list((out or {}).get("candidate_actions") or [])
    supporting = list((out or {}).get("evidence_refs") or [])
    contradicting = list((out or {}).get("contradicting_evidence_refs") or [])
    limitations = list((out or {}).get("limitations") or [])
    hypotheses = list((out or {}).get("hypotheses") or [])

    report["diagnosis_summary"] = {
        "status": status,
        "category": category,
        "confidence": (out or {}).get("confidence"),
        "limitations": limitations,
        "hypothesis": hypotheses[0] if hypotheses else None,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "candidate_strategy": candidates,
        "cause_status": None,
        "requested_evidence": (out or {}).get("requested_evidence") or [],
    }

    # ----- PHASE 5: actionability gate -----
    gate = evaluate_action_gate(
        diagnosis_status=status,
        diagnosis_accepted=bool(result.accepted),
        category=None if category is None else str(category),
        candidate_actions=candidates,
        cause_status=None,
    )
    report["actionability"] = gate
    milestone = map_gate_to_milestone(gate, status)

    binding = DiagnosisBinding(
        diagnosis_id=f"{MILESTONE}:{region['id']}",
        diagnosis_revision=2,
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
            reason=str(gate.get("milestone_status") or gate.get("reason") or status),
        )
        report["MusicPlan"] = plan.model_dump(mode="json")
        report["causal_target"] = None
        final_session = daw.snapshot(include_notes=False)
        attach_tokens(final_session)
        return _terminal(
            report,
            evidence=evidence,
            status=milestone,
            musical_decision="ABSTAIN",
            daw=daw,
            extra={
                "final_project_state": {
                    "PROJECT_STATE_TOKEN": final_session.project_token or "",
                    "AUDIBLE_STATE_TOKEN": final_session.audible_token or "",
                },
                "transaction_terminal_state": None,
                "open_transaction": False,
                "journal_terminal_state": None,
            },
        )

    # ----- Write path (rare) -----
    live = daw.snapshot(include_notes=False)
    attach_tokens(live)
    track = _resolve_volume_target(live, candidates)
    if track is None:
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "NO_RESOLVABLE_VOLUME_TARGET"},
        )

    report["causal_target"] = {"track": track.name, "index": track.index}
    before_vol = float(track.mixer.volume)
    intended = max(0.05, before_vol - 0.02)
    if intended >= before_vol:
        return _terminal(
            report,
            evidence=evidence,
            status="ACTION_NOT_AVAILABLE",
            musical_decision="ABSTAIN",
            daw=daw,
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
        "FullMix BEFORE vs AFTER on HOLDOUT_320_352"
    )
    action.verification.musical.deferred = False
    action.verification.musical.comparison = "holdout_fullmix_before_after"
    action.verification.musical.note = (
        "KEEP only if expected musical effect supported; else ROLLBACK."
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
        notes=[f"{MILESTONE} — one SET_TRACK_VOLUME max. No second write."],
    )
    report["MusicPlan"] = plan.model_dump(mode="json")
    report["BEFORE_evidence"] = {
        "parameter_volume": before_vol,
        "fullmix": obs.get("fullmix"),
        "listen_main": obs.get("listen_main"),
        "tokens": obs.get("tokens"),
    }

    tools = build_agent_tools(
        daw, journal_path=evidence / "journals" / f"ame_v2_{uuid4().hex[:10]}.jsonl"
    )
    write_report = _execute_one_volume_write(tools, plan=plan, target_name=track.name)
    report["write_readback"] = write_report
    report["MUSICAL WRITES"] = int(
        (write_report.get("MUSICAL_WRITE_COUNT") or {}).get("forward") or 0
    )

    if not write_report.get("EXECUTED"):
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
            daw=daw,
            extra={
                "transaction_terminal_state": write_report.get("journal_terminal_state"),
                "open_transaction": bool(write_report.get("open_transaction")),
            },
        )

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
            daw=daw,
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
            daw=daw,
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
    report["musical_verification"] = compare

    if compare.get("verdict") != "SUPPORTED":
        rb = _rollback_volume(tools, target_name=track.name, rollback_value=before_vol)
        report["rollback"] = rb
        report["MUSICAL WRITES"] += int(rb.get("MUSICAL_WRITE_COUNT_rollback") or 0)
        report["adjust_recommendation"] = (
            "ADJUST_RECOMMENDED conceptually only — V2 forbids second write; rolled back."
        )
        return _terminal(
            report,
            evidence=evidence,
            status="AUTONOMOUS_CHANGE_ROLLED_BACK",
            musical_decision="ROLLBACK",
            daw=daw,
            extra={
                "transaction_terminal_state": rb.get("journal_terminal_state"),
                "open_transaction": bool(rb.get("open_transaction")),
            },
        )

    final_session = daw.snapshot(include_notes=False)
    attach_tokens(final_session)
    ft = final_session.track_by_name(track.name)
    report["unexpected_diffs"] = write_report.get("state_diff")
    return _terminal(
        report,
        evidence=evidence,
        status="AUTONOMOUS_IMPROVEMENT_VERIFIED",
        musical_decision="KEEP",
        daw=daw,
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
