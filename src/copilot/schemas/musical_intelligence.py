"""Typed, read-only contracts for the musical-intelligence recovery loop.

These records describe evidence and provider boundaries.  They do not grant
permission to mutate Ableton and they deliberately preserve UNKNOWN states.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MusicalIntelligenceStatus(StrEnum):
    IMPLEMENTED_VERIFIED = "IMPLEMENTED_VERIFIED"
    IMPLEMENTED_PARTIAL = "IMPLEMENTED_PARTIAL"
    CONTRACT_ONLY = "CONTRACT_ONLY"
    DOC_ONLY = "DOC_ONLY"
    EXPERIMENTAL = "EXPERIMENTAL"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MISSING = "MISSING"
    BROKEN = "BROKEN"
    DEFERRED_BY_DESIGN = "DEFERRED_BY_DESIGN"
    OBSOLETE = "OBSOLETE"


class AuditItem(BaseModel):
    component: str
    status: MusicalIntelligenceStatus
    owner: str = "CORE"
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_step: str | None = None


class ProviderHealth(StrEnum):
    CONFIGURED = "CONFIGURED"
    HEALTHY = "HEALTHY"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ProviderCapabilityRecord(BaseModel):
    provider_id: str
    model_id: str | None = None
    capability: str
    availability: bool
    health: ProviderHealth
    local_or_remote: str = "UNKNOWN"
    timeout_s: float | None = None
    schema_support: bool | None = None
    context_limit: int | None = None
    audio_support: bool = False
    failure_code: str | None = None
    provenance: list[str] = Field(default_factory=list)
    no_secrets_exposed: bool = True


class SemanticObservation(BaseModel):
    observation_id: str
    region: str | None = None
    semantic_dimension: str
    observation: str
    evidence_refs: list[str] = Field(default_factory=list)
    provider: str
    model: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    limitations: list[str] = Field(default_factory=list)
    temporal_scope: dict[str, float | str | None] = Field(default_factory=dict)
    no_write: bool = True


class SemanticProviderResult(BaseModel):
    status: str
    provider: str
    model: str
    observations: list[SemanticObservation] = Field(default_factory=list)
    reason: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True


class LongRangeRegion(BaseModel):
    region_id: str
    start_beat: float
    end_beat: float
    label: str = "UNKNOWN"
    energy_db: float | None = None
    energy_slope_db_per_s: float | None = None
    repetition_strength: float | None = None
    variation_score: float | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class LongRangeMusicContext(BaseModel):
    status: str
    reference_state_token: str
    regions: list[LongRangeRegion] = Field(default_factory=list)
    energy_arc: list[dict[str, Any]] = Field(default_factory=list)
    repetition: list[dict[str, Any]] = Field(default_factory=list)
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    motif_memory: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    no_write: bool = True


class CandidateRecord(BaseModel):
    candidate_id: str
    plan_fingerprint: str
    source: str
    status: str = "UNRENDERED"
    duplicate_of: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class CandidateSearchReport(BaseModel):
    status: str
    provider_status: str
    candidates: list[CandidateRecord] = Field(default_factory=list)
    rendered_candidates: list[str] = Field(default_factory=list)
    pairwise_evaluations: list[dict[str, Any]] = Field(default_factory=list)
    selected_candidate: str | None = None
    selection_authority: str = "REAL_LUCAS"
    limitations: list[str] = Field(default_factory=list)
    no_write: bool = True


class PreferenceObservation(BaseModel):
    observation_id: str
    source: str
    judgment: str
    reason: str | None = None
    scope: str
    confidence: float | None = None
    must_not_infer: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)

