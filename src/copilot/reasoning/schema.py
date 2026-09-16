from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from copilot.schemas.diagnosis import (
    CandidateActionType,
    Confidence,
    DiagnosisStatus,
    FindingType,
)
from copilot.schemas.evidence import EvidenceRequest

PROMPT_VERSION = "reason-fullmix-1"
SCHEMA_VERSION = "music-diagnosis-reason-1"
ANALYSIS_VERSION = "lowend-obs-1"
DOMAIN = "fullmix+lowend"


class GroundedHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    evidence_refs: list[str]
    reasoning_summary: str
    confidence: Confidence
    alternatives_considered: list[str]
    contradicting_evidence_refs: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)


class ReasoningCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: CandidateActionType
    target: str | None = None
    entity_refs: list[str] = Field(default_factory=list)
    reason: str
    expected_effect: str
    risk: str
    evidence_refs: list[str]


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
