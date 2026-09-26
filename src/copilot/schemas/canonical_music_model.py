"""Read-only canonical view over existing musical evidence artifacts.

This module intentionally contains a view contract, not a new analyzer.  The
view exposes normalized projections and provenance while leaving each domain
artifact as the authority for its own measurements and inferences.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


CANONICAL_MUSIC_MODEL_SCHEMA_VERSION = "canonical-music-model-view-v1"


class CanonicalSourceRef(BaseModel):
    """Reference to an existing artifact; never a copied artifact body."""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = Field(min_length=1)
    schema_version: str | None = None
    artifact_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class CanonicalDomainView(BaseModel):
    """One domain projection with explicit epistemic status."""

    model_config = ConfigDict(extra="forbid")

    status: str = "NOT_AVAILABLE"
    value: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_artifacts: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class CanonicalTimeline(BaseModel):
    """Shared coordinate view; values are exposed only when evidenced."""

    model_config = ConfigDict(extra="forbid")

    tempo_bpm: float | None = Field(default=None, gt=0)
    project_qn_start: float | None = Field(default=None, ge=0)
    project_qn_end: float | None = Field(default=None, gt=0)
    local_seconds_start: float | None = Field(default=None, ge=0)
    local_seconds_end: float | None = Field(default=None, gt=0)
    reference_region: dict[str, Any] = Field(default_factory=dict)
    bar_mapping: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_spans(self) -> "CanonicalTimeline":
        if (
            self.project_qn_start is not None
            and self.project_qn_end is not None
            and self.project_qn_end <= self.project_qn_start
        ):
            raise ValueError("CANONICAL_TIMELINE_INVALID_PROJECT_QN_SPAN")
        if (
            self.local_seconds_start is not None
            and self.local_seconds_end is not None
            and self.local_seconds_end <= self.local_seconds_start
        ):
            raise ValueError("CANONICAL_TIMELINE_INVALID_LOCAL_SECONDS_SPAN")
        return self


class CanonicalMusicModelView(BaseModel):
    """One product-facing read model assembled from existing artifacts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = CANONICAL_MUSIC_MODEL_SCHEMA_VERSION
    reference_id: str | None = None
    project_id: str | None = None
    identity: dict[str, Any] = Field(default_factory=dict)
    timeline: CanonicalTimeline
    structure: CanonicalDomainView = Field(default_factory=CanonicalDomainView)
    rhythm: CanonicalDomainView = Field(default_factory=CanonicalDomainView)
    bass: CanonicalDomainView = Field(default_factory=CanonicalDomainView)
    harmony: CanonicalDomainView = Field(default_factory=CanonicalDomainView)
    sources: dict[str, CanonicalSourceRef] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    no_write: bool = True

    @model_validator(mode="after")
    def fail_closed(self) -> "CanonicalMusicModelView":
        if not self.no_write:
            raise ValueError("canonical music model views must be NO_WRITE")
        return self


__all__ = [
    "CANONICAL_MUSIC_MODEL_SCHEMA_VERSION",
    "CanonicalDomainView",
    "CanonicalMusicModelView",
    "CanonicalSourceRef",
    "CanonicalTimeline",
]

