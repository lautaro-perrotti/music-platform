"""AUTONOMOUS_MUSICAL_FEEDBACK_V1 over persisted Alpha evidence.

This module is deliberately offline.  It never opens Ableton and never turns
missing provider output into a musical verdict.  It establishes the boundary
between factual post-build evidence and a later Lucas decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from copilot.schemas.feedback import (
    AutonomousFeedbackReport,
    ComparisonStatus,
    ExpectedActualComparison,
    FeedbackDecision,
    FeedbackIssueKind,
    MusicalProblem,
)

MILESTONE = "AUTONOMOUS_MUSICAL_FEEDBACK_V1"


def load_alpha_artifact(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ALPHA_ARTIFACT_MUST_BE_OBJECT")
    return payload


def evaluate_alpha_artifact(path: str | Path) -> AutonomousFeedbackReport:
    """Evaluate a completed Alpha artifact without contacting Live or Astra."""
    source = Path(path)
    return evaluate_alpha_payload(load_alpha_artifact(source), source_artifact=source)


def evaluate_alpha_payload(
    artifact: dict[str, Any], *, source_artifact: str | Path = "<memory>"
) -> AutonomousFeedbackReport:
    execution = artifact.get("execution") or {}
    final = artifact.get("final") or {}
    project = artifact.get("project") or {}
    lucas = artifact.get("lucas") or {}
    plan = lucas.get("plan") or {}
    actions = execution.get("actions") or []
    sample_actions = [
        row for row in actions if row.get("action_type") in {"SAMPLE_LOAD", "LOAD_SAMPLE"}
    ]
    arrangement_actions = [
        row for row in actions if row.get("source") == "REAL_LUCAS_ARRANGEMENT"
    ]
    comparisons: list[ExpectedActualComparison] = []
    problems: list[MusicalProblem] = []

    failed = int(execution.get("failed") or 0)
    comparisons.append(
        ExpectedActualComparison(
            comparison_id="execution.failed",
            dimension="technical_execution",
            expected=0,
            actual=failed,
            status=ComparisonStatus.VERIFIED if failed == 0 else ComparisonStatus.FAILED,
            evidence_refs=["alpha.execution"],
        )
    )
    if failed:
        problems.append(
            MusicalProblem(
                problem_id="technical.execution_failure",
                kind=FeedbackIssueKind.TECHNICAL_DEFECT,
                region="POST_CHANGE",
                dimension="EXECUTION",
                observation=f"Alpha reported {failed} failed execution actions.",
                evidence_refs=["alpha.execution"],
                confidence=1.0,
                hypotheses=["SafeWrite execution or authoritative readback failed"],
            )
        )

    original_untouched = final.get("original_untouched") is True
    comparisons.append(
        ExpectedActualComparison(
            comparison_id="project.original_untouched",
            dimension="safety_boundary",
            expected=True,
            actual=original_untouched,
            status=ComparisonStatus.VERIFIED if original_untouched else ComparisonStatus.FAILED,
            evidence_refs=["alpha.final.original_untouched"],
        )
    )
    if not original_untouched:
        problems.append(
            MusicalProblem(
                problem_id="technical.original_mutation",
                kind=FeedbackIssueKind.TECHNICAL_DEFECT,
                region="PROJECT_BOUNDARY",
                dimension="SAFETY",
                observation="The Alpha artifact does not prove the original remained untouched.",
                evidence_refs=["alpha.final.original_untouched"],
                confidence=1.0,
            )
        )

    safe_authorities = final.get("safe_write_authorities")
    direct_writes = (
        final.get("direct_lucas_writes", 0),
        final.get("direct_soniq_writes", 0),
    )
    comparisons.append(
        ExpectedActualComparison(
            comparison_id="write.authority",
            dimension="write_authority",
            expected={"safe_write_authorities": 1, "direct_writes": 0},
            actual={"safe_write_authorities": safe_authorities, "direct_writes": direct_writes},
            status=(
                ComparisonStatus.VERIFIED
                if safe_authorities == 1 and direct_writes == (0, 0)
                else ComparisonStatus.FAILED
            ),
            evidence_refs=["alpha.final"],
        )
    )

    verified_samples = sum(row.get("status") == "VERIFIED" for row in sample_actions)
    comparisons.append(
        ExpectedActualComparison(
            comparison_id="sample_load.readback",
            dimension="sample_selection_and_load",
            expected=len(sample_actions),
            actual=verified_samples,
            status=(
                ComparisonStatus.VERIFIED
                if verified_samples == len(sample_actions)
                else ComparisonStatus.DEFERRED
            ),
            evidence_refs=["alpha.execution", "safe_write.readback"],
            limitation=(
                None
                if verified_samples == len(sample_actions)
                else "At least one planned sample load was deferred or ambiguous."
            ),
        )
    )
    if verified_samples != len(sample_actions):
        problems.append(
            MusicalProblem(
                problem_id="uncertain.sample_load_coverage",
                kind=FeedbackIssueKind.UNCERTAIN_OBSERVATION,
                region="POST_CHANGE",
                dimension="SAMPLE_SELECTION",
                observation=f"{verified_samples}/{len(sample_actions)} sample loads have authoritative readback.",
                evidence_refs=["alpha.execution", "safe_write.readback"],
                confidence=None,
                hypotheses=[
                    "The deferred target needs a more specific readback contract",
                    "The planned MIDI/sample target was already occupied",
                ],
                limitations=["This is not treated as a production failure."],
            )
        )

    verified_arrangement = sum(row.get("status") == "VERIFIED" for row in arrangement_actions)
    comparisons.append(
        ExpectedActualComparison(
            comparison_id="arrangement.readback",
            dimension="arrangement_placement",
            expected=len(arrangement_actions),
            actual=verified_arrangement,
            status=(
                ComparisonStatus.VERIFIED
                if verified_arrangement == len(arrangement_actions)
                else ComparisonStatus.DEFERRED
            ),
            evidence_refs=["alpha.execution", "safe_write.readback"],
        )
    )

    post_change = artifact.get("post_change_context") or {}
    has_analysis = bool(post_change.get("music_analysis")) and bool(post_change.get("advanced_perception"))
    comparisons.append(
        ExpectedActualComparison(
            comparison_id="post_change.analysis",
            dimension="capture_and_analysis",
            expected="MusicAnalysisPack + AdvancedPerception",
            actual="present" if has_analysis else "missing",
            status=ComparisonStatus.VERIFIED if has_analysis else ComparisonStatus.UNKNOWN,
            evidence_refs=["alpha.post_change_context"],
        )
    )

    critique = artifact.get("lucas_feedback") or {}
    critique_status = str(critique.get("status") or "CRITIQUE_PROVIDER_UNAVAILABLE")
    critique_result = critique.get("result")
    if critique_status != "CRITIQUE_COMPLETE" or not critique_result:
        comparisons.append(
            ExpectedActualComparison(
                comparison_id="post_change.critique",
                dimension="musical_feedback",
                expected="typed Lucas critique",
                actual=critique_status,
                status=ComparisonStatus.DEFERRED,
                evidence_refs=["alpha.lucas_feedback"],
                limitation="No KEEP/ADJUST/ROLLBACK verdict may be inferred without a provider result.",
            )
        )
        problems.append(
            MusicalProblem(
                problem_id="uncertain.post_change_critique",
                kind=FeedbackIssueKind.UNCERTAIN_OBSERVATION,
                region="POST_CHANGE",
                dimension="MUSICAL_FEEDBACK",
                observation="The production pass has no typed Lucas critique verdict.",
                evidence_refs=["alpha.lucas_feedback"],
                hypotheses=[
                    "Provider unavailable or quota exhausted",
                    "Musical weakness remains unclassified",
                ],
                limitations=["Do not convert this into KEEP, ADJUST, or ROLLBACK."],
            )
        )

    integrity_ok = (
        failed == 0
        and original_untouched
        and safe_authorities == 1
        and direct_writes == (0, 0)
    )
    if not integrity_ok:
        decision = FeedbackDecision.ROLLBACK
        reason = "Safety or execution integrity failed; rollback is the only supported decision."
    elif critique_status == "CRITIQUE_COMPLETE" and critique_result:
        decision, reason = _decision_from_verdict(str(critique_result.get("verdict") or ""))
    else:
        decision = FeedbackDecision.ABSTAIN
        reason = "Production pass is preserved; a provider is required for musical KEEP/ADJUST."

    return AutonomousFeedbackReport(
        source_artifact=str(source_artifact),
        plan_id=plan.get("plan_id"),
        project_identity=project.get("identity"),
        alpha_status=str(artifact.get("status") or "UNKNOWN"),
        decision=decision,
        decision_reason=reason,
        comparisons=comparisons,
        problems=problems,
        critique_status=critique_status,
        provider_required=decision is FeedbackDecision.ABSTAIN,
        provider_verdict=(
            str(critique_result.get("verdict"))
            if isinstance(critique_result, dict) and critique_result.get("verdict")
            else None
        ),
        provenance={
            "strategy_provenance": artifact.get("strategy_provenance"),
            "execution": "persisted_alpha_artifact",
            "MUSICAL_WRITES": 0,
        },
    )


def _decision_from_verdict(verdict: str) -> tuple[FeedbackDecision, str]:
    normalized = verdict.casefold().strip()
    if normalized == "finalize":
        return FeedbackDecision.KEEP, "Lucas critique returned finalize over persisted evidence."
    if normalized == "improve":
        return FeedbackDecision.ADJUST, "Lucas critique returned improve over persisted evidence."
    return FeedbackDecision.ABSTAIN, f"Unknown critique verdict {verdict!r}; fail closed."


def write_feedback_report(report: AutonomousFeedbackReport, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Alpha musical feedback")
    parser.add_argument(
        "artifact",
        nargs="?",
        default="logs/autonomous_producer_alpha_v1.json",
        help="persisted Alpha artifact; never opens Ableton",
    )
    parser.add_argument("--output", default=None, help="optional feedback report path")
    args = parser.parse_args(argv)
    report = evaluate_alpha_artifact(args.artifact)
    if args.output:
        write_feedback_report(report, args.output)
    print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 2 if report.decision is FeedbackDecision.ROLLBACK else 0


if __name__ == "__main__":
    raise SystemExit(main())
