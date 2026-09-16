"""Unit tests for AUTONOMOUS_MUSICAL_EVALUATION_V2."""

from __future__ import annotations

import json
from pathlib import Path

from copilot.audio.autonomous_musical_evaluation_v2 import (
    FORBIDDEN_QN_RANGES,
    HOLDOUT_REGION,
    check_holdout_independence,
    map_gate_to_milestone,
    persist_holdout_v2,
)
from copilot.audio.first_autonomous_musical_improvement_v1 import evaluate_action_gate


def test_holdout_320_352_outside_forbidden() -> None:
    start = float(HOLDOUT_REGION["start_qn"])
    end = float(HOLDOUT_REGION["end_qn"])
    for lo, hi, label in FORBIDDEN_QN_RANGES:
        assert not (start < hi and end > lo), label


def test_independence_ok_on_current_logs() -> None:
    r = check_holdout_independence(Path("logs"))
    assert r["verdict"] == "INDEPENDENT"
    assert r["ok"] is True


def test_persist_holdout_before_diagnosis(tmp_path: Path) -> None:
    path = persist_holdout_v2(
        tmp_path, region=HOLDOUT_REGION, project_token="abc123"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["region"]["id"] == "HOLDOUT_320_352"
    assert payload["PROJECT_STATE_TOKEN_at_selection"] == "abc123"
    assert payload["NO_ANALYZER_RETUNE"] is True
    assert payload["not_selected_for_known_defect"] is True


def test_ie_not_collapsed_to_no_action() -> None:
    g = evaluate_action_gate(
        diagnosis_status="INSUFFICIENT_EVIDENCE",
        diagnosis_accepted=True,
        category="INSUFFICIENT_EVIDENCE",
        candidate_actions=[{"action_type": "NO_CHANGE"}],
        cause_status=None,
    )
    assert g["milestone_status"] == "INSUFFICIENT_EVIDENCE"
    assert map_gate_to_milestone(g, "INSUFFICIENT_EVIDENCE") == "INSUFFICIENT_EVIDENCE"


def test_diagnosis_unstable_preserved() -> None:
    g = evaluate_action_gate(
        diagnosis_status="DIAGNOSIS_UNSTABLE",
        diagnosis_accepted=False,
        category="INSUFFICIENT_EVIDENCE",
        candidate_actions=[],
        cause_status=None,
    )
    assert map_gate_to_milestone(g, "DIAGNOSIS_UNSTABLE") == "DIAGNOSIS_UNSTABLE"
