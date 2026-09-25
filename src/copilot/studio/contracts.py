from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    PROVISIONING = "PROVISIONING"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    IN_DOUBT = "IN_DOUBT"


class ProjectRecord(BaseModel):
    project_id: str
    name: str
    created_at: str
    updated_at: str
    ableton_identity: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobRecord(BaseModel):
    job_id: str
    project_id: str
    status: JobStatus
    brief: dict[str, Any]
    provider_id: str | None = None
    provider_model: dict[str, Any] | None = None
    requested_candidates: int
    completed_candidates: int = 0
    current_stage: str | None = None
    error: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    idempotency_key: str | None = None


class ArtifactRecord(BaseModel):
    artifact_id: str
    job_id: str
    candidate_id: str | None = None
    filename: str
    relative_path: str
    sha256: str
    bytes: int
    duration_s: float | None = None
    sample_rate: int | None = None
    mime_type: str = "audio/wav"
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    rights: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CandidateRecord(BaseModel):
    candidate_id: str
    job_id: str
    ordinal: int
    status: str
    label: str
    artifact_id: str
    technical: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class VersionRecord(BaseModel):
    version_id: str
    project_id: str
    name: str
    source_candidate_id: str
    manifest: dict[str, Any]
    created_at: str


class StudioEvent(BaseModel):
    event_id: str
    job_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


# --- Produce: "Generate in Ableton, preview here." -------------------------
# Ableton is the canonical musical state. A variation is editable material on a
# Copilot-owned track in the Working Copy; the WAV preview is only an audition
# artifact captured from that material and served by /api/artifacts/{id}/audio.

class CapabilityState(StrEnum):
    REAL = "REAL"
    PARTIAL = "PARTIAL"
    UNCERTIFIED = "UNCERTIFIED"
    MISSING = "MISSING"


class ProduceCapability(BaseModel):
    name: str
    state: CapabilityState
    detail: str


class VariationPreview(BaseModel):
    artifact_id: str
    bars: int
    duration_s: float
    capture_region_id: str | None = None

    @property
    def audio_url(self) -> str:
        return f"/api/artifacts/{self.artifact_id}/audio"


class VariationRecord(BaseModel):
    variation_id: str
    project_id: str
    request_id: str
    index: int
    status: str  # PLANNED | CREATING_IN_ABLETON | CAPTURING | READY | KEPT | DISCARDED | FAILED
    ableton_track_ref: str | None = None
    ableton_clip_ref: str | None = None
    preview: VariationPreview | None = None
    plan_id: str | None = None
    ownership: dict[str, Any] = Field(default_factory=dict)
    safe_write: dict[str, Any] = Field(default_factory=dict)
    region: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ProduceRequest(BaseModel):
    scope: str  # TRACK | REGION
    instruction: str
    variations: int  # 1 | 3 | 5
    length_bars: int | None = None  # 8 | 16 | 32 | None == Auto
    start_qn: float = 0.0
    end_qn: float | None = None
    reference_analysis_path: str | None = None
    astra_interpretation_path: str | None = None
