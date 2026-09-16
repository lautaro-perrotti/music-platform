"""Token freshness gate for MusicPlan — fail closed. No production writes."""

from __future__ import annotations

from copilot.reasoning.musicplan_gate import (
    PROJECT_MISMATCH,
    STALE_AUDIBLE_STATE,
    STALE_PROJECT_STATE,
    STALE_TARGET_STATE,
    TARGET_AMBIGUOUS,
    evaluate_musicplan_gate,
)


def _fresh(**overrides):
    base = dict(
        diagnosis_accepted=True,
        diagnosis_status="SUPPORTED",
        actionable=True,
        cause_status="CAUSE_SUPPORTED",
        require_cause_supported=True,
        evidence_project_token="proj-a",
        evidence_audible_token="aud-a",
        evidence_target_token="tgt-a",
        live_project_token="proj-a",
        live_audible_token="aud-a",
        live_target_token="tgt-a",
    )
    base.update(overrides)
    return evaluate_musicplan_gate(**base)


def test_a_matching_tokens_may_open() -> None:
    result = _fresh()
    assert result.gate == "OPEN"
    assert result.code is None


def test_b_project_mismatch_closed() -> None:
    result = _fresh(live_project_token="proj-b")
    assert result.gate == "CLOSED"
    assert result.code == STALE_PROJECT_STATE


def test_c_audible_mismatch_closed() -> None:
    result = _fresh(live_audible_token="aud-b")
    assert result.gate == "CLOSED"
    assert result.code == STALE_AUDIBLE_STATE


def test_d_target_mismatch_closed() -> None:
    result = _fresh(live_target_token="tgt-b")
    assert result.gate == "CLOSED"
    assert result.code == STALE_TARGET_STATE


def test_e_same_path_token_mismatch_still_closed() -> None:
    result = _fresh(live_project_token="proj-drift", project_path_match=True)
    assert result.gate == "CLOSED"
    assert result.code == STALE_PROJECT_STATE
    assert (result.detail or {}).get("project_path_match") is True


def test_f_ref_resolved_but_target_token_stale_closed() -> None:
    # PersistentObjectRef may resolve; TARGET token freshness still required.
    result = _fresh(live_target_token="tgt-stale", target_resolve_status="RESOLVED")
    assert result.gate == "CLOSED"
    assert result.code == STALE_TARGET_STATE


def test_g_ambiguous_reconciliation_closed() -> None:
    result = _fresh(target_resolve_status=TARGET_AMBIGUOUS)
    assert result.gate == "CLOSED"
    assert result.code == TARGET_AMBIGUOUS


def test_h_fresh_snapshot_does_not_mutate_historical_tokens() -> None:
    historical = {
        "project": "proj-old",
        "audible": "aud-old",
        "target": "tgt-old",
    }
    result = _fresh(
        evidence_project_token=historical["project"],
        evidence_audible_token=historical["audible"],
        evidence_target_token=historical["target"],
        live_project_token="proj-new",
        live_audible_token="aud-new",
        live_target_token="tgt-new",
    )
    assert result.gate == "CLOSED"
    assert result.code == STALE_PROJECT_STATE
    # Historical diagnosis tokens unchanged by gate evaluation.
    assert historical == {
        "project": "proj-old",
        "audible": "aud-old",
        "target": "tgt-old",
    }


def test_project_mismatch_resolve_status() -> None:
    result = _fresh(target_resolve_status=PROJECT_MISMATCH)
    assert result.gate == "CLOSED"
    assert result.code == PROJECT_MISMATCH
