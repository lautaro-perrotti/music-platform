"""Read-only contracts for ADVANCED_PERCEPTION_V1.

Providers contribute bounded observations.  They never become measurement or
state authority, and they never authorize a musical write.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class AdvancedPerceptionStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PROVIDER_LIMITED = "PROVIDER_LIMITED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class ProviderAvailability(BaseModel):
    name: str
    version: str
    available: bool
    capabilities: list[str] = Field(default_factory=list)
    reason: str | None = None
    semantic: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PerceptionObservation(BaseModel):
    observation_id: str
    provider: str
    provider_version: str
    domain: str
    question: str
    value: Any
    source_token: str
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True

    @model_validator(mode="after")
    def fail_closed(self) -> "PerceptionObservation":
        if not self.no_write:
            raise ValueError("perception observations must be NO_WRITE")
        return self


class ReferenceRoleBinding(BaseModel):
    role: str
    reference_state_token: str
    features: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ReferenceFeatureBinding(BaseModel):
    feature: str
    reference_state_token: str
    observation_ids: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ReferenceIntentBundle(BaseModel):
    bundle_id: str
    references: list[str] = Field(min_length=1)
    role_bindings: list[ReferenceRoleBinding] = Field(default_factory=list)
    feature_bindings: list[ReferenceFeatureBinding] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True

    @model_validator(mode="after")
    def validate_bindings(self) -> "ReferenceIntentBundle":
        if len(set(self.references)) != len(self.references):
            raise ValueError("reference tokens in a bundle must be unique")
        unknown = {
            binding.reference_state_token
            for binding in [*self.role_bindings, *self.feature_bindings]
            if binding.reference_state_token not in self.references
        }
        if unknown:
            raise ValueError(f"bindings reference unknown tokens: {sorted(unknown)}")
        if not self.no_write:
            raise ValueError("reference intent bundles must be NO_WRITE")
        return self


class AdvancedPerceptionResult(BaseModel):
    schema_version: str = "advanced-perception-v1"
    status: AdvancedPerceptionStatus
    source_reference_token: str
    providers: list[ProviderAvailability] = Field(default_factory=list)
    observations: list[PerceptionObservation] = Field(default_factory=list)
    fusions: list[dict[str, Any]] = Field(default_factory=list)
    multi_reference: ReferenceIntentBundle | None = None
    limitations: list[str] = Field(default_factory=list)
    timings_s: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True

    @model_validator(mode="after")
    def fail_closed(self) -> "AdvancedPerceptionResult":
        if not self.no_write:
            raise ValueError("advanced perception must be NO_WRITE")
        if any(not item.no_write for item in self.observations):
            raise ValueError("all perception observations must be NO_WRITE")
        return self
