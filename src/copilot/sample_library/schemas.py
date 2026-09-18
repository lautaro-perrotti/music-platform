"""Typed contracts for the sample-library intelligence layer."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SampleType(StrEnum):
    ONE_SHOT = "ONE_SHOT"
    LOOP = "LOOP"
    UNKNOWN = "UNKNOWN"


class SampleRole(StrEnum):
    KICK = "KICK"
    SNARE = "SNARE"
    CLAP = "CLAP"
    RIM = "RIM"
    CLOSED_HAT = "CLOSED_HAT"
    OPEN_HAT = "OPEN_HAT"
    RIDE = "RIDE"
    SHAKER = "SHAKER"
    PERCUSSION = "PERCUSSION"
    TOP_LOOP = "TOP_LOOP"
    DRUM_LOOP = "DRUM_LOOP"
    BASS = "BASS"
    VOCAL = "VOCAL"
    VOCAL_CHOP = "VOCAL_CHOP"
    FX = "FX"
    IMPACT = "IMPACT"
    RISER = "RISER"
    DOWNLIFTER = "DOWNLIFTER"
    TEXTURE = "TEXTURE"
    SYNTH = "SYNTH"
    CHORD = "CHORD"
    MELODY = "MELODY"
    AMBIENCE = "AMBIENCE"
    UNKNOWN = "UNKNOWN"


class AssetStatus(StrEnum):
    INDEXED = "INDEXED"
    UNSUPPORTED = "UNSUPPORTED"
    DECODE_FAILED = "DECODE_FAILED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    EMBEDDING_FAILED = "EMBEDDING_FAILED"
    MISSING = "MISSING"


class AudioDescriptors(BaseModel):
    duration_s: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    rms: float | None = None
    peak: float | None = None
    crest_factor: float | None = None
    spectral_centroid_hz: float | None = None
    low_band_energy: float | None = None
    mid_band_energy: float | None = None
    high_band_energy: float | None = None
    transient_strength: float | None = None
    stereo_width: float | None = None
    silence_ratio: float | None = None


class BpmEstimate(BaseModel):
    value: float | None = None
    confidence: float | None = None


class PitchEstimate(BaseModel):
    value: str | None = None
    confidence: float | None = None


class EmbeddingRef(BaseModel):
    provider: str
    model: str
    version: str
    reference: str | None = None  # id/vector ref, not necessarily inline vector


class SampleAsset(BaseModel):
    id: str
    path: str
    filename: str
    library_root: str
    relative_path: str
    extension: str
    size_bytes: int
    mtime_ns: int | None = None
    sha256: str
    sample_type: SampleType = SampleType.UNKNOWN
    semantic_role: SampleRole = SampleRole.UNKNOWN
    bpm: BpmEstimate = Field(default_factory=BpmEstimate)
    pitch: PitchEstimate = Field(default_factory=PitchEstimate)
    descriptors: AudioDescriptors = Field(default_factory=AudioDescriptors)
    embedding: EmbeddingRef | None = None
    classification_confidence: float | None = None
    tags: list[str] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)
    status: AssetStatus = AssetStatus.INDEXED
    error: str | None = None


class LibraryIndex(BaseModel):
    version: int = 1
    analysis_version: str = "sample-library-v1"
    roots: list[str] = Field(default_factory=list)
    assets: dict[str, SampleAsset] = Field(default_factory=dict)  # keyed by sha256
    duplicates: dict[str, list[str]] = Field(default_factory=dict)  # sha256 -> extra paths
    path_meta: dict[str, dict] = Field(default_factory=dict)  # path -> {size_bytes, mtime_ns}
    created_at: str | None = None
    updated_at: str | None = None


class SearchResult(BaseModel):
    asset: SampleAsset
    score: float
    reasons: list[str] = Field(default_factory=list)


class SampleSetContext(BaseModel):
    """Compact candidate shortlist for Astra. Not the full library."""

    task_id: str
    roles: dict[str, list[dict]] = Field(default_factory=dict)  # role -> candidate summaries
    candidates: list[dict] = Field(default_factory=list)
    selection_reasons: dict[str, str] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    asset: SampleAsset
    score: float
    reasons: list[str] = Field(default_factory=list)
    descriptor_distance: float | None = None
