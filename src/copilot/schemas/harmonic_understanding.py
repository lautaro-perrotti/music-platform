"""Typed, read-only harmonic evidence derived from authoritative sources."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from copilot.schemas.musical_understanding import TonalityHypothesis


class ChordHypothesis(BaseModel):
    root: str
    quality: str
    label: str
    pitch_classes: list[str] = Field(default_factory=list)
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)


class HarmonicWindow(BaseModel):
    window_id: str
    start_bar: float = Field(ge=0)
    end_bar: float = Field(gt=0)
    start_qn: float = Field(ge=0)
    end_qn: float = Field(gt=0)
    pitch_class_energy: dict[str, float] = Field(default_factory=dict)
    bass_pitch_classes: list[str] = Field(default_factory=list)
    hypotheses: list[ChordHypothesis] = Field(default_factory=list)
    selected: ChordHypothesis | None = None
    status: str = "INSUFFICIENT_EVIDENCE"
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "HarmonicWindow":
        if self.end_bar <= self.start_bar or self.end_qn <= self.start_qn:
            raise ValueError("harmonic window must have a positive span")
        return self


class HarmonicRhythmSegment(BaseModel):
    start_bar: float = Field(ge=0)
    end_bar: float = Field(gt=0)
    label: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "HarmonicRhythmSegment":
        if self.end_bar <= self.start_bar:
            raise ValueError("harmonic rhythm segment must have a positive span")
        return self


class BassHarmonyRelationship(BaseModel):
    event_id: str
    bass_pitch_class: str
    window_id: str
    chord_label: str
    role: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class HarmonicUnderstanding(BaseModel):
    schema_version: str = "harmonic-understanding-v1"
    reference_id: str
    source_analysis_id: str
    tempo_bpm: float = Field(gt=0)
    source_kind: str
    windows: list[HarmonicWindow] = Field(default_factory=list)
    harmonic_rhythm: list[HarmonicRhythmSegment] = Field(default_factory=list)
    tonal_hypotheses: list[TonalityHypothesis] = Field(default_factory=list)
    selected_tonality: TonalityHypothesis | None = None
    bass_harmony_relationships: list[BassHarmonyRelationship] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True

    @model_validator(mode="after")
    def fail_closed(self) -> "HarmonicUnderstanding":
        if not self.no_write:
            raise ValueError("harmonic understanding must be NO_WRITE")
        return self


__all__ = [
    "BassHarmonyRelationship",
    "ChordHypothesis",
    "HarmonicRhythmSegment",
    "HarmonicUnderstanding",
    "HarmonicWindow",
]
