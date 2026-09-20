from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from copilot.schemas.diagnosis import (
    CandidateActionType,
    Confidence,
    DiagnosisStatus,
    FindingType,
)
from copilot.schemas.evidence import EvidenceRequest

PROMPT_VERSION = "reason-evidence-view-2"
SCHEMA_VERSION = "music-diagnosis-reason-2"
ANALYSIS_VERSION = "lowend-obs-1"
DOMAIN = "fullmix+lowend"


class ClaimClass(StrEnum):
    FACTUAL_REFERENCE = "FACTUAL_REFERENCE"
    INTERPRETATION = "INTERPRETATION"
    HYPOTHESIS = "HYPOTHESIS"
    RECOMMENDATION = "RECOMMENDATION"


class CausalStance(StrEnum):
    COINCIDES_WITH = "COINCIDES_WITH"
    COMPATIBLE_WITH = "COMPATIBLE_WITH"
    SUGGESTS = "SUGGESTS"
    WEAKLY_SUPPORTS = "WEAKLY_SUPPORTS"
    STRONGLY_SUPPORTS = "STRONGLY_SUPPORTS"
    CAUSE_UNRESOLVED = "CAUSE_UNRESOLVED"


class GroundedHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    evidence_refs: list[str]
    reasoning_summary: str
    confidence: Confidence
    alternatives_considered: list[str]
    contradicting_evidence_refs: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    status: DiagnosisStatus | None = None
    causal_stance: CausalStance | None = None


class ReasoningCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: CandidateActionType
    target: str | None = None
    entity_refs: list[str] = Field(default_factory=list)
    reason: str
    expected_effect: str
    risk: str
    evidence_refs: list[str]


class CandidateStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)


class ReasoningOutput(BaseModel):
    """Vendor-neutral structured result. Core assigns diagnosis_id after accept."""

    model_config = ConfigDict(extra="forbid")

    category: FindingType
    summary: str
    status: DiagnosisStatus
    confidence: Confidence
    hypotheses: list[GroundedHypothesis]
    evidence_refs: list[str]
    contradicting_evidence_refs: list[str]
    limitations: list[str]
    candidate_actions: list[ReasoningCandidate]
    requested_evidence: list[EvidenceRequest] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    question: str = ""
    scope: str = ""
    candidate_strategies: list[CandidateStrategy] = Field(default_factory=list)
