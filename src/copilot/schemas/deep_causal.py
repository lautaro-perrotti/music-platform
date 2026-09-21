"""Typed, read-only contracts for DEEP_CAUSAL_V2.

These contracts describe evidence and hypotheses.  They do not authorize any
Ableton mutation and deliberately keep audio and control paths distinct.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CausalGrade(StrEnum):
    COINCIDENT = "COINCIDENT"
    COMPATIBLE_WITH_CAUSE = "COMPATIBLE_WITH_CAUSE"
    WEAK_CAUSAL_SUPPORT = "WEAK_CAUSAL_SUPPORT"
    STRONG_CAUSAL_SUPPORT = "STRONG_CAUSAL_SUPPORT"
    CAUSALITY_UNRESOLVED = "CAUSALITY_UNRESOLVED"


class CausalNodeKind(StrEnum):
    SOURCE = "SOURCE"
    DEVICE = "DEVICE"
    POST_MIXER = "POST_MIXER"
    GROUP = "GROUP"
    NESTED_GROUP = "NESTED_GROUP"
    SEND = "SEND"
    RETURN = "RETURN"
    MAIN = "MAIN"
    CONTROL = "CONTROL"
    UNKNOWN = "UNKNOWN"


class CausalPathKind(StrEnum):
    AUDIO = "AUDIO"
    CONTROL = "CONTROL"


class CausalNode(BaseModel):
    node_id: str = Field(min_length=1)
    name: str = ""
    kind: CausalNodeKind = CausalNodeKind.UNKNOWN


class CausalPath(BaseModel):
    path_id: str = Field(min_length=1)
    kind: CausalPathKind
    node_ids: list[str] = Field(min_length=2)
    valid: bool = True
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    parallel_path_ids: list[str] = Field(default_factory=list)


class WindowMeasurement(BaseModel):
    """One factual measurement for a node and a before/during/after phase."""

    node_id: str = Field(min_length=1)
    phase: Literal["before", "during", "after"]
    level_db: float | None = None
    active: bool | None = None
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    evidence_refs: list[str] = Field(default_factory=list)
    project_identity: str = ""
    project_token: str = ""
    audible_token: str = ""
    generation: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_span(self) -> "WindowMeasurement":
        if self.start_s is not None and self.end_s is not None and self.end_s < self.start_s:
            raise ValueError("measurement end must not precede start")
        return self


class CausalCandidate(BaseModel):
    candidate_id: str = Field(min_length=1)
    cause_node_id: str = Field(min_length=1)
    effect_node_id: str = Field(min_length=1)
    path_id: str = Field(min_length=1)
    cause_event_start_s: float | None = Field(default=None, ge=0)
    effect_event_start_s: float | None = Field(default=None, ge=0)
    max_propagation_ms: float = Field(default=250.0, ge=0)
    minimum_change_db: float = Field(default=3.0, ge=0)
    measurements: list[WindowMeasurement] = Field(default_factory=list)
    counterevidence: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class CausalContext(BaseModel):
    """Evidence-native input to the deterministic evaluator."""

    project_identity: str = ""
    project_token: str = ""
    audible_token: str = ""
    evidence_generation: int | None = Field(default=None, ge=0)
    nodes: list[CausalNode] = Field(default_factory=list)
    paths: list[CausalPath] = Field(default_factory=list)
    candidates: list[CausalCandidate] = Field(default_factory=list)
    counterevidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    no_write: bool = True
    musical_writes: int = 0

    @model_validator(mode="after")
    def read_only(self) -> "CausalContext":
        if not self.no_write or self.musical_writes != 0:
            raise ValueError("deep causal context is read-only")
        return self


class CausalEvidence(BaseModel):
    """Auditable result; every grade remains evidence-qualified."""

    schema_version: str = "deep-causal-v2"
    candidate_id: str
    cause_node_id: str
    effect_node_id: str
    path_id: str
    grade: CausalGrade
    path_kind: CausalPathKind | None = None
    temporal_precedence: bool | None = None
    propagation_supported: bool | None = None
    path_valid: bool | None = None
    direction_compatible: bool | None = None
    persistence_supported: bool | None = None
    reasons: list[str] = Field(default_factory=list)
    counterevidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    project_identity: str = ""
    project_token: str = ""
    audible_token: str = ""
    provenance: list[str] = Field(default_factory=list)
    no_write: bool = True
    musical_writes: int = 0

    @model_validator(mode="after")
    def read_only(self) -> "CausalEvidence":
        if not self.no_write or self.musical_writes != 0:
            raise ValueError("causal evidence must remain read-only")
        return self


class CausalEvaluation(BaseModel):
    schema_version: str = "deep-causal-v2"
    results: list[CausalEvidence] = Field(default_factory=list)
    context_provenance: list[str] = Field(default_factory=list)
    rejected: bool = False
    rejection_reason: str | None = None
    no_write: bool = True
    musical_writes: int = 0

    @model_validator(mode="after")
    def read_only(self) -> "CausalEvaluation":
        if not self.no_write or self.musical_writes != 0:
            raise ValueError("causal evaluation must remain read-only")
        return self
