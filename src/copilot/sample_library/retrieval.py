"""Sample retrieval: typed search_samples + reference-like retrieval + Astra context.

SAMPLE_LIBRARY_INTELLIGENCE_V1 — sections 10/11/12/14. Retrieval is
deterministic and grounded in factual DSP descriptors; semantic (embedding)
similarity is only used when a semantic provider (CLAP) is installed. With the
non-semantic stub, embedding similarity is honestly reported as unavailable.
"""

from __future__ import annotations

import math
from typing import Any

from copilot.sample_library.embeddings import Embedding, EmbeddingProvider, get_embedding_provider
from copilot.sample_library.library_v1 import _normalize
from copilot.sample_library.schemas import (
    AudioDescriptors,
    LibraryIndex,
    RetrievalResult,
    SampleRole,
    SampleType,
)


def descriptor_distance(a: AudioDescriptors, b: AudioDescriptors) -> float | None:
    """Normalized Euclidean distance over comparable factual DSP fields (lower = closer)."""
    comps: list[float] = []
    if a.spectral_centroid_hz is not None and b.spectral_centroid_hz is not None:
        comps.append((abs(a.spectral_centroid_hz - b.spectral_centroid_hz) / 10000.0) ** 2)
    if a.duration_s is not None and b.duration_s is not None:
        comps.append((abs(a.duration_s - b.duration_s) / 4.0) ** 2)
    for f in ("low_band_energy", "mid_band_energy", "high_band_energy"):
        av = getattr(a, f)
        bv = getattr(b, f)
        if av is not None and bv is not None:
            comps.append((av - bv) ** 2)
    if not comps:
        return None
    return math.sqrt(sum(comps) / len(comps))


class SampleRetriever:
    def __init__(self, index: LibraryIndex, embedding_provider: EmbeddingProvider | None = None):
        self.index = index
        self.embedding_provider = embedding_provider or get_embedding_provider()

    def search_samples(
        self,
        *,
        role: SampleRole | None = None,
        text_query: str | None = None,
        bpm: float | None = None,
        bpm_tol: float = 5.0,
        key: str | None = None,
        one_shot_or_loop: SampleType | None = None,
        reference_descriptors: AudioDescriptors | None = None,
        reference_embedding: Embedding | None = None,
        desired_descriptors: dict[str, Any] | None = None,
        top_k: int = 10,
    ) -> list[RetrievalResult]:
        """Typed retrieval. All args optional; deterministic + reproducible."""
        results: list[RetrievalResult] = []
        q = _normalize(text_query) if text_query else ""

        for asset in self.index.assets.values():
            if asset.status.value != "INDEXED":
                continue
            # hard filters
            if role is not None and asset.semantic_role != role:
                continue
            if one_shot_or_loop is not None and asset.sample_type != one_shot_or_loop:
                continue
            if bpm is not None:
                if not asset.bpm.value or abs(asset.bpm.value - bpm) > bpm_tol:
                    continue

            score = 0.0
            reasons: list[str] = []
            if role is not None:
                score += 3.0
                reasons.append(f"semantic_role == {role.value}")
            if q:
                hay = _normalize(asset.filename + " " + asset.relative_path)
                for kw in q.split():
                    if kw and kw in hay:
                        score += 1.0
                        reasons.append(f"token '{kw}' in filename/path")
            if bpm is not None and asset.bpm.value:
                score += 2.0
                reasons.append(f"bpm {asset.bpm.value} within {bpm_tol} of {bpm}")

            dist = None
            if reference_descriptors is not None:
                dist = descriptor_distance(reference_descriptors, asset.descriptors)
                if dist is not None:
                    score += (1.0 - dist) * 3.0
                    reasons.append(f"descriptor distance {dist:.3f}")
            if reference_embedding is not None and not self.embedding_provider.is_semantic:
                reasons.append("embedding similarity unavailable (non-semantic provider)")
            if key is not None:
                reasons.append("key filter requested but pitch/key not estimated in V1")

            results.append(RetrievalResult(asset=asset, score=score, reasons=reasons, descriptor_distance=dist))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def retrieve_reference_like(
        self,
        *,
        role: SampleRole,
        reference_descriptors: AudioDescriptors | None = None,
        bpm: float | None = None,
        top_k: int = 10,
    ) -> list[RetrievalResult]:
        """Reference -> sample retrieval: nearest candidates in the same role/neighborhood."""
        return self.search_samples(
            role=role,
            reference_descriptors=reference_descriptors,
            bpm=bpm,
            top_k=top_k,
        )


def build_astra_sample_context(
    retriever: SampleRetriever,
    *,
    task_id: str,
    reference_character: dict[str, Any],
    wanted_roles: list[SampleRole],
    bpm: float | None = None,
    per_role: int = 3,
) -> dict[str, Any]:
    """Typed Astra-facing context: reference says X -> library offers A/B/C (no writes)."""
    out: dict[str, Any] = {
        "task_id": task_id,
        "reference_character": reference_character,
        "candidates": {},
        "embedding_provider": retriever.embedding_provider.name,
        "semantic": retriever.embedding_provider.is_semantic,
        "NO_WRITE": True,
    }
    for role in wanted_roles:
        hits = retriever.search_samples(role=role, bpm=bpm, top_k=per_role)
        out["candidates"][role.value] = [
            {
                "asset_id": h.asset.id,
                "filename": h.asset.filename,
                "relative_path": h.asset.relative_path,
                "confidence": h.asset.classification_confidence,
                "bpm": h.asset.bpm.value,
                "duration_s": h.asset.descriptors.duration_s,
                "centroid_hz": h.asset.descriptors.spectral_centroid_hz,
                "score": round(h.score, 3),
                "descriptor_distance": round(h.descriptor_distance, 3) if h.descriptor_distance is not None else None,
                "reasons": h.reasons,
            }
            for h in hits
        ]
    return out
