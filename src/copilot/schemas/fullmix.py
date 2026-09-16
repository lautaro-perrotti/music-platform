from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EnergyEventKind(StrEnum):
    SILENCE = "SILENCE"
    NEAR_SILENCE = "NEAR_SILENCE"
    STRONG_ENERGY_DIP = "STRONG_ENERGY_DIP"


class FullMixBand(StrEnum):
    SUB = "SUB"
    LOW = "LOW"
    LOW_MID = "LOW_MID"
    MID = "MID"
    HIGH_MID = "HIGH_MID"
    HIGH = "HIGH"


class TimeRange(BaseModel):
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, float(self.end_s) - float(self.start_s))


class EnergyFrame(BaseModel):
    t_s: float
    rms: float
    peak: float
    relative_db: float
    reference_rms: float
    crest_factor: float | None = None


class EnergyEvent(BaseModel):
    kind: EnergyEventKind
    start_s: float
    end_s: float
    duration_s: float
    minimum_rms: float
    reference_rms: float
    relative_drop_db: float
    quality: str = "OK"
    event_similarity_count: int = 0
    approx_period_s: float | None = None
    repetition_strength: float | None = None
    limitations: list[str] = Field(default_factory=list)


class SpectralBandPoint(BaseModel):
    t_s: float
    bands: dict[str, float]


class SpectralEvent(BaseModel):
    start_s: float
    end_s: float
    band: str
    relative_change_db: float
    reference_energy: float
    measured_energy: float
    quality: str = "OK"
    limitations: list[str] = Field(default_factory=list)


class TransientObservation(BaseModel):
    window_start_s: float
    window_end_s: float
    transient_count: int
    transient_density_per_s: float
    mean_ioi_s: float | None = None
    median_ioi_s: float | None = None
    precision_ms: float
    quality: str = "OK"
    limitations: list[str] = Field(default_factory=list)


class StereoObservation(BaseModel):
    t_s: float
    left_rms: float
    right_rms: float
    correlation: float | None = None
    mid_energy: float | None = None
    side_energy: float | None = None
    side_mid_ratio: float | None = None
    quality: str = "OK"
    limitations: list[str] = Field(default_factory=list)


class DynamicObservation(BaseModel):
    window_start_s: float
    window_end_s: float
    crest_factor: float | None = None
    short_term_dynamic_range_db: float | None = None
    peak: float
    rms: float
    quality: str = "OK"
    limitations: list[str] = Field(default_factory=list)


class FullMixObservation(BaseModel):
    """Factual Main-mix observation. MEASURE only. No musical judgment."""

    analyzer_id: str
    analyzer_sha256: str
    configuration_hash: str
    audio_sha256: str
    audio_path: str
    region_id: str
    region_label: str
    sample_rate: int
    channels: int
    duration_s: float
    window_s: float
    hop_s: float
    thresholds: dict[str, Any]
    energy_frames: list[EnergyFrame] = Field(default_factory=list)
    energy_events: list[EnergyEvent] = Field(default_factory=list)
    spectral_trajectory: list[SpectralBandPoint] = Field(default_factory=list)
    spectral_events: list[SpectralEvent] = Field(default_factory=list)
    transient: TransientObservation | None = None
    stereo_frames: list[StereoObservation] = Field(default_factory=list)
    dynamics: list[DynamicObservation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    cache_hit: bool = False
