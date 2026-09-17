"""CROSS_PROJECT_MUSICAL_VALIDATION_V1 — first unseen real song, read-only.

No musical feature work until this run tells us what is missing.
Does not modify code for a specific song. Does not write music.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copilot.audio.cross_project_bootstrap_v1 import classify_project, retain_tokens
from copilot.audio.doctor_v1 import doctor
from copilot.audio.producer_analyze_v1 import producer_analyze
from copilot.audio.project_ready_v1 import project_ready
from copilot.audio.working_copy_policy_v1 import evaluate_working_copy
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.human_eval.store import now_iso

MILESTONE = "CROSS_PROJECT_MUSICAL_VALIDATION_V1"
ARTIFACT = "cross_project_musical_validation_v1.json"
STATUS = "WAITING_FOR_EXTERNAL_SONG"
FROZEN_FEATURES = True

KNOWN_NON_HOLDOUT = frozenset(
    {
        "development_original",
        "development_working_copy",
        "bootstrap_fixture",
        "untitled_scratch",
        "unknown",
    }
)

# What to build next. Never invent EQ/comp/MIDI here.
NEXT_FIX_BUG = "FIX_BUG_AND_RERUN"
NEXT_AUDIT_EVIDENCE = "AUDIT_MISSING_EVIDENCE"
NEXT_AUDIT_ACTION = "AUDIT_NEXT_ACTION_TYPE"
NEXT_AUTONOMOUS = "ENABLE_EXTERNAL_AUTONOMOUS_EVALUATION"
NEXT_VOLUME_LOOP = "MUSICPLAN_SET_TRACK_VOLUME"
NEXT_READ_ONLY_VERIFIED = "READ_ONLY_GENERALIZATION_VERIFIED"
NEXT_NEED_SONG = "WAITING_FOR_EXTERNAL_SONG"


def interpret_read_only(
    *,
    plumbing_ok: bool,
    analyze_status: str | None,
    gate: dict[str, Any] | None = None,
    plumbing_reason: str = "",
) -> dict[str, Any]:
    """Map one read-only result to the only development that is justified."""
    gate = gate or {}
    status = str(analyze_status or "")
    if not plumbing_ok:
        return {
            "next": NEXT_FIX_BUG,
            "reason": plumbing_reason or "plumbing_failed",
            "develop": "Fix the concrete bootstrap/capture/identity bug. Do not add musical features.",
            "NO WRITE": True,
        }
    if status == "INSUFFICIENT_EVIDENCE":
        return {
            "next": NEXT_AUDIT_EVIDENCE,
            "reason": gate.get("reason") or status,
            "develop": "Audit missing evidence. Perception work only if that audit justifies it.",
            "NO WRITE": True,
        }
    if status == "DIAGNOSIS_UNSTABLE":
        return {
            "next": NEXT_AUDIT_EVIDENCE,
            "reason": status,
            "develop": "Diagnosis unstable. Do not pick a side. Audit evidence, do not add actions.",
            "NO WRITE": True,
        }
    if status == "ACTION_NOT_AVAILABLE":
        return {
            "next": NEXT_AUDIT_ACTION,
            "reason": gate.get("reason") or status,
            "required_tools": list(gate.get("required_tools") or []),
            "develop": "The next real action type comes from this gate, not from a feature wishlist.",
            "NO WRITE": True,
        }
    if status == "SUPPORTED" and gate.get("write_justified_if_autonomous"):
        return {
            "next": NEXT_VOLUME_LOOP,
            "reason": "SUPPORTED + SET_TRACK_VOLUME + LEVEL_IMBALANCE",
            "develop": "After this read-only pass: MusicPlan volume write, recapture, KEEP/ROLLBACK.",
            "NO WRITE": True,
            "autonomous_candidate": True,
        }
    if status in {"SUPPORTED", "WEAKLY_SUPPORTED", "NO_ACTION_REQUIRED"}:
        return {
            "next": NEXT_READ_ONLY_VERIFIED,
            "reason": status,
            "develop": "Read-only generalization holds. External autonomous evaluation may be enabled.",
            "NO WRITE": True,
            "enable_external_autonomous": status != "WEAKLY_SUPPORTED",
        }
    return {
        "next": NEXT_AUDIT_EVIDENCE,
        "reason": status or "unknown_status",
        "develop": "Unmapped status. Audit. Do not invent a feature.",
        "NO WRITE": True,
    }


def refuse_known_lab(identity: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any] | None:
    kind = identity.get("kind") or "unknown"
    if kind in KNOWN_NON_HOLDOUT or not policy.get("musical_holdout"):
        return {
            "milestone": MILESTONE,
            "status": NEXT_NEED_SONG,
            "CROSS_PROJECT_MUSICAL_VALIDATION_V1": NEXT_NEED_SONG,
            "reason": "NOT_AN_UNSEEN_REAL_SONG",
            "detail": (
                "Open a duplicated working copy of a real song the system has never seen. "
                "pista.als, pista_copilot_eval.als, and copilot_bootstrap_fixture.als are not this test."
            ),
            "project": identity,
            "working_copy_policy": policy,
            "NO WRITE": True,
            "MUSICAL WRITES": 0,
            "FROZEN_FEATURES": FROZEN_FEATURES,
            "ts": now_iso(),
        }
    return None


def run_cross_project_musical_validation(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path,
) -> dict[str, Any]:
    """First pass is always read-only. Reuses doctor + onboard + producer-analyze."""
    session = daw.snapshot(include_notes=False)
    retain_tokens(session)
    identity = classify_project(session.project_path, session.project_name)
    policy = evaluate_working_copy(session.project_path, session.project_name)
    blocked = refuse_known_lab(identity, policy)
    if blocked is not None:
        _persist(blocked, evidence)
        return blocked

    doctor_report = doctor(evidence=evidence, daw=daw)
    ready_report = project_ready(daw, evidence=evidence, bootstrap_apply=True)
    plumbing_ok = (
        doctor_report.get("status") == "READY"
        and ready_report.get("PROJECT_READY") == "VERIFIED"
    )
    analyze: dict[str, Any] = {}
    if plumbing_ok:
        analyze = producer_analyze(daw, evidence=evidence)
    decision = interpret_read_only(
        plumbing_ok=plumbing_ok,
        analyze_status=analyze.get("status"),
        gate=analyze.get("gate") if isinstance(analyze.get("gate"), dict) else {},
        plumbing_reason=(
            ",".join(doctor_report.get("failures") or [])
            if doctor_report.get("status") != "READY"
            else ready_report.get("reason") or ready_report.get("status") or ""
        ),
    )
    report = {
        "milestone": MILESTONE,
        "status": decision["next"],
        "CROSS_PROJECT_MUSICAL_VALIDATION_V1": decision["next"],
        "ts": now_iso(),
        "project": identity,
        "working_copy_policy": policy,
        "doctor_status": doctor_report.get("status"),
        "project_ready": ready_report.get("PROJECT_READY"),
        "analyze_status": analyze.get("status"),
        "gate": analyze.get("gate"),
        "decision": decision,
        "first_pass": "READ_ONLY",
        "NO WRITE": True,
        "MUSICAL WRITES": 0,
        "FROZEN_FEATURES": FROZEN_FEATURES,
        "NO PROJECT_SPECIFIC_CODE": True,
    }
    _persist(report, evidence)
    return report


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
