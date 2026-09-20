"""Audio-embedding layer for the sample library (provider-replaceable).

SAMPLE_LIBRARY_INTELLIGENCE_V1 — section 9. The index must not be permanently
coupled to one embedding model. Preferred semantic provider is CLAP; until it
is installed, a deterministic, NON-semantic stub keeps the contract exercised
end-to-end without pretending to produce musical similarity.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from copilot.audio.file_hash import sha256_file


class Embedding(BaseModel):
    vector: list[float]
    dim: int
    provider: str
    model: str
    version: str


class EmbeddingProviderUnavailable(RuntimeError):
    """Raised when a provider (e.g. CLAP) is selected but not installed."""


class EmbeddingProvider(ABC):
    """Contract for audio/text embedding. Keep replaceable."""

    name: str
    model: str
    version: str
    is_semantic: bool = False

    @abstractmethod
    def embed_audio(self, path: Path) -> Embedding: ...

    @abstractmethod
    def embed_text(self, text: str) -> Embedding: ...

    def embed_batch(self, paths: list[Path]) -> list[Embedding]:
        return [self.embed_audio(p) for p in paths]

    def embed_digest(self, digest: str) -> Embedding:
        raise EmbeddingProviderUnavailable(
            f"{self.name} does not support digest-based embedding (needs audio)"
        )


class StubEmbeddingProvider(EmbeddingProvider):
    """Deterministic NON-semantic placeholder.

    Projects a sha256 digest into a fixed-dim unit vector. Reproducible across
    runs but carries no musical meaning. Only present so the embedding contract
    (embed -> reference -> search) is exercised without a heavy model. Semantic
    similarity MUST NOT be claimed from this provider.
    """

    name = "stub"
    model = "sha256-projection"
    version = "1"
    is_semantic = False
    DIM = 64

    def embed_audio(self, path: Path) -> Embedding:
        return self._embed(sha256_file(path) or "")

    def embed_text(self, text: str) -> Embedding:
        return self._embed(hashlib.sha256(text.encode("utf-8")).hexdigest())

    def embed_digest(self, digest: str) -> Embedding:
        return self._embed(digest)

    def _embed(self, digest: str) -> Embedding:
        seed = int(digest[:16] or "0", 16)
        rng = np.random.default_rng(seed)
        vec = rng.uniform(-1.0, 1.0, self.DIM).tolist()
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vec = [v / norm for v in vec]
        return Embedding(
            vector=vec, dim=self.DIM, provider=self.name, model=self.model, version=self.version
        )


class ClapEmbeddingProvider(EmbeddingProvider):
    """CLAP (LAION) semantic audio/text embeddings.

    Preferred semantic provider. Requires ``laion-clap`` + ``torch`` (heavy,
    not installed by default). Kept as the documented replaceable provider so
    the index can switch to semantic embeddings without re-architecting.
    """

    name = "clap"
    model = "laion-clap"
    version = "1"
    is_semantic = True

    def __init__(self) -> None:
        raise EmbeddingProviderUnavailable(
            "CLAP requires 'laion-clap' + 'torch' (not installed). "
            "Use the stub provider, or install CLAP to enable semantic embeddings."
        )

    def embed_audio(self, path: Path) -> Embedding:  # pragma: no cover
        raise EmbeddingProviderUnavailable("CLAP not available")

    def embed_text(self, text: str) -> Embedding:  # pragma: no cover
        raise EmbeddingProviderUnavailable("CLAP not available")


def get_embedding_provider(name: str | None = None) -> EmbeddingProvider:
    """Return the configured provider. Default is the honest stub."""
    if name in (None, "", "stub"):
        return StubEmbeddingProvider()
    if name == "clap":
        return ClapEmbeddingProvider()
    raise ValueError(f"unknown embedding provider: {name!r}")
