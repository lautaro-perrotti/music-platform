from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ClaimKind(StrEnum):
    MEASURED = "MEASURED"
    INFERRED = "INFERRED"
    HYPOTHESIS = "HYPOTHESIS"


class Claim(BaseModel):
    kind: ClaimKind
    name: str
    value: Any
    confidence: float = 1.0
    unit: str | None = None
    notes: str | None = None


class SignalFeatures(BaseModel):
    duration_seconds: float
    sample_rate: int
    channels: int
    rms: float
    peak: float
    true_peak: float | None = None
    lufs: float | None = None
    crest_factor: float | None = None
    spectral_centroid_hz: float | None = None
    bass_energy_ratio: float | None = None
    stereo_width: float | None = None


class CaptureView(StrEnum):
    MASTER_CONTEXT = "MASTER_CONTEXT"
    TRACK_ISOLATED = "TRACK_ISOLATED"
    TRACK_CONTEXT_REMOVAL = "TRACK_CONTEXT_REMOVAL"


class ObservationSource(StrEnum):
    """Deprecated alias of CaptureView. Do not add new values here."""

    MASTER_CONTEXT = "MASTER_CONTEXT"
    TRACK_ISOLATED = "TRACK_ISOLATED"
    TRACK_IN_MIX_CONTEXT = "TRACK_IN_MIX_CONTEXT"
    TRACK_CONTEXT_REMOVAL = "TRACK_CONTEXT_REMOVAL"


class SignalPoint(StrEnum):
    MAIN_FINAL = "MAIN_FINAL"
    MAIN_NOT_FINAL = "MAIN_NOT_FINAL"
    TRACK_POST_MIXER = "TRACK_POST_MIXER"
    TRACK_POST_MIXER_THROUGH_MASTER_CHAIN = "TRACK_POST_MIXER_THROUGH_MASTER_CHAIN"
    PRE_FX = "PRE_FX"
    POST_FX = "POST_FX"
    MASTER = "MASTER"
    POST_MIXER = "POST_MIXER"
    UNKNOWN = "UNKNOWN"


class TailPolicy(StrEnum):
    STRICT_REGION = "STRICT_REGION"
    CONTEXT_WITH_TAIL = "CONTEXT_WITH_TAIL"


class MusicObservation(BaseModel):
    source: str
    region: str
    timestamp: str
    signal: SignalFeatures
    claims: list[Claim] = Field(default_factory=list)
    tempo_bpm: float | None = None
    key: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)
    capture_view: CaptureView | None = None
    observation_source: ObservationSource | None = None
    signal_point: SignalPoint | None = None
    signal_point_label: str | None = None
    tail_policy: TailPolicy = TailPolicy.STRICT_REGION
    evidence_id: str | None = None
    analysis_version: str | None = None
    source_asset: str | None = None
    quality: str | None = None
    limitations: list[str] = Field(default_factory=list)
    project_token: str | None = None
    audible_token: str | None = None
    target_token: str | None = None

    def measured(self, name: str) -> Claim | None:
        for claim in self.claims:
            if claim.name == name and claim.kind == ClaimKind.MEASURED:
                return claim
        return None
