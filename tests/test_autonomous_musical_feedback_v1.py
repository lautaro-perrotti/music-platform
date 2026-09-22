from __future__ import annotations

from pathlib import Path

from copilot.integration.autonomous_musical_feedback_v1 import (
    evaluate_alpha_payload,
)
from copilot.schemas.feedback import FeedbackDecision, FeedbackIssueKind


def _artifact(*, critique: dict | None = None, failed: int = 0, untouched: bool = True) -> dict:
    return {
        "status": "PRODUCTION_PASS_VERIFIED / REVISION_PROVIDER_LIMITED",
        "strategy_provenance": "REAL_LUCAS",
        "project": {"identity": "project-1"},
        "lucas": {"plan": {"plan_id": "alpha-1"}},
        "execution": {
            "failed": failed,
            "actions": [
                {"action_type": "SAMPLE_LOAD", "status": "VERIFIED"},
                {"action_type": "SAMPLE_LOAD", "status": "EXECUTION_DEFERRED"},
                {
                    "action_type": "DUPLICATE_CLIP_TO_ARRANGEMENT",
                    "status": "VERIFIED",
                    "source": "REAL_LUCAS_ARRANGEMENT",
                },
            ],
        },
        "post_change_context": {
            "music_analysis": {"schema_version": "music-analysis-v1"},
            "advanced_perception": {"status": "VERIFIED"},
        },
        "lucas_feedback": critique or {"status": "CRITIQUE_PROVIDER_UNAVAILABLE", "result": None},
        "final": {
            "original_untouched": untouched,
            "direct_lucas_writes": 0,
            "direct_soniq_writes": 0,
            "safe_write_authorities": 1,
        },
    }


def test_missing_provider_abstains_without_inventing_verdict() -> None:
    report = evaluate_alpha_payload(_artifact(), source_artifact=Path("alpha.json"))

    assert report.decision is FeedbackDecision.ABSTAIN
    assert report.provider_required is True
    assert report.provider_verdict is None
    assert any(p.kind is FeedbackIssueKind.UNCERTAIN_OBSERVATION for p in report.problems)
    assert report.MUSICAL_WRITES == 0
    assert report.NO_WRITE is True


def test_typed_lucas_finalize_maps_to_keep() -> None:
    report = evaluate_alpha_payload(
        _artifact(critique={"status": "CRITIQUE_COMPLETE", "result": {"verdict": "finalize"}})
    )
    assert report.decision is FeedbackDecision.KEEP
    assert report.provider_verdict == "finalize"


def test_typed_lucas_improve_maps_to_adjust() -> None:
    report = evaluate_alpha_payload(
        _artifact(critique={"status": "CRITIQUE_COMPLETE", "result": {"verdict": "improve"}})
    )
    assert report.decision is FeedbackDecision.ADJUST


def test_integrity_failure_forces_rollback_without_provider() -> None:
    report = evaluate_alpha_payload(_artifact(failed=1, untouched=False))

    assert report.decision is FeedbackDecision.ROLLBACK
    assert report.provider_required is False
    assert any(p.kind is FeedbackIssueKind.TECHNICAL_DEFECT for p in report.problems)

