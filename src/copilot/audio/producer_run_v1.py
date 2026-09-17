"""Canonical producer-run.

Reuses producer-analyze, MusicPlan, freshness/production-write loops.
Does not duplicate capture, State Trust, rollback, or Astra contracts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copilot.audio.cross_project_bootstrap_v1 import classify_project
from copilot.audio.producer_analyze_v1 import producer_analyze
from copilot.audio.terminal_state_v1 import verify_terminal_state
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.human_eval.store import now_iso

MILESTONE = "PRODUCER_RUN_V1"
ARTIFACT = "producer_run_v1.json"
CANONICAL_ANALYZE = "producer-analyze"
CANONICAL_AUTONOMOUS_DEV = (
    "production-write --first-autonomous-musical-improvement"
)
LAB_FLAGS = (
    "--autonomous-musical-evaluation-v2",
    "--autonomous-musical-evaluation-v3",
    "--arrangement-active-source-isolation",
)


def producer_run(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path,
    mode: str,
    region_id: str | None = None,
    start_qn: float | None = None,
    end_qn: float | None = None,
) -> dict[str, Any]:
    """mode=analyze | autonomous. No mock fallback."""
    if mode not in {"analyze", "autonomous"}:
        report = {
            "milestone": MILESTONE,
            "status": "BLOCKED",
            "reason": "MODE_REQUIRED",
            "detail": "Use --mode analyze or --mode autonomous",
        }
        _persist(report, evidence)
        return report

    analyze = producer_analyze(
        daw,
        evidence=evidence,
        region_id=region_id,
        start_qn=start_qn,
        end_qn=end_qn,
    )
    session = daw.snapshot(include_notes=False)
    identity = classify_project(session.project_path, session.project_name)
    terminal = verify_terminal_state(transport_playing=session.transport.playing)
    report: dict[str, Any] = {
        "milestone": MILESTONE,
        "mode": mode,
        "ts": now_iso(),
        "project": identity,
        "analyze_status": analyze.get("status"),
        "gate": analyze.get("gate"),
        "reused": {
            "analyze": CANONICAL_ANALYZE,
            "autonomous_development": CANONICAL_AUTONOMOUS_DEV,
            "lab_flags_remain_on_production_write": list(LAB_FLAGS),
        },
        "MUSICAL WRITES": 0,
        "terminal": terminal,
        "no_duplicate_implementations": True,
    }

    if mode == "analyze":
        report["status"] = analyze.get("status") or "BLOCKED"
        report["NO WRITE"] = True
        _persist(report, evidence)
        return report

    justified = bool((analyze.get("gate") or {}).get("write_justified_if_autonomous"))
    if analyze.get("status") == "BLOCKED" or not justified:
        report["status"] = analyze.get("status") or "BLOCKED"
        report["musical_decision"] = "ABSTAIN"
        report["reason"] = (analyze.get("gate") or {}).get("reason") or analyze.get("reason")
        report["NO WRITE"] = True
        _persist(report, evidence)
        return report

    if identity.get("is_development_working_copy"):
        from copilot.audio.first_autonomous_musical_improvement_v1 import (
            run_first_autonomous_musical_improvement_v1,
        )

        frozen = run_first_autonomous_musical_improvement_v1(daw, evidence=evidence)
        report["status"] = frozen.get("status") or frozen.get(
            "FIRST_AUTONOMOUS_MUSICAL_IMPROVEMENT_V1"
        )
        report["musical_decision"] = frozen.get("final_musical_decision")
        report["MUSICAL WRITES"] = frozen.get("MUSICAL WRITES") or 0
        report["reused_runner"] = "FIRST_AUTONOMOUS_MUSICAL_IMPROVEMENT_V1"
        report["frozen_runner"] = frozen
        _persist(report, evidence)
        return report

    report["status"] = "ACTION_NOT_AVAILABLE"
    report["musical_decision"] = "ABSTAIN"
    report["reason"] = (
        "SUPPORTED volume action on an external project is not yet live-validated. "
        "Refusing to invent a second write loop. "
        "Use producer-analyze today; autonomous SET_TRACK_VOLUME remains the frozen "
        "development-set runner."
    )
    report["NO WRITE"] = True
    report["NO MOCK SUCCESS"] = True
    _persist(report, evidence)
    return report


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
