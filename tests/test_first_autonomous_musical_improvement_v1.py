"""Unit tests for FIRST_AUTONOMOUS_MUSICAL_IMPROVEMENT_V1 action gate / holdout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from copilot.audio.first_autonomous_musical_improvement_v1 import (
    FORBIDDEN_QN_RANGES,
    HOLDOUT_REGION,
    evaluate_action_gate,
    persist_holdout_region,
)


def test_holdout_outside_forbidden_ranges() -> None:
    start = float(HOLDOUT_REGION["start_qn"])
    end = float(HOLDOUT_REGION["end_qn"])
    for lo, hi, label in FORBIDDEN_QN_RANGES:
        assert not (start < hi and end > lo), label


def test_persist_holdout_rejects_forbidden(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        persist_holdout_region(
            tmp_path,
            {"id": "BAD", "start_qn": 32.0, "end_qn": 64.0},
        )


def test_persist_holdout_ok(tmp_path: Path) -> None:
    path = persist_holdout_region(tmp_path, HOLDOUT_REGION)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["region"]["id"] == "HOLDOUT_128_160"
    assert payload["NO_ANALYZER_RETUNE"] is True


def test_gate_insufficient_evidence() -> None:
    g = evaluate_action_gate(
        diagnosis_status="INSUFFICIENT_EVIDENCE",
        diagnosis_accepted=False,
        category="INSUFFICIENT_EVIDENCE",
        candidate_actions=[],
        cause_status=None,
    )
    assert g["milestone_status"] == "INSUFFICIENT_EVIDENCE"


def test_gate_insufficient_evidence_with_no_change_stays_insufficient() -> None:
    """NO_CHANGE under IE is abstention for lack of evidence, not musical clearance."""
    g = evaluate_action_gate(
        diagnosis_status="INSUFFICIENT_EVIDENCE",
        diagnosis_accepted=True,
        category="INSUFFICIENT_EVIDENCE",
        candidate_actions=[{"action_type": "NO_CHANGE"}],
        cause_status=None,
    )
    assert g["decision"] == "ABSTAIN"
    assert g["milestone_status"] == "INSUFFICIENT_EVIDENCE"
    assert g["proceed_to_write"] is False


def test_gate_no_action_required() -> None:
    g = evaluate_action_gate(
        diagnosis_status="NO_ACTION_REQUIRED",
        diagnosis_accepted=True,
        category="NO_ACTION_REQUIRED",
        candidate_actions=[{"action_type": "NO_CHANGE"}],
        cause_status=None,
    )
    assert g["decision"] == "ABSTAIN"
    assert g["milestone_status"] == "NO_ACTION_REQUIRED"
    assert g["proceed_to_write"] is False


def test_gate_supported_non_volume_is_action_not_available() -> None:
    g = evaluate_action_gate(
        diagnosis_status="SUPPORTED",
        diagnosis_accepted=True,
        category="TEMPORAL_MASKING",
        candidate_actions=[
            {"action_type": "SHORTEN_BASS_RELEASE", "target": "Sub Sub Bass"}
        ],
        cause_status="CAUSE_SUPPORTED",
    )
    assert g["decision"] == "ABSTAIN"
    assert g["milestone_status"] == "ACTION_NOT_AVAILABLE"
    assert g["proceed_to_write"] is False
    assert "SHORTEN_BASS_RELEASE" in (g.get("required_tools") or [])


def test_gate_refuses_eq_approximation_via_volume() -> None:
    g = evaluate_action_gate(
        diagnosis_status="SUPPORTED",
        diagnosis_accepted=True,
        category="SPECTRAL_MASKING",
        candidate_actions=[
            {"action_type": "REDUCE_LOW_BAND_ENERGY", "target": "Sub Sub Bass"}
        ],
        cause_status="CAUSE_SUPPORTED",
    )
    assert g["milestone_status"] == "ACTION_NOT_AVAILABLE"


def test_gate_supported_without_volume_candidate_abstains() -> None:
    g = evaluate_action_gate(
        diagnosis_status="SUPPORTED",
        diagnosis_accepted=True,
        category="LEVEL_IMBALANCE",
        candidate_actions=[],
        cause_status="CAUSE_SUPPORTED",
    )
    assert g["milestone_status"] == "ACTION_NOT_AVAILABLE"
    assert g["proceed_to_write"] is False


def test_gate_volume_candidate_without_cause_insufficient() -> None:
    g = evaluate_action_gate(
        diagnosis_status="SUPPORTED",
        diagnosis_accepted=True,
        category="LEVEL_IMBALANCE",
        candidate_actions=[{"action_type": "SET_TRACK_VOLUME", "target": "Pad"}],
        cause_status=None,
    )
    assert g["milestone_status"] == "INSUFFICIENT_EVIDENCE"
    assert g["proceed_to_write"] is False


def test_gate_volume_path_opens_only_when_fully_justified() -> None:
    g = evaluate_action_gate(
        diagnosis_status="SUPPORTED",
        diagnosis_accepted=True,
        category="LEVEL_IMBALANCE",
        candidate_actions=[{"action_type": "SET_TRACK_VOLUME", "target": "Pad"}],
        cause_status="CAUSE_SUPPORTED",
    )
    assert g["decision"] == "ACT"
    assert g["proceed_to_write"] is True
