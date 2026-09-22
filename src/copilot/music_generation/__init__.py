"""Provider-neutral dense music generation domain."""

from copilot.music_generation.schemas import (
    GeneratedAsset,
    GenerationBatch,
    GenerationBrief,
    GeneratorCapability,
    GeneratorHealth,
    GeneratorRequest,
    RightsManifest,
)
from copilot.music_generation.registry import MusicGeneratorRegistry

__all__ = [
    "GeneratedAsset",
    "GenerationBatch",
    "GenerationBrief",
    "GeneratorCapability",
    "GeneratorHealth",
    "GeneratorRequest",
    "MusicGeneratorRegistry",
    "RightsManifest",
]

