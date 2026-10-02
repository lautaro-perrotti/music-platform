"""Reusable inferred chord events, independent of a particular instrument."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ChordEvent(BaseModel):
    start_qn: float = Field(ge=0)
    duration_qn: float = Field(gt=0)
    root: str
    quality: str
    pitch_classes: list[str]
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1)
    authority: str = "INFERRED"

    @model_validator(mode="after")
    def inferred_only(self) -> "ChordEvent":
        if self.authority != "INFERRED":
            raise ValueError("audio-derived chord event cannot claim human authority")
        return self


class HarmonicPhrase(BaseModel):
    source_start_s: float = Field(ge=0)
    source_end_s: float = Field(gt=0)
    tempo_bpm: float = Field(gt=0)
    chord_events: list[ChordEvent] = Field(default_factory=list)
    windows: list[dict] = Field(default_factory=list)
    source_refs: dict[str, str]
    limitations: list[str] = Field(default_factory=list)
    status: str = "INFERRED"

    @model_validator(mode="after")
    def span_and_evidence(self) -> "HarmonicPhrase":
        if self.source_end_s <= self.source_start_s:
            raise ValueError("invalid harmonic phrase span")
        if self.status not in {"INFERRED", "INSUFFICIENT_EVIDENCE"}:
            raise ValueError("harmonic phrase is not human-verified")
        return self
