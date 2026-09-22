"""ADVANCED_PERCEPTION_V1 provider boundary and read-only fusion.

The local Analyzer remains the measurement source.  Optional providers add
observations only when actually available; unavailable providers become
explicit limitations instead of synthetic musical facts.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import hashlib
from pathlib import Path
from time import perf_counter
from typing import Any

from copilot.runtime.evidence import EvidenceGraph, EvidenceNode
from copilot.sample_library.embeddings import (
    Embedding,
    EmbeddingProviderUnavailable,
    cosine_similarity,
    get_embedding_provider,
)
from copilot.schemas.advanced_perception import (
    AdvancedPerceptionResult,
    AdvancedPerceptionStatus,
    PerceptionObservation,
    ProviderAvailability,
    ReferenceFeatureBinding,
    ReferenceIntentBundle,
    ReferenceRoleBinding,
)
from copilot.schemas.evidence import EvidenceKind, FusionStatus
from copilot.schemas.music_analysis import MusicAnalysisPack
from copilot.schemas.musical_intelligence import SemanticObservation, SemanticProviderResult


class AnalyzerMIRProvider:
    """Expose real Analyzer facts through a replaceable perception boundary."""

    name = "local-music-analyzer"
    version = "music-analyzer-v1"

    def availability(self, pack: MusicAnalysisPack) -> ProviderAvailability:
        return ProviderAvailability(
            name=self.name,
            version=self.version,
            available=bool(pack.windows or pack.sections),
            capabilities=["structure", "groove", "harmony", "timbre", "lowend"],
            reason=None if (pack.windows or pack.sections) else "MusicAnalysisPack has no measured regions",
            semantic=False,
        )

    def observe(self, pack: MusicAnalysisPack) -> list[PerceptionObservation]:
        availability = self.availability(pack)
        if not availability.available:
            return []
        token = pack.tokens.reference_state_token
        refs = list(pack.evidence_refs)
        provenance = {
            "source": "MusicAnalysisPack",
            "audio_sha256": pack.provenance.get("audio_sha256"),
            "analyzer_ids": dict(pack.analyzer_ids),
        }
        observations: list[PerceptionObservation] = []
        boundaries = sorted({
            float(section.start_beat)
            for section in pack.sections
            if section.start_beat > 0
        })
        window_size = float(pack.window_bars * 4)
        non_template = any(
            min(boundary % window_size, window_size - (boundary % window_size)) > 0.5
            for boundary in boundaries
        ) if boundaries and window_size > 0 else False
        observations.append(self._observation(
            token,
            "structure",
            "section.boundaries_beats",
            boundaries,
            refs,
            0.9 if boundaries else None,
            provenance,
        ))
        observations.append(self._observation(
            token,
            "structure",
            "section.non_template_boundary",
            non_template,
            refs + ["music_analyzer.change_points"],
            1.0 if boundaries else None,
            provenance,
        ))
        durations = [section.end_beat - section.start_beat for section in pack.sections]
        observations.append(self._observation(
            token,
            "structure",
            "section.duration_beats",
            durations,
            refs,
            0.9 if durations else None,
            provenance,
        ))
        densities = [window.groove.onset_density_per_s for window in pack.windows]
        densities = [float(value) for value in densities if value is not None]
        if densities:
            observations.append(self._observation(
                token,
                "groove",
                "groove.onset_density_per_s",
                float(sum(densities) / len(densities)),
                refs + ["music_analyzer.onsets"],
                0.8,
                provenance,
            ))
        key_candidates = [
            (window.harmony.key_candidate, window.harmony.key_confidence)
            for window in pack.windows
            if window.harmony.key_candidate
        ]
        if key_candidates:
            observations.append(self._observation(
                token,
                "harmony",
                "harmony.key_candidate",
                {"candidates": [item[0] for item in key_candidates], "confidence": [item[1] for item in key_candidates]},
                refs + ["music_analyzer.chroma"],
                max((item[1] or 0.0) for item in key_candidates),
                {**provenance, "interpretation": "candidate_only"},
                limitations=["Key is a candidate set; low confidence is preserved."],
            ))
        return observations

    def _observation(
        self,
        token: str,
        domain: str,
        question: str,
        value: Any,
        refs: Sequence[str],
        confidence: float | None,
        provenance: dict[str, Any],
        limitations: Sequence[str] = (),
    ) -> PerceptionObservation:
        safe_id = question.replace(".", "-")
        return PerceptionObservation(
            observation_id=f"perception.{self.name}.{safe_id}",
            provider=self.name,
            provider_version=self.version,
            domain=domain,
            question=question,
            value=value,
            source_token=token,
            evidence_refs=list(dict.fromkeys(refs)),
            confidence=confidence,
            limitations=list(limitations),
            provenance=dict(provenance),
        )


class EmbeddingPerceptionProvider:
    """Adapter for semantic embeddings with honest availability reporting."""

    def __init__(self, provider_name: str = "clap") -> None:
        self.provider_name = provider_name
        self.last_error: str | None = None

    def availability(self) -> ProviderAvailability:
        self.last_error = None
        probe_result: Embedding | None = None
        try:
            provider = get_embedding_provider(self.provider_name)
            probe = getattr(provider, "probe", None)
            if callable(probe):
                probe_result = probe()
        except (EmbeddingProviderUnavailable, ValueError) as exc:
            return ProviderAvailability(
                name=self.provider_name,
                version="unknown",
                available=False,
                capabilities=["audio_embedding", "text_embedding"],
                reason=str(exc),
                semantic=self.provider_name == "clap",
            )
        return ProviderAvailability(
            name=provider.name,
            version=provider.version,
            available=bool(provider.is_semantic),
            capabilities=["audio_embedding", "text_embedding"] if provider.is_semantic else [],
            reason=None if provider.is_semantic else "provider is deterministic non-semantic stub",
            semantic=provider.is_semantic,
            metadata={
                "model": provider.model,
                "runtime": provider.__class__.__name__,
                "device": getattr(provider, "device_name", None),
                "dim": probe_result.dim if probe_result is not None else None,
                "license": "Apache-2.0 (laion/clap-htsat-unfused model card)" if provider.name == "clap" else None,
            },
        )

    def observe(self, path: Path, source_token: str) -> PerceptionObservation | None:
        self.last_error = None
        try:
            provider = get_embedding_provider(self.provider_name)
            if not provider.is_semantic:
                return None
            embedding = provider.embed_audio(path)
        except (EmbeddingProviderUnavailable, ValueError) as exc:
            self.last_error = str(exc)
            return None
        return PerceptionObservation(
            observation_id=f"perception.{provider.name}.audio-embedding",
            provider=provider.name,
            provider_version=provider.version,
            domain="embedding",
            question="embedding.audio_vector",
            value={
                "dim": embedding.dim,
                "model": embedding.model,
                "sample_rate": embedding.sample_rate,
                "duration_s": embedding.duration_s,
                "window_start_s": embedding.window_start_s,
                "window_end_s": embedding.window_end_s,
                "preprocessing": embedding.preprocessing,
            },
            source_token=source_token,
            confidence=None,
            evidence_refs=["advanced_perception.audio_embedding"],
            provenance={"audio_sha256": _file_sha256(path)},
        )


class SemanticAudioProvider:
    """Generic semantic-audio boundary; unavailable providers fail closed.

    CLAP is intentionally not used here: embeddings support similarity and
    retrieval, but are not a semantic-language judgment.  A future provider
    can implement ``observe`` and return typed, provenance-bearing
    observations without changing the factual DSP layer.
    """

    name = "music-flamingo"
    version = "unavailable"

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(
            name=self.name,
            version=self.version,
            available=False,
            capabilities=["semantic_audio_description", "source_function"],
            reason="no semantic-ear provider is installed or configured",
            semantic=True,
        )

    def observe(
        self,
        *,
        audio_path: Path | None = None,
        source_token: str,
        evidence_refs: Sequence[str] = (),
    ) -> SemanticProviderResult:
        return SemanticProviderResult(
            status="SEMANTIC_PROVIDER_UNAVAILABLE",
            provider=self.name,
            model=self.version,
            reason="no semantic-ear provider is installed or configured",
            provenance={
                "audio_path_supplied": audio_path is not None,
                "source_token": source_token,
                "evidence_refs": list(evidence_refs),
            },
        )


class SemanticEarProvider(SemanticAudioProvider):
    """Backward-compatible name for the generic semantic provider boundary."""


def compare_embeddings(left: Embedding, right: Embedding) -> dict[str, Any]:
    """Return a measured similarity or an explicit NOT_COMPARABLE result."""
    try:
        return {
            "status": "COMPARABLE",
            "similarity": cosine_similarity(left, right),
            "provider": left.provider,
            "model": left.model,
            "version": left.version,
        }
    except ValueError as exc:
        return {
            "status": "NOT_COMPARABLE",
            "similarity": None,
            "reason": str(exc),
            "left": {"provider": left.provider, "model": left.model, "version": left.version, "dim": left.dim},
            "right": {"provider": right.provider, "model": right.model, "version": right.version, "dim": right.dim},
        }


def build_perception_graph(
    pack: MusicAnalysisPack,
    observations: Iterable[PerceptionObservation],
) -> EvidenceGraph:
    """Put provider observations into EvidenceGraph without rewriting the pack."""
    reference_token = pack.tokens.reference_state_token
    graph = EvidenceGraph(project_identity=reference_token, project_state=pack.tokens.target_state_token)
    source_artifact = pack.provenance.get("audio_path")
    artifact_hash = pack.provenance.get("audio_sha256")
    for observation in observations:
        graph.add(EvidenceNode(
            identity=observation.observation_id,
            kind=EvidenceKind.OBSERVATION.value,
            project_identity=reference_token,
            project_state=pack.tokens.target_state_token,
            source_artifact=str(source_artifact) if source_artifact else None,
            source_ref=str(source_artifact) if source_artifact else None,
            provider=observation.provider,
            version=observation.provider_version,
            provider_version=observation.provider_version,
            provenance={**observation.provenance, "evidence_refs": observation.evidence_refs},
            confidence=None if observation.confidence is None else str(observation.confidence),
            limitations=[{"code": item, "detail": "provider observation limitation"} for item in observation.limitations],
            payload={"name": observation.question, "value": observation.value},
            subject_identity=reference_token,
            state_tokens={
                "REFERENCE_STATE_TOKEN": pack.tokens.reference_state_token,
                "TARGET_STATE_TOKEN": pack.tokens.target_state_token,
            },
            artifact_hash=str(artifact_hash) if artifact_hash else None,
            immutable_artifact=bool(artifact_hash),
            question=observation.question,
            quality="OK" if observation.confidence is not None else "UNKNOWN",
        ))
    return graph


def run_advanced_perception(
    pack: MusicAnalysisPack,
    *,
    audio_path: Path | str | None = None,
    embedding_provider: str = "clap",
    additional_observations: Iterable[PerceptionObservation] = (),
    multi_reference: ReferenceIntentBundle | None = None,
) -> AdvancedPerceptionResult:
    """Run the bounded provider set over one real MusicAnalysisPack."""
    started = perf_counter()
    mir = AnalyzerMIRProvider()
    mir_status = mir.availability(pack)
    embedding = EmbeddingPerceptionProvider(embedding_provider)
    embedding_status = embedding.availability()
    semantic_status = SemanticEarProvider().availability()
    observations = mir.observe(pack)
    if audio_path is not None:
        embedding_observation = embedding.observe(Path(audio_path), pack.tokens.reference_state_token)
        if embedding_observation is not None:
            observations.append(embedding_observation)
        if embedding.last_error:
            embedding_status = embedding_status.model_copy(update={
                "available": False,
                "reason": f"audio observation failed: {embedding.last_error}",
            })
    observations.extend(additional_observations)
    graph = build_perception_graph(pack, observations)
    fusions = [record.to_dict() for record in graph.fuse_comparable()]
    contradictions = [row for row in fusions if row.get("status") == FusionStatus.CONTRADICT.value]
    limitations = list(pack.limitations)
    for status in (embedding_status, semantic_status):
        if not status.available and status.reason:
            limitations.append(f"{status.name}: {status.reason}")
    limitations = list(dict.fromkeys(limitations))
    if not mir_status.available:
        result_status = AdvancedPerceptionStatus.BLOCKED
    elif contradictions:
        result_status = AdvancedPerceptionStatus.PARTIAL
    elif not embedding_status.available:
        result_status = AdvancedPerceptionStatus.PROVIDER_LIMITED
    else:
        result_status = AdvancedPerceptionStatus.VERIFIED
    elapsed = perf_counter() - started
    return AdvancedPerceptionResult(
        status=result_status,
        source_reference_token=pack.tokens.reference_state_token,
        providers=[mir_status, embedding_status, semantic_status],
        observations=observations,
        fusions=fusions,
        multi_reference=multi_reference,
        limitations=limitations,
        timings_s={"total_s": elapsed},
        metadata={
            "graph_nodes": len(graph.nodes),
            "contradiction_count": len(contradictions),
            "baseline_pack_schema": pack.schema_version,
            "MUSICAL_WRITES": 0,
        },
    )


def build_multi_reference_bundle(
    packs: Sequence[MusicAnalysisPack],
    *,
    role_bindings: Sequence[ReferenceRoleBinding] = (),
    feature_bindings: Sequence[ReferenceFeatureBinding] = (),
) -> ReferenceIntentBundle:
    """Bind independently identified references without merging their facts."""
    if not packs:
        raise ValueError("at least one reference pack is required")
    tokens = [pack.tokens.reference_state_token for pack in packs]
    if len(set(tokens)) != len(tokens):
        raise ValueError("multi-reference bundle requires distinct reference tokens")
    observations: list[str] = []
    for pack in packs:
        observations.extend(pack.evidence_refs)
    return ReferenceIntentBundle(
        bundle_id="reference-intent-" + hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()[:16],
        references=tokens,
        role_bindings=list(role_bindings),
        feature_bindings=list(feature_bindings),
        provenance={
            "source": "MusicAnalysisPack",
            "reference_count": len(packs),
            "evidence_refs": list(dict.fromkeys(observations)),
        },
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
