"""External source ingestion and specialist source-separation boundaries."""

from copilot.music_source.ingestion import (
    ExternalSourceCandidate,
    ExternalSourceDiscovery,
    ExternalSourceManifest,
    SourceDiscoveryStatus,
    discover_external_source,
    ingest_external_source,
)
from copilot.music_source.separation import (
    BSRoFormerInferProvider,
    SeparationBatch,
    SeparationRequest,
    SeparationStatus,
    SeparatedStem,
    SeparatorDescriptor,
)

__all__ = [
    "ExternalSourceCandidate",
    "ExternalSourceDiscovery",
    "ExternalSourceManifest",
    "SourceDiscoveryStatus",
    "discover_external_source",
    "ingest_external_source",
    "BSRoFormerInferProvider",
    "SeparationBatch",
    "SeparationRequest",
    "SeparationStatus",
    "SeparatedStem",
    "SeparatorDescriptor",
]
