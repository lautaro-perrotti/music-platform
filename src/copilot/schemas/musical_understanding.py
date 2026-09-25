"""Typed, deterministic musical facts derived from immutable reference stems.

This schema is intentionally descriptive.  It is not a planner, a diagnosis,
or an authorization to write to a DAW.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class MusicalGridPoint(BaseModel):
    onset_s: float = Field(ge=0)
    onset_qn: float = Field(ge=0)
    bar: float = Field(ge=0)
    beat_in_bar: float = Field(ge=0)
    subdivision: str
    nearest_grid_qn: float = Field(ge=0)
    deviation_qn: float
    deviation_ms: float
    evidence_refs: list[str] = Field(default_factory=list)


class BassPitchEvent(BaseModel):
    event_id: str
    grid: MusicalGridPoint
    offset_s: float = Field(gt=0)
    f0_hz: float | None = Field(default=None, gt=0)
    midi_float: float | None = None
    midi_note: int | None = Field(default=None, ge=0, le=127)
    pitch_class: str | None = None
    confidence: float = Field(ge=0, le=1)
    status: str = "RELIABLE"
    evidence_refs: list[str] = Field(default_factory=list)


class TonalityHypothesis(BaseModel):
    tonic: str
    mode: str
    scale: list[str] = Field(default_factory=list)
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)


class IntervalEvidence(BaseModel):
    from_event_id: str
    to_event_id: str
    semitones: int
    direction: str
    interval_class: int = Field(ge=0, le=6)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)


class PeriodicityCandidate(BaseModel):
    period_bars: int = Field(ge=1)
    period_qn: float = Field(gt=0)
    strength: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)


class RhythmicStructure(BaseModel):
    event_count: int = Field(ge=0)
    density_per_bar: float = Field(ge=0)
    median_duration_qn: float | None = Field(default=None, ge=0)
    ioi_qn: list[float] = Field(default_factory=list)
    offbeat_ratio: float | None = Field(default=None, ge=0, le=1)
    median_timing_deviation_ms: float | None = None
    periodicity_candidates: list[PeriodicityCandidate] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class MotifPhraseEvidence(BaseModel):
    phrase_id: str
    start_bar: float = Field(ge=0)
    end_bar: float = Field(gt=0)
    structural_label: str = "UNKNOWN"
    similarity_to_first: float | None = Field(default=None, ge=0, le=1)
    event_count: int = Field(ge=0)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "MotifPhraseEvidence":
        if self.end_bar <= self.start_bar:
            raise ValueError("phrase end must be after phrase start")
        return self


class BassUnderstanding(BaseModel):
    status: str = "SUPPORTED"
    pitch_events: list[BassPitchEvent] = Field(default_factory=list)
    pitch_classes: dict[str, float] = Field(default_factory=dict)
    tonality_status: str = "INSUFFICIENT_EVIDENCE"
    tonality: list[TonalityHypothesis] = Field(default_factory=list)
    selected_tonality: TonalityHypothesis | None = None
    scale_degrees: list[int] = Field(default_factory=list)
    intervals: list[IntervalEvidence] = Field(default_factory=list)
    rhythmic_structure: RhythmicStructure
    motifs: list[MotifPhraseEvidence] = Field(default_factory=list)
    phrase_structure: list[MotifPhraseEvidence] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DrumTransientEvent(BaseModel):
    event_id: str
    grid: MusicalGridPoint
    strength: float = Field(ge=0)
    evidence_refs: list[str] = Field(default_factory=list)


class PulseStructure(BaseModel):
    dominant_subdivisions: list[str] = Field(default_factory=list)
    periodicity_candidates: list[PeriodicityCandidate] = Field(default_factory=list)
    offbeat_ratio: float | None = Field(default=None, ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)


class DrumsUnderstanding(BaseModel):
    status: str = "SUPPORTED"
    transient_grid: list[DrumTransientEvent] = Field(default_factory=list)
    pulse_structure: PulseStructure
    rhythmic_structure: RhythmicStructure
    repetition: list[PeriodicityCandidate] = Field(default_factory=list)
    local_variations: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class BassDrumsRelationship(BaseModel):
    bass_event_count: int = Field(ge=0)
    drum_event_count: int = Field(ge=0)
    coincidence_count: int = Field(ge=0)
    coincidence_ratio: float = Field(ge=0, le=1)
    displacement_ms: list[float] = Field(default_factory=list)
    median_displacement_ms: float | None = None
    relationship_status: str = "DESCRIPTIVE_ONLY"
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class MusicalUnderstanding(BaseModel):
    schema_version: str = "musical-understanding-v1"
    reference_id: str
    source_analysis_id: str
    stem_analysis_id: str
    tempo_bpm: float = Field(gt=0)
    timeline: dict[str, Any]
    bass: BassUnderstanding
    drums: DrumsUnderstanding
    relationships: dict[str, BassDrumsRelationship]
    global_limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True
    raw_audio_included: bool = False

    @model_validator(mode="after")
    def fail_closed(self) -> "MusicalUnderstanding":
        if not self.no_write:
            raise ValueError("musical understanding must be NO_WRITE")
        if self.raw_audio_included:
            raise ValueError("raw audio cannot be embedded in musical understanding")
        return self

