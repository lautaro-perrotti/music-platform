"""Typed contracts for specialist music generators.

These contracts describe generated assets; they do not grant Ableton write
authority.  A generator can only create files inside an explicit output
directory and must return provenance before an asset is eligible for import.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class GeneratorCapability(StrEnum):
    TEXT_TO_MUSIC = "TEXT_TO_MUSIC"
    AUDIO_CONDITIONING = "AUDIO_CONDITIONING"
    REFERENCE_AUDIO = "REFERENCE_AUDIO"
    AUDIO_TO_AUDIO = "AUDIO_TO_AUDIO"
    REPAINT = "REPAINT"
    INPAINT = "INPAINT"
    EXTEND = "EXTEND"
    COVER = "COVER"
    STEM_OUTPUT = "STEM_OUTPUT"
    MIDI_OUTPUT = "MIDI_OUTPUT"
    SECTION_CONTROL = "SECTION_CONTROL"
    LONG_FORM = "LONG_FORM"
    INSTRUMENTAL = "INSTRUMENTAL"
    VOCALS = "VOCALS"
    LOCAL_INFERENCE = "LOCAL_INFERENCE"
    REMOTE_INFERENCE = "REMOTE_INFERENCE"


class GeneratorHealth(StrEnum):
    HEALTHY = "HEALTHY"
    CONFIGURED = "CONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class GeneratorFailureCode(StrEnum):
    GENERATOR_UNAVAILABLE = "GENERATOR_UNAVAILABLE"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    MODEL_MISSING = "MODEL_MISSING"
    HARDWARE_UNSUPPORTED = "HARDWARE_UNSUPPORTED"
    RIGHTS_BLOCKED = "RIGHTS_BLOCKED"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    INFERENCE_FAILED = "INFERENCE_FAILED"


class RightsClassification(StrEnum):
    COMMERCIAL_ALLOWED = "COMMERCIAL_ALLOWED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class RightsManifest(BaseModel):
    source_audio_ownership: str = "UNKNOWN"
    remote_upload_allowed: bool = False
    transformation_allowed: bool = False
    training_allowed: bool = False
    output_use: RightsClassification = RightsClassification.UNKNOWN
    provenance: list[str] = Field(default_factory=list)


class ModelManifest(BaseModel):
    provider: str
    model_id: str
    revision: str | None = None
    checkpoint_path: str | None = None
    license: str = "UNKNOWN"
    license_source: str | None = None
    quantization: str | None = None


class GenerationBrief(BaseModel):
    brief_id: str
    user_intent: str
    source_project_context_ref: str | None = None
    reference_context_refs: list[str] = Field(default_factory=list)
    preference_context_refs: list[str] = Field(default_factory=list)
    target_duration_s: float = Field(gt=0)
    tempo_bpm: float | None = Field(default=None, gt=0)
    meter: str | None = None
    key_context: str | None = None
    instrumental: bool = True
    lyrics: str | None = None
    language: str | None = None
    structural_intent: list[str] = Field(default_factory=list)
    energy_intent: str | None = None
    groove_intent: str | None = None
    density_intent: str | None = None
    preserve_constraints: list[str] = Field(default_factory=list)
    change_constraints: list[str] = Field(default_factory=list)
    negative_constraints: list[str] = Field(default_factory=list)
    requested_outputs: list[str] = Field(default_factory=lambda: ["stereo"])
    candidate_count: int = Field(default=1, ge=1, le=16)
    seed_policy: str = "explicit_per_candidate"
    generation_mode: str = "text_to_music"
    reference_conditioning_policy: str = "analysis_only_unless_rights_allow_upload"
    rights_manifest: RightsManifest = Field(default_factory=RightsManifest)
    no_write: bool = True


class GeneratorRequest(BaseModel):
    request_id: str
    brief: GenerationBrief
    seed: int
    output_dir: Path
    model_manifest: ModelManifest | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    no_ableton_access: bool = True


class PerformanceManifest(BaseModel):
    device: str
    precision: str | None = None
    latency_s: float | None = None
    actual_duration_s: float | None = None
    sample_rate: int | None = None


class GeneratedAsset(BaseModel):
    asset_id: str
    path: Path
    kind: str = "stereo"
    sha256: str
    bytes: int = Field(ge=0)
    duration_s: float | None = None
    sample_rate: int | None = None
    non_silent: bool
    model: ModelManifest
    seed: int
    prompt: str
    performance: PerformanceManifest
    rights_manifest: RightsManifest
    lineage: list[str] = Field(default_factory=list)
    no_ableton_access: bool = True


class GenerationBatch(BaseModel):
    batch_id: str
    status: str
    provider: str
    model: ModelManifest
    assets: list[GeneratedAsset] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    brief_id: str
    no_ableton_access: bool = True


class GeneratorDescriptor(BaseModel):
    provider_id: str
    model: ModelManifest
    capabilities: list[GeneratorCapability] = Field(default_factory=list)
    health: GeneratorHealth
    local_or_remote: str
    hardware_requirements: dict[str, Any] = Field(default_factory=dict)
    rights_classification: RightsClassification = RightsClassification.UNKNOWN
    runtime: str = "isolated"

