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
from copilot.music_generation.resources import ExecutionRoute, StorageVolume, WorkerResources, choose_execution_route, discover_worker_resources

__all__ = [
    "GeneratedAsset",
    "GenerationBatch",
    "GenerationBrief",
    "GeneratorCapability",
    "GeneratorHealth",
    "GeneratorRequest",
    "MusicGeneratorRegistry",
    "RightsManifest",
    "ExecutionRoute",
    "StorageVolume",
    "WorkerResources",
    "choose_execution_route",
    "discover_worker_resources",
]
