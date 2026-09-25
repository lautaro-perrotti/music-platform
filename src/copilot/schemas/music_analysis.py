"""Typed, read-only evidence for MUSIC_ANALYZER_V1.

This layer packages factual analyzer output for Astra.  It is deliberately
separate from ``MusicPlan``: analysis and planning do not authorize writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from copilot.schemas.reference_analysis import ReferenceStateTokens


class AudioAnalysisInput(BaseModel):
    """Common ingest boundary for reference files and project captures."""

    main_path: Path
    reference_state_token: str
    target_state_token: str
    tempo_bpm: float = Field(gt=0)
    mode: str = "WHOLE_TRACK"
    bar_start: int | None = Field(default=None, ge=1)
    bar_end: int | None = Field(default=None, ge=1)
    source_paths: dict[str, Path] = Field(default_factory=dict)
    project_identity: str | None = None
    capture_id: str | None = None

    @model_validator(mode="after")
    def distinct_state_tokens(self) -> "AudioAnalysisInput":
        if self.reference_state_token == self.target_state_token:
            raise ValueError("reference and target state tokens must remain distinct")
        if (self.bar_start is None) != (self.bar_end is None):
            raise ValueError("bar_start and bar_end must be provided together")
        if self.bar_start is not None and self.bar_end < self.bar_start:
            raise ValueError("bar_end must be at or after bar_start")
        if self.mode not in {"WHOLE_TRACK", "SELECTED_REGION"}:
            raise ValueError("unsupported reference analysis mode")
        if self.mode == "SELECTED_REGION" and self.bar_start is None:
            raise ValueError("selected-region input requires bar_start and bar_end")
        if self.mode == "WHOLE_TRACK" and self.bar_start is not None:
            raise ValueError("whole-track input cannot include a bar range")
        return self


class SectionEvidence(BaseModel):
    name: str
    start_beat: float = Field(ge=0)
    end_beat: float = Field(gt=0)
    function: str = "UNKNOWN"
    energy_mean_db: float | None = None
    energy_slope_db_per_s: float | None = None
    contrast_db: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    active_sources: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "SectionEvidence":
        if self.end_beat <= self.start_beat:
            raise ValueError("section end must be after section start")
        return self


class StructuralRegion(BaseModel):
    """Observed region facts, deliberately independent from a label."""

    region_id: str
    start_beat: float = Field(ge=0)
    end_beat: float = Field(gt=0)
    energy_mean_db: float | None = None
    energy_slope_db_per_s: float | None = None
    contrast_db: float | None = None
    feature_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "StructuralRegion":
        if self.end_beat <= self.start_beat:
            raise ValueError("structural region end must be after start")
        return self


class SectionHypothesis(BaseModel):
    """A semantic interpretation of a structural region, never a fact."""

    region_id: str
    label: str = "UNKNOWN"
    confidence: float | None = Field(default=None, ge=0, le=1)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class GrooveEvidence(BaseModel):
    onset_count: int | None = Field(default=None, ge=0)
    onset_density_per_s: float | None = Field(default=None, ge=0)
    median_ioi_s: float | None = Field(default=None, ge=0)
    swing_ratio: float | None = Field(default=None, ge=0)
    offbeat_ratio: float | None = Field(default=None, ge=0, le=1)
    syncopation_proxy: float | None = Field(default=None, ge=0, le=1)
    repetition_strength: float | None = Field(default=None, ge=0, le=1)
    variation_score: float | None = Field(default=None, ge=0, le=1)
    event_locations: list[float] = Field(default_factory=list)


class HarmonyEvidence(BaseModel):
    key_candidate: str | None = None
    key_confidence: float | None = Field(default=None, ge=0, le=1)
    scale_candidate: str | None = None
    chroma_profile: list[float] = Field(default_factory=list)


class TimbreEvidence(BaseModel):
    spectral_centroid_hz: float | None = Field(default=None, ge=0)
    spectral_rolloff_hz: float | None = Field(default=None, ge=0)
    spectral_flatness: float | None = Field(default=None, ge=0)
    zero_crossing_rate: float | None = Field(default=None, ge=0)


class TextureEvidence(BaseModel):
    active_source_count: int | None = Field(default=None, ge=0)
    active_sources: list[str] = Field(default_factory=list)
    overlap_durations: dict[str, float] = Field(default_factory=dict)
    relative_low_band_energy: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def non_negative_overlaps(self) -> "TextureEvidence":
        if any(value < 0 for value in self.overlap_durations.values()):
            raise ValueError("texture overlap durations cannot be negative")
        return self


class ProminenceEvidence(BaseModel):
    source_scores: dict[str, float] = Field(default_factory=dict)
    method: str = "UNAVAILABLE"


class SourceActivityEvidence(BaseModel):
    source: str
    start_beat: float = Field(ge=0)
    end_beat: float = Field(gt=0)
    activity: str = "UNKNOWN"
    relative_energy: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_span(self) -> "SourceActivityEvidence":
        if self.end_beat <= self.start_beat:
            raise ValueError("source activity end must be after start")
        return self


class TransitionEvidence(BaseModel):
    start_beat: float = Field(ge=0)
    end_beat: float = Field(gt=0)
    kind: str = "UNKNOWN"
    energy_delta_db: float | None = None
    event_locations: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "TransitionEvidence":
        if self.end_beat <= self.start_beat:
            raise ValueError("transition end must be after start")
        return self


class MusicAnalysisWindow(BaseModel):
    """Facts for one 32-bar reference window."""

    index: int = Field(ge=0)
    start_beat: float = Field(ge=0)
    end_beat: float = Field(gt=0)
    section_label: str = "UNKNOWN"
    energy_db: float | None = None
    lufs: float | None = None
    crest_factor_db: float | None = None
    low_band_energy: float | None = Field(default=None, ge=0)
    kick_energy: float | None = Field(default=None, ge=0)
    bass_energy: float | None = Field(default=None, ge=0)
    kick_bass_overlap_ratio: float | None = Field(default=None, ge=0, le=1)
    kick_bass_overlap_duration_s: float | None = Field(default=None, ge=0)
    lowend_measurement_status: str = "MASTER_ONLY"
    decay_trajectory: list[float] = Field(default_factory=list)
    groove: GrooveEvidence = Field(default_factory=GrooveEvidence)
    harmony: HarmonyEvidence = Field(default_factory=HarmonyEvidence)
    timbre: TimbreEvidence = Field(default_factory=TimbreEvidence)
    texture: TextureEvidence = Field(default_factory=TextureEvidence)
    prominence: ProminenceEvidence = Field(default_factory=ProminenceEvidence)
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "MusicAnalysisWindow":
        if self.end_beat <= self.start_beat:
            raise ValueError("analysis window end must be after start")
        if any(value < self.start_beat or value > self.end_beat for value in self.groove.event_locations):
            raise ValueError("groove event location outside analysis window")
        return self


class MusicAnalysisPack(BaseModel):
    """Astra-facing reference evidence; never a write authorization."""

    schema_version: str = "music-analysis-v1"
    tokens: ReferenceStateTokens
    reference_id: str | None = None
    project_id: str | None = None
    source_ref: dict[str, Any] = Field(default_factory=dict)
    mode: str = "WHOLE_TRACK"
    timeline: dict[str, Any] = Field(default_factory=dict)
    tempo_bpm: float = Field(gt=0)
    window_bars: int = Field(default=32, ge=1)
    windows: list[MusicAnalysisWindow] = Field(default_factory=list)
    structural_regions: list[StructuralRegion] = Field(default_factory=list)
    section_hypotheses: list[SectionHypothesis] = Field(default_factory=list)
    sections: list[SectionEvidence] = Field(default_factory=list)
    source_activity: list[SourceActivityEvidence] = Field(default_factory=list)
    transitions: list[TransitionEvidence] = Field(default_factory=list)
    analyzer_ids: dict[str, str] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    no_write: bool = True
    raw_audio_included: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fail_closed(self) -> "MusicAnalysisPack":
        if not self.no_write:
            raise ValueError("music analysis packs must be NO_WRITE")
        if self.raw_audio_included:
            raise ValueError("raw audio cannot be included in MusicAnalysisPack")
        return self
