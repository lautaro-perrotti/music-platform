"""Audio-embedding layer for the sample library (provider-replaceable).

SAMPLE_LIBRARY_INTELLIGENCE_V1 - section 9. The index must not be permanently
coupled to one embedding model. CLAP is an optional real semantic provider;
when it is not installed, a deterministic NON-semantic stub keeps the
contract exercised without pretending to produce musical similarity.
"""

from __future__ import annotations

import hashlib
import math
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from pydantic import BaseModel, Field

from copilot.audio.file_hash import sha256_file


class Embedding(BaseModel):
    vector: list[float]
    dim: int
    provider: str
    model: str
    version: str
    sample_rate: int | None = None
    duration_s: float | None = None
    window_start_s: float | None = None
    window_end_s: float | None = None
    preprocessing: dict[str, Any] = Field(default_factory=dict)


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
    """Real LAION CLAP through the maintained Transformers model boundary.

    The model is loaded lazily and cached per process.  No random or DSP
    fallback is permitted.  The selected checkpoint is the public
    ``laion/clap-htsat-unfused`` model (Apache-2.0 according to its model
    card), whose processor declares 48 kHz, 10-second input windows.
    """

    name = "clap"
    model = "laion/clap-htsat-unfused"
    version = "main"
    is_semantic = True
    SAMPLE_RATE = 48_000
    WINDOW_S = 10.0
    MODEL_ID = "laion/clap-htsat-unfused"
    DEFAULT_REVISION = "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
    _cache: ClassVar[dict[tuple[str, str, str], tuple[Any, Any, Any]]] = {}
    _audio_cache: ClassVar[dict[tuple[str, str, str, str], list[Embedding]]] = {}
    _probe_cache: ClassVar[dict[tuple[str, str, str], Embedding]] = {}

    def __init__(self) -> None:
        self.model_id = os.getenv("COPILOT_CLAP_MODEL", self.MODEL_ID)
        self.revision = os.getenv("COPILOT_CLAP_REVISION", self.DEFAULT_REVISION)
        self.local_files_only = os.getenv("COPILOT_CLAP_LOCAL_ONLY", "0").lower() in {"1", "true", "yes"}
        self.version = self.revision
        self.device_name = ""
        self.last_cache_hit = False

    def _runtime(self) -> tuple[Any, Any, Any]:
        try:
            import torch
            from transformers import ClapModel, ClapProcessor
        except ImportError as exc:
            raise EmbeddingProviderUnavailable(f"CLAP dependencies unavailable: {exc}") from exc
        device = "cuda" if torch.cuda.is_available() and os.getenv("COPILOT_CLAP_CPU_ONLY", "0") != "1" else "cpu"
        key = (self.model_id, self.revision, device)
        cached = self._cache.get(key)
        if cached is not None:
            self.device_name = device
            return cached
        try:
            processor = ClapProcessor.from_pretrained(
                self.model_id,
                revision=self.revision,
                local_files_only=self.local_files_only,
            )
            model = ClapModel.from_pretrained(
                self.model_id,
                revision=self.revision,
                local_files_only=self.local_files_only,
            )
            model.to(device)
            model.eval()
        except Exception as exc:
            raise EmbeddingProviderUnavailable(f"CLAP model load failed: {exc}") from exc
        self.device_name = device
        self._cache[key] = (processor, model, torch)
        return self._cache[key]

    @staticmethod
    def _feature_tensor(output: Any) -> Any:
        if hasattr(output, "pooler_output"):
            return output.pooler_output
        if hasattr(output, "audio_embeds"):
            return output.audio_embeds
        if hasattr(output, "text_embeds"):
            return output.text_embeds
        return output

    def _embed_audio_array(self, audio: np.ndarray, *, start_s: float, end_s: float) -> Embedding:
        processor, model, torch = self._runtime()
        try:
            inputs = processor(
                audio=[audio.astype(np.float32, copy=False)],
                sampling_rate=self.SAMPLE_RATE,
                return_tensors="pt",
            )
            inputs = {key: value.to(self.device_name) if hasattr(value, "to") else value for key, value in inputs.items()}
            with torch.inference_mode():
                output = self._feature_tensor(model.get_audio_features(**inputs))
            vector = output[0].detach().float().cpu().numpy().tolist()
        except Exception as exc:
            raise EmbeddingProviderUnavailable(f"CLAP audio inference failed: {exc}") from exc
        norm = math.sqrt(sum(float(value) * float(value) for value in vector)) or 1.0
        vector = [float(value) / norm for value in vector]
        return Embedding(
            vector=vector,
            dim=len(vector),
            provider=self.name,
            model=self.model_id,
            version=self.revision,
            sample_rate=self.SAMPLE_RATE,
            duration_s=end_s - start_s,
            window_start_s=start_s,
            window_end_s=end_s,
            preprocessing=self.preprocessing_metadata(),
        )

    @staticmethod
    def preprocessing_metadata() -> dict[str, Any]:
        return {
            "sample_rate": ClapEmbeddingProvider.SAMPLE_RATE,
            "channels": "mono_mean",
            "window_s": ClapEmbeddingProvider.WINDOW_S,
            "short_audio": "processor_repeatpad",
            "long_audio": "non_overlapping_windows_mean_only_for_scalar_embed",
            "normalization": "unit_l2",
        }

    @staticmethod
    def _load_audio(path: Path) -> tuple[np.ndarray, int]:
        try:
            import soundfile as sf
            from scipy.signal import resample_poly
            audio, sample_rate = sf.read(str(path), always_2d=True, dtype="float32")
        except Exception as exc:
            raise EmbeddingProviderUnavailable(f"CLAP audio decode failed: {exc}") from exc
        if audio.size == 0:
            raise EmbeddingProviderUnavailable("CLAP cannot embed empty audio")
        mono = np.mean(audio, axis=1, dtype=np.float32)
        if sample_rate != ClapEmbeddingProvider.SAMPLE_RATE:
            mono = resample_poly(mono, ClapEmbeddingProvider.SAMPLE_RATE, int(sample_rate)).astype(np.float32)
        return mono, ClapEmbeddingProvider.SAMPLE_RATE

    def embed_audio_windows(self, path: Path) -> list[Embedding]:
        digest = sha256_file(path) or ""
        cache_key = (digest, self.model_id, self.revision, repr(self.preprocessing_metadata()))
        cached = self._audio_cache.get(cache_key)
        if cached is not None:
            self.last_cache_hit = True
            return [item.model_copy(deep=True) for item in cached]
        self.last_cache_hit = False
        audio, sample_rate = self._load_audio(path)
        window_samples = int(self.WINDOW_S * sample_rate)
        windows: list[Embedding] = []
        for start in range(0, len(audio), window_samples):
            end = min(start + window_samples, len(audio))
            windows.append(self._embed_audio_array(audio[start:end], start_s=start / sample_rate, end_s=end / sample_rate))
        self._audio_cache[cache_key] = [item.model_copy(deep=True) for item in windows]
        return windows

    def embed_audio(self, path: Path) -> Embedding:
        windows = self.embed_audio_windows(Path(path))
        if len(windows) == 1:
            return windows[0]
        vector = np.mean(np.asarray([item.vector for item in windows], dtype=np.float32), axis=0)
        vector /= np.linalg.norm(vector) or 1.0
        result = windows[0].model_copy(update={
            "vector": vector.astype(float).tolist(),
            "duration_s": sum(item.duration_s or 0.0 for item in windows),
            "window_start_s": 0.0,
            "window_end_s": sum(item.duration_s or 0.0 for item in windows),
            "preprocessing": {**self.preprocessing_metadata(), "window_count": len(windows)},
        })
        return result

    def probe(self) -> Embedding:
        """Load the checkpoint and run one minimal real inference."""
        _, model, _ = self._runtime()
        device = self.device_name or str(next(model.parameters()).device)
        key = (self.model_id, self.revision, device)
        cached = self._probe_cache.get(key)
        if cached is not None:
            return cached.model_copy(deep=True)
        result = self._embed_audio_array(
            np.zeros(int(self.SAMPLE_RATE * self.WINDOW_S), dtype=np.float32),
            start_s=0.0,
            end_s=self.WINDOW_S,
        )
        self._probe_cache[key] = result.model_copy(deep=True)
        return result

    def embed_text(self, text: str) -> Embedding:
        if not text.strip():
            raise EmbeddingProviderUnavailable("CLAP cannot embed empty text")
        processor, model, torch = self._runtime()
        try:
            inputs = processor(text=[text], return_tensors="pt", padding=True)
            inputs = {key: value.to(self.device_name) if hasattr(value, "to") else value for key, value in inputs.items()}
            with torch.inference_mode():
                output = self._feature_tensor(model.get_text_features(**inputs))
            vector = output[0].detach().float().cpu().numpy().tolist()
        except Exception as exc:
            raise EmbeddingProviderUnavailable(f"CLAP text inference failed: {exc}") from exc
        norm = math.sqrt(sum(float(value) * float(value) for value in vector)) or 1.0
        vector = [float(value) / norm for value in vector]
        return Embedding(
            vector=vector,
            dim=len(vector),
            provider=self.name,
            model=self.model_id,
            version=self.revision,
            preprocessing={"normalization": "unit_l2", "text": "CLAP tokenizer"},
        )


def cosine_similarity(left: Embedding, right: Embedding) -> float:
    """Compare only compatible provider/model/version embeddings."""
    if (left.provider, left.model, left.version, left.dim) != (right.provider, right.model, right.version, right.dim):
        raise ValueError("NOT_COMPARABLE: embedding provider/model/version/dimension mismatch")
    if not left.vector or not right.vector:
        raise ValueError("NOT_COMPARABLE: empty embedding")
    return float(np.dot(np.asarray(left.vector, dtype=np.float32), np.asarray(right.vector, dtype=np.float32)))


def get_embedding_provider(name: str | None = None) -> EmbeddingProvider:
    """Return the configured provider. Default is the honest stub."""
    if name in (None, "", "stub"):
        return StubEmbeddingProvider()
    if name == "clap":
        return ClapEmbeddingProvider()
    raise ValueError(f"unknown embedding provider: {name!r}")
