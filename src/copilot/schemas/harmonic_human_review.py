"""Typed contract for human review of harmonic evidence."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from copilot.schemas.musical_understanding import TonalityHypothesis

HumanVerdict = Literal[
    "PENDING",
    "ACCEPT",
    "PLAUSIBLE_AMBIGUOUS",
    "WRONG",
    "UNKNOWN_CORRECT",
    "UNKNOWN_SHOULD_RESOLVE",
]


class ReviewRegion(BaseModel):
    start_qn: float = Field(ge=0)
    end_qn: float = Field(gt=0)
    start_bar: float = Field(ge=0)
    end_bar: float = Field(gt=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def valid_span(self) -> "ReviewRegion":
        if self.end_qn <= self.start_qn or self.end_bar <= self.start_bar or self.end_seconds <= self.start_seconds:
            raise ValueError("review region must have a positive span")
        return self


class ReviewAudioArtifact(BaseModel):
    role: str
    path: str | None = None
    filename: str | None = None
    sha256: str | None = None
    available: bool = False
    non_silent: bool = False
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    source_hash_verified: bool | None = None
    limitation: str | None = None


class HarmonicAlternative(BaseModel):
    label: str
    root: str
    quality: str
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    observed_tones: list[str] = Field(default_factory=list)
    missing_or_inferred: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class BassRelationshipSummary(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)
    event_count: int = Field(ge=0)
    events: list[dict[str, Any]] = Field(default_factory=list)


class HarmonicHumanReviewWindow(BaseModel):
    window_id: str
    analysis_region: ReviewRegion
    listening_region: ReviewRegion
    audio_artifacts: dict[str, ReviewAudioArtifact] = Field(default_factory=dict)
    selected_hypothesis: HarmonicAlternative | None = None
    alternatives: list[HarmonicAlternative] = Field(default_factory=list)
    selected_runner_up_score_delta: float | None = None
    observed_pitch_classes: list[str] = Field(default_factory=list)
    bass_events: list[dict[str, Any]] = Field(default_factory=list)
    bass_harmony: BassRelationshipSummary
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    human_review_note: str | None = None
    human_verdict: HumanVerdict = "PENDING"
    human_notes: str = ""


class HarmonicHumanReview(BaseModel):
    schema_version: str = "harmonic-human-review-v1"
    analysis_artifact_id: str
    reference_id: str
    source_hash: str
    source_artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    review_windows: list[HarmonicHumanReviewWindow] = Field(default_factory=list)
    global_tonality_status: str
    global_tonality_candidates: list[TonalityHypothesis] = Field(default_factory=list)
    global_tonality_limitations: list[str] = Field(default_factory=list)
    review_status: str = "AWAITING_HUMAN_SANITY_CHECK"
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True
    model_api_calls: int = 0
    musical_writes: int = 0
    ableton_mutations: int = 0

    @model_validator(mode="after")
    def fail_closed(self) -> "HarmonicHumanReview":
        if not self.no_write:
            raise ValueError("human harmonic review must be NO_WRITE")
        if self.model_api_calls != 0 or self.musical_writes != 0 or self.ableton_mutations != 0:
            raise ValueError("human review package must be read-only and model-free")
        return self


__all__ = ["HarmonicHumanReview", "HarmonicHumanReviewWindow", "HumanVerdict"]
