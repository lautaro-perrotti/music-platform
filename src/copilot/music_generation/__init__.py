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
from copilot.music_generation.benchmark import (
    BestOfNReport,
    BlindBenchmarkReport,
    ProviderComparisonReport,
    CandidateRecord,
    DuplicateRelation,
    TechnicalValidation,
    analyze_candidate,
    freeze_quality_baseline,
    run_best_of_n,
    resume_best_of_n_after_runtime_recovery,
    build_blind_benchmark,
    build_provider_comparison_benchmark,
    persist_human_evaluation,
    validate_generated_audio,
)
from copilot.music_generation.resources import ExecutionRoute, StorageVolume, WorkerResources, choose_execution_route, discover_worker_resources
from copilot.music_generation.generated_asset_import import StagedGeneratedAsset, build_generated_asset_load_plan, stage_generated_asset
from copilot.music_generation.elevenlabs import ElevenLabsMusicProvider, build_elevenlabs_composition_plan
from copilot.music_generation.ace_cloud import AceStepCloudProvider
from copilot.music_generation.executive_producer import ExecutiveProducerAdapter, ExecutiveProducerContext, ExecutiveProducerDecision, GeneratorEditIntent, ProductionRefinementIntent

__all__ = [
    "GeneratedAsset",
    "GenerationBatch",
    "GenerationBrief",
    "GeneratorCapability",
    "GeneratorHealth",
    "GeneratorRequest",
    "MusicGeneratorRegistry",
    "BestOfNReport",
    "BlindBenchmarkReport",
    "ProviderComparisonReport",
    "CandidateRecord",
    "DuplicateRelation",
    "TechnicalValidation",
    "analyze_candidate",
    "freeze_quality_baseline",
    "run_best_of_n",
    "resume_best_of_n_after_runtime_recovery",
    "build_blind_benchmark",
    "build_provider_comparison_benchmark",
    "persist_human_evaluation",
    "validate_generated_audio",
    "RightsManifest",
    "ExecutionRoute",
    "StorageVolume",
    "WorkerResources",
    "choose_execution_route",
    "discover_worker_resources",
    "StagedGeneratedAsset",
    "build_generated_asset_load_plan",
    "stage_generated_asset",
    "ElevenLabsMusicProvider",
    "build_elevenlabs_composition_plan",
    "AceStepCloudProvider",
    "ExecutiveProducerAdapter",
    "ExecutiveProducerContext",
    "ExecutiveProducerDecision",
    "GeneratorEditIntent",
    "ProductionRefinementIntent",
]
