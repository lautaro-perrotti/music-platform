from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from copilot.schemas.evidence import EvidenceRequest


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EvidenceStatus(StrEnum):
    MEASURED = "MEASURED"
    INFERRED = "INFERRED"
    UNRESOLVED = "UNRESOLVED"
    PHASE_NOT_ASSESSED = "PHASE_NOT_ASSESSED"


class FindingType(StrEnum):
    """Leading hypothesis label. Independent of DiagnosisStatus.

    TEMPORAL_MASKING + INSUFFICIENT_EVIDENCE means temporal masking is the
    leading hypothesis but is not sufficiently established. Category is not
    a confirmed diagnosis by itself.
    """

    TEMPORAL_MASKING = "TEMPORAL_MASKING"
    SPECTRAL_MASKING = "SPECTRAL_MASKING"
    EXCESSIVE_BASS_DECAY = "EXCESSIVE_BASS_DECAY"
    KICK_DECAY_COLLISION = "KICK_DECAY_COLLISION"
    LEVEL_IMBALANCE = "LEVEL_IMBALANCE"
    ARRANGEMENT_COLLISION = "ARRANGEMENT_COLLISION"
    POSSIBLE_DYNAMIC_INTERACTION = "POSSIBLE_DYNAMIC_INTERACTION"
    GROOVE_PATTERN = "GROOVE_PATTERN"
    ENERGY_STRUCTURE = "ENERGY_STRUCTURE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"


class DiagnosisStatus(StrEnum):
    """Commitment / support threshold. Independent of FindingType category."""

    SUPPORTED = "SUPPORTED"
    WEAKLY_SUPPORTED = "WEAKLY_SUPPORTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
    DIAGNOSIS_UNSTABLE = "DIAGNOSIS_UNSTABLE"


class CandidateActionType(StrEnum):
    SHORTEN_BASS_RELEASE = "SHORTEN_BASS_RELEASE"
    CHANGE_BASS_NOTE_LENGTH = "CHANGE_BASS_NOTE_LENGTH"
    CHANGE_OCTAVE = "CHANGE_OCTAVE"
    REDUCE_LOW_BAND_ENERGY = "REDUCE_LOW_BAND_ENERGY"
    CHANGE_SOUND_SELECTION = "CHANGE_SOUND_SELECTION"
    SIDECHAIN = "SIDECHAIN"
    NO_CHANGE = "NO_CHANGE"


class Finding(BaseModel):
    type: FindingType
    status: EvidenceStatus = EvidenceStatus.MEASURED
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    statement: str
    confidence: Confidence
    status: EvidenceStatus = EvidenceStatus.INFERRED
    evidence_refs: list[str] = Field(default_factory=list)
    claim: str | None = None
    reasoning_summary: str = ""
    alternatives_considered: list[str] = Field(default_factory=list)
    contradicting_evidence_refs: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    support_status: DiagnosisStatus | None = None


class CandidateAction(BaseModel):
    action_type: str
    target: str
    rationale: str = ""
    expected_effect: str
    risk: str
    confidence: Confidence
    evidence_refs: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)


class ObservationRef(BaseModel):
    capture_id: str
    role: str
    view: str | None = None
    signal_point: str | None = None
    signal_point_label: str | None = None
    region: str
    start_quarter: float
    end_quarter: float
    capture_quality: str | None = None
    session_revision: int | None = None
    limitations: list[str] = Field(default_factory=list)


class MusicDiagnosis(BaseModel):
    diagnosis_id: str
    region: str
    targets: dict[str, str]
    observations_used: list[ObservationRef] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    primary_hypothesis: Hypothesis | None = None
    secondary_hypotheses: list[Hypothesis] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    candidate_actions: list[CandidateAction] = Field(default_factory=list)
    no_change_is_valid: bool = True
    limitations: list[str] = Field(default_factory=list)
    structured_evidence: dict[str, Any] = Field(default_factory=dict)
    feature_sources: dict[str, Any] = Field(default_factory=dict)
    user_facing: str = ""
    phase: str = "PHASE_NOT_ASSESSED"
    timings: dict[str, float] = Field(default_factory=dict)
    status: DiagnosisStatus | None = None
    contradicting_evidence_refs: list[str] = Field(default_factory=list)
    requested_evidence: list[EvidenceRequest] = Field(default_factory=list)
    acceptance: str | None = None
    reasoning_audit: dict[str, Any] = Field(default_factory=dict)
    question: str = ""
    scope: str = ""
    project_identity: str | None = None
    candidate_strategies: list[dict[str, Any]] = Field(default_factory=list)
    confidence_components: dict[str, Any] = Field(default_factory=dict)
