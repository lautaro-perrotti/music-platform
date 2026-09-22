"""Typed contracts for the autonomous musical feedback loop."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FeedbackIssueKind(StrEnum):
    TECHNICAL_DEFECT = "TECHNICAL_DEFECT"
    MUSICAL_WEAKNESS = "MUSICAL_WEAKNESS"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    PREFERENCE_MISMATCH = "PREFERENCE_MISMATCH"
    CREATIVE_CHOICE = "CREATIVE_CHOICE"
    UNCERTAIN_OBSERVATION = "UNCERTAIN_OBSERVATION"


class FeedbackDecision(StrEnum):
    KEEP = "KEEP"
    ADJUST = "ADJUST"
    ROLLBACK = "ROLLBACK"
    ABSTAIN = "ABSTAIN"


class ComparisonStatus(StrEnum):
    VERIFIED = "VERIFIED"
    DEFERRED = "DEFERRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ExpectedActualComparison(BaseModel):
    comparison_id: str
    dimension: str
    expected: Any = None
    actual: Any = None
    status: ComparisonStatus
    evidence_refs: list[str] = Field(default_factory=list)
    limitation: str | None = None


class MusicalProblem(BaseModel):
    problem_id: str
    kind: FeedbackIssueKind
    region: str
    dimension: str
    observation: str
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    hypotheses: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AutonomousFeedbackReport(BaseModel):
    schema_version: str = "autonomous-musical-feedback-v1"
    source_artifact: str
    plan_id: str | None = None
    project_identity: str | None = None
    alpha_status: str
    decision: FeedbackDecision
    decision_reason: str
    comparisons: list[ExpectedActualComparison] = Field(default_factory=list)
    problems: list[MusicalProblem] = Field(default_factory=list)
    critique_status: str
    provider_required: bool = True
    provider_verdict: str | None = None
    MUSICAL_WRITES: int = 0
    NO_WRITE: bool = True
    provenance: dict[str, Any] = Field(default_factory=dict)
