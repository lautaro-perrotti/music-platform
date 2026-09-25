"""Typed, deterministic symbolic model for an analyzed bass line."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from copilot.schemas.musical_understanding import BassDrumsRelationship, TonalityHypothesis


class PitchClassMaterial(BaseModel):
    pitch_class: str
    midi_note_count: int = Field(ge=0)
    duration_qn: float = Field(ge=0)
    duration_ratio: float = Field(ge=0, le=1)
    first_onset_qn: float = Field(ge=0)
    last_onset_qn: float = Field(ge=0)


class IntervalLanguage(BaseModel):
    total_intervals: int = Field(ge=0)
    semitone_histogram: dict[str, int] = Field(default_factory=dict)
    interval_class_histogram: dict[str, int] = Field(default_factory=dict)
    direction_histogram: dict[str, int] = Field(default_factory=dict)
    repeated_note_ratio: float = Field(ge=0, le=1)
    stepwise_ratio: float = Field(ge=0, le=1)
    leap_ratio: float = Field(ge=0, le=1)
    largest_absolute_interval: int = Field(ge=0)
    evidence_refs: list[str] = Field(default_factory=list)


class RhythmicCell(BaseModel):
    cell_id: str
    onset_offsets_qn: list[float] = Field(default_factory=list)
    duration_buckets: list[str] = Field(default_factory=list)
    event_count: int = Field(ge=0)
    occurrence_count: int = Field(ge=1)
    phrase_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class BassMotif(BaseModel):
    motif_id: str
    signature: list[str] = Field(default_factory=list)
    phrase_ids: list[str] = Field(default_factory=list)
    occurrence_count: int = Field(ge=1)
    event_count: int = Field(ge=0)
    relation_to_first: str = "UNKNOWN"
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class BassPhraseModel(BaseModel):
    phrase_id: str
    start_bar: float = Field(ge=0)
    end_bar: float = Field(gt=0)
    structural_label: str = "UNKNOWN"
    event_count: int = Field(ge=0)
    density_per_bar: float = Field(ge=0)
    pitch_classes: list[str] = Field(default_factory=list)
    rhythmic_cell_ids: list[str] = Field(default_factory=list)
    motif_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "BassPhraseModel":
        if self.end_bar <= self.start_bar:
            raise ValueError("phrase end must be after phrase start")
        return self


class BassMusicalModel(BaseModel):
    schema_version: str = "bass-musical-model-v1"
    reference_id: str
    source_analysis_id: str
    source_kind: str
    event_count: int = Field(ge=0)
    pitch_material: list[PitchClassMaterial] = Field(default_factory=list)
    interval_language: IntervalLanguage
    rhythmic_cells: list[RhythmicCell] = Field(default_factory=list)
    motifs: list[BassMotif] = Field(default_factory=list)
    phrases: list[BassPhraseModel] = Field(default_factory=list)
    tonal_hypotheses: list[TonalityHypothesis] = Field(default_factory=list)
    selected_tonality: TonalityHypothesis | None = None
    bass_drums_relationship: BassDrumsRelationship | None = None
    limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True

    @model_validator(mode="after")
    def fail_closed(self) -> "BassMusicalModel":
        if not self.no_write:
            raise ValueError("bass musical model must be NO_WRITE")
        return self
