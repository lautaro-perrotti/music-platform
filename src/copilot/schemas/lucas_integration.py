"""Typed ownership-boundary contracts for the Lucas/Core integration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from copilot.sample_library.schemas import SampleSetContext


class ContextFact(BaseModel):
    domain: str
    name: str
    value: Any
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class UserIntent(BaseModel):
    description: str
    requested_bpm: float | None = Field(default=None, gt=0)
    constraints: list[str] = Field(default_factory=list)


class ReferenceSectionContext(BaseModel):
    name: str
    start_beat: float
    end_beat: float
    function: str
    confidence: float | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class ReferenceContext(BaseModel):
    schema_version: str = "reference-context-v1"
    reference_state_token: str
    identity: str
    tempo_bpm: float
    sections: list[ReferenceSectionContext] = Field(default_factory=list)
    facts: list[ContextFact] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    no_write: bool = True

    @model_validator(mode="after")
    def validate_read_only(self) -> "ReferenceContext":
        if not self.no_write:
            raise ValueError("ReferenceContext must be NO_WRITE")
        return self


class SampleSetBoundary(BaseModel):
    schema_version: str = "sample-set-boundary-v1"
    context: SampleSetContext
    stable_sample_ids: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True

    @model_validator(mode="after")
    def validate_read_only(self) -> "SampleSetBoundary":
        if not self.no_write:
            raise ValueError("SampleSetBoundary must be NO_WRITE")
        candidate_ids = {
            str(candidate.get("id"))
            for candidate in self.context.candidates
            if candidate.get("id")
        }
        if not set(self.stable_sample_ids).issuperset(candidate_ids):
            raise ValueError("stable sample IDs must cover all SampleSetContext candidates")
        return self


class ProjectTrackContext(BaseModel):
    stable_id: str
    name: str
    role: str
    index_locator: int
    device_count: int
    clip_count: int
    volume: float


class ProjectContext(BaseModel):
    schema_version: str = "project-context-v1"
    project_identity: str
    project_token: str
    audible_token: str
    session_incarnation_id: str
    project_name: str | None = None
    tracks: list[ProjectTrackContext] = Field(default_factory=list)
    facts: list[ContextFact] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    no_write: bool = True

    @model_validator(mode="after")
    def validate_read_only(self) -> "ProjectContext":
        if not self.no_write:
            raise ValueError("ProjectContext must be NO_WRITE")
        if not self.project_identity or not self.project_token:
            raise ValueError("ProjectContext requires durable project identity and token")
        return self


class StyleContext(BaseModel):
    schema_version: str = "style-context-v1"
    preferences: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    novelty_preference: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True


class LucasProducerInput(BaseModel):
    schema_version: str = "lucas-producer-input-v1"
    user_intent: UserIntent
    reference: ReferenceContext
    samples: SampleSetBoundary
    style: StyleContext
    project: ProjectContext
    no_write: bool = True

    @model_validator(mode="after")
    def validate_boundary(self) -> "LucasProducerInput":
        if not self.no_write:
            raise ValueError("LucasProducerInput must be NO_WRITE")
        if self.reference.reference_state_token == self.project.project_token:
            raise ValueError("reference and target project tokens must remain distinct")
        return self
