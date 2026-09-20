"""Contracts for deterministic reference-track analysis.

Reference analysis is evidence production only.  It never carries raw audio and
it never grants permission to mutate the target project.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ReferenceStateTokens(BaseModel):
    reference_state_token: str
    target_state_token: str

    @model_validator(mode="after")
    def tokens_must_be_distinct(self) -> "ReferenceStateTokens":
        if self.reference_state_token == self.target_state_token:
            raise ValueError("REFERENCE_STATE_TOKEN and TARGET_STATE_TOKEN must differ")
        return self


class ReferenceWindowEvidence(BaseModel):
    """Factual measurements for one aligned musical window."""

    start_beat: float = Field(ge=0)
    end_beat: float = Field(gt=0)
    section_label: str = "UNKNOWN"
    energy_db: float | None = None
    lufs: float | None = None
    crest_factor_db: float | None = None
    low_band_energy: float | None = Field(default=None, ge=0)
    spectral_centroid_hz: float | None = Field(default=None, ge=0)
    decay_ms: float | None = Field(default=None, ge=0)
    tempo_bpm: float | None = Field(default=None, gt=0)
    key: str | None = None
    event_locations: list[float] = Field(default_factory=list)
    overlap_durations: dict[str, float] = Field(default_factory=dict)
    decay_trajectory: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "ReferenceWindowEvidence":
        if self.end_beat <= self.start_beat:
            raise ValueError("reference window must have positive duration")
        if any(value < self.start_beat or value > self.end_beat for value in self.event_locations):
            raise ValueError("event location must fall inside its reference window")
        if any(value < 0 for value in self.overlap_durations.values()):
            raise ValueError("overlap durations cannot be negative")
        return self


class ReferenceAnalysisPack(BaseModel):
    """Astra-facing reference evidence; raw WAV/audio payloads are forbidden."""

    schema_version: str = "reference-analysis-v1"
    tokens: ReferenceStateTokens
    window_beats: float = Field(default=128.0, gt=0)
    windows: list[ReferenceWindowEvidence] = Field(default_factory=list)
    no_write: bool = True
    raw_audio_included: bool = False

    @model_validator(mode="after")
    def fail_closed_for_audio_or_writes(self) -> "ReferenceAnalysisPack":
        if not self.no_write:
            raise ValueError("reference analysis packs must be NO_WRITE")
        if self.raw_audio_included:
            raise ValueError("raw audio cannot be included in EvidencePack")
        return self
