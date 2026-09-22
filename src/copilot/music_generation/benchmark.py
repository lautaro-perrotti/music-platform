"""Provider-neutral best-of-N generation and candidate evidence bundles.

This module owns no Ableton access and makes no musical winner claims.  It
keeps generation, technical validation, perception evidence and duplicate
analysis separate so a later human or reasoning provider can judge candidates
without losing provenance.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import random
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field

from copilot.audio.file_hash import sha256_file
from copilot.audio.music_analyzer import analyze_reference_file
from copilot.music_generation.registry import MusicGeneratorProvider
from copilot.music_generation.schemas import GeneratedAsset, GenerationBrief, GeneratorRequest
from copilot.sample_library.embeddings import Embedding, EmbeddingProviderUnavailable, cosine_similarity


class TechnicalValidation(BaseModel):
    readable: bool = False
    non_empty: bool = False
    non_silent: bool = False
    expected_duration: bool = False
    valid_channels: bool = False
    valid_sample_rate: bool = False
    finite_samples: bool = False
    hash_matches: bool = False
    provenance_complete: bool = False
    status: str = "INVALID"
    reasons: list[str] = Field(default_factory=list)


class DuplicateRelation(BaseModel):
    left_candidate_id: str
    right_candidate_id: str
    exact_hash: bool = False
    same_seed: bool = False
    clap_similarity: float | None = None
    effectively_duplicate: bool = False
    basis: list[str] = Field(default_factory=list)


class CandidateRecord(BaseModel):
    candidate_id: str
    attempt: int
    asset: GeneratedAsset
    bundle_path: Path
    technical_validation: TechnicalValidation
    analyzer_path: Path | None = None
    clap_path: Path | None = None
    clap_status: str = "NOT_RUN"
    duplicate_relations: list[DuplicateRelation] = Field(default_factory=list)


class BestOfNReport(BaseModel):
    report_id: str
    brief: GenerationBrief
    output_root: Path
    desired_candidates: int
    max_generation_attempts: int
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[CandidateRecord] = Field(default_factory=list)
    duplicate_relations: list[DuplicateRelation] = Field(default_factory=list)
    baseline: dict[str, Any] | None = None
    quality_status: str = "CANDIDATES_NOT_READY"
    musical_winner: str | None = None
    no_ableton_access: bool = True


class BlindBenchmarkReport(BaseModel):
    benchmark_id: str
    listener_dir: Path
    mapping_path: Path
    item_count: int
    target_rms_dbfs: float
    baseline_included: bool
    human_evaluation_status: str = "PENDING"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def validate_generated_audio(asset: GeneratedAsset, *, expected_duration_s: float) -> TechnicalValidation:
    """Validate facts only; never rate musical quality."""
    result = TechnicalValidation()
    reasons: list[str] = []
    path = Path(asset.path)
    try:
        info = sf.info(path)
        audio, sample_rate = sf.read(path, always_2d=True, dtype="float32")
        result.readable = True
        result.non_empty = bool(audio.size and info.frames > 0)
        result.non_silent = bool(np.any(np.abs(audio) > 1e-6))
        result.expected_duration = abs(float(info.duration) - expected_duration_s) <= max(0.5, expected_duration_s * 0.05)
        result.valid_channels = 1 <= int(info.channels) <= 8
        result.valid_sample_rate = int(sample_rate) > 0
        result.finite_samples = bool(np.isfinite(audio).all())
        actual_hash = sha256_file(path)
        result.hash_matches = actual_hash == asset.sha256
    except Exception as exc:
        reasons.append(f"decode:{type(exc).__name__}:{exc}")
    result.provenance_complete = bool(
        asset.sha256 and asset.model.provider and asset.model.model_id and asset.performance.device
        and asset.seed is not None and asset.prompt
    )
    for name in (
        "readable", "non_empty", "non_silent", "expected_duration", "valid_channels",
        "valid_sample_rate", "finite_samples", "hash_matches", "provenance_complete",
    ):
        if not getattr(result, name):
            reasons.append(name)
    result.reasons = reasons
    result.status = "VALID" if not reasons else "INVALID"
    return result


def _bundle_candidate(asset: GeneratedAsset, brief: GenerationBrief, root: Path, validation: TechnicalValidation) -> Path:
    bundle = root / asset.asset_id.replace(":", "_")
    bundle.mkdir(parents=True, exist_ok=True)
    raw = bundle / "raw.wav"
    if Path(asset.path).resolve() != raw.resolve():
        shutil.copy2(asset.path, raw)
    _write_json(bundle / "generation_brief.json", brief.model_dump(mode="json"))
    _write_json(bundle / "generator_config.json", {"seed": asset.seed, "no_ableton_access": asset.no_ableton_access})
    _write_json(bundle / "model_manifest.json", asset.model.model_dump(mode="json"))
    _write_json(bundle / "rights_manifest.json", asset.rights_manifest.model_dump(mode="json"))
    _write_json(bundle / "performance.json", asset.performance.model_dump(mode="json"))
    _write_json(bundle / "technical_validation.json", validation.model_dump(mode="json"))
    return bundle


def _cosine(left: Embedding | None, right: Embedding | None) -> float | None:
    if left is None or right is None:
        return None
    return float(cosine_similarity(left, right))


def analyze_candidate(
    record: CandidateRecord,
    *,
    brief: GenerationBrief,
    reference_state_token: str,
    target_state_token: str,
    clap_provider: Any | None = None,
) -> Embedding | None:
    """Run the existing Analyzer and optional real CLAP provider for one asset."""
    pack = analyze_reference_file(
        record.asset.path,
        reference_state_token=reference_state_token,
        target_state_token=target_state_token,
        tempo_bpm=float(brief.tempo_bpm or 120.0),
        use_cache=True,
    )
    analyzer_path = record.bundle_path / "music_analyzer.json"
    _write_json(analyzer_path, pack.model_dump(mode="json"))
    record.analyzer_path = analyzer_path
    if clap_provider is None:
        record.clap_status = "NOT_CONFIGURED"
        return None
    try:
        embedding = clap_provider.embed_audio(record.asset.path)
        clap_path = record.bundle_path / "clap.json"
        _write_json(clap_path, embedding.model_dump(mode="json"))
        record.clap_path = clap_path
        record.clap_status = "AVAILABLE"
        return embedding
    except EmbeddingProviderUnavailable as exc:
        record.clap_status = f"UNAVAILABLE:{exc}"
        return None


def _relations(records: list[CandidateRecord], embeddings: dict[str, Embedding]) -> list[DuplicateRelation]:
    relations: list[DuplicateRelation] = []
    for index, left in enumerate(records):
        for right in records[index + 1:]:
            exact_hash = left.asset.sha256 == right.asset.sha256
            same_seed = left.asset.seed == right.asset.seed
            similarity = _cosine(embeddings.get(left.candidate_id), embeddings.get(right.candidate_id))
            basis: list[str] = []
            if exact_hash:
                basis.append("identical_sha256")
            if same_seed and left.asset.model.model_id == right.asset.model.model_id:
                basis.append("same_seed_and_model")
            if similarity is not None and similarity >= 0.995:
                basis.append("clap_cosine_ge_0.995")
            relations.append(DuplicateRelation(
                left_candidate_id=left.candidate_id,
                right_candidate_id=right.candidate_id,
                exact_hash=exact_hash,
                same_seed=same_seed,
                clap_similarity=similarity,
                effectively_duplicate=bool(basis),
                basis=basis,
            ))
    return relations


def run_best_of_n(
    provider: MusicGeneratorProvider,
    brief: GenerationBrief,
    *,
    output_root: Path,
    desired_candidates: int = 8,
    max_generation_attempts: int = 12,
    seed_start: int = 1721,
    reference_state_token: str = "reference:best-of-n",
    target_state_token: str = "target:best-of-n",
    clap_provider: Any | None = None,
    baseline: dict[str, Any] | None = None,
) -> BestOfNReport:
    """Generate a bounded resumable batch and persist every factual outcome."""
    if desired_candidates < 1 or desired_candidates > 16:
        raise ValueError("desired_candidates must be between 1 and 16")
    if max_generation_attempts < desired_candidates:
        raise ValueError("max_generation_attempts must cover desired_candidates")
    report = BestOfNReport(
        report_id=f"foundation-generation-{brief.brief_id}",
        brief=brief,
        output_root=output_root,
        desired_candidates=desired_candidates,
        max_generation_attempts=max_generation_attempts,
        baseline=baseline,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    embeddings: dict[str, Embedding] = {}
    for attempt in range(1, max_generation_attempts + 1):
        if len(report.candidates) >= desired_candidates:
            break
        request_id = f"{brief.brief_id}-candidate-{attempt:02d}"
        seed = seed_start + attempt - 1
        request = GeneratorRequest(
            request_id=request_id,
            brief=brief,
            seed=seed,
            output_dir=output_root / "raw",
        )
        batch = provider.generate(request)
        attempt_row: dict[str, Any] = {
            "attempt": attempt,
            "request_id": request_id,
            "seed": seed,
            "status": batch.status,
            "failures": batch.failures,
            "asset_count": len(batch.assets),
        }
        report.attempts.append(attempt_row)
        _write_json(output_root / "attempts.json", report.attempts)
        for asset in batch.assets:
            candidate_id = f"candidate_{len(report.candidates) + 1:03d}"
            validation = validate_generated_audio(asset, expected_duration_s=brief.target_duration_s)
            bundle = _bundle_candidate(asset, brief, output_root / "candidates", validation)
            record = CandidateRecord(
                candidate_id=candidate_id,
                attempt=attempt,
                asset=asset,
                bundle_path=bundle,
                technical_validation=validation,
            )
            if validation.status == "VALID":
                embedding = analyze_candidate(
                    record,
                    brief=brief,
                    reference_state_token=f"{reference_state_token}:{candidate_id}",
                    target_state_token=f"{target_state_token}:{candidate_id}",
                    clap_provider=clap_provider,
                )
                if embedding is not None:
                    embeddings[candidate_id] = embedding
            report.candidates.append(record)
            _write_json(output_root / "candidate_report.json", [item.model_dump(mode="json") for item in report.candidates])
    report.duplicate_relations = _relations(report.candidates, embeddings)
    for relation in report.duplicate_relations:
        for record in report.candidates:
            if record.candidate_id in {relation.left_candidate_id, relation.right_candidate_id}:
                record.duplicate_relations.append(relation)
    report.quality_status = "CANDIDATES_READY / HUMAN_EVALUATION_PENDING" if report.candidates else "NO_VALID_CANDIDATES"
    report.musical_winner = None
    _write_json(output_root / "comparison.json", {
        "duplicate_relations": [item.model_dump(mode="json") for item in report.duplicate_relations],
        "winner": None,
        "winner_claim": "NONE: Core does not infer musical quality from DSP or CLAP alone",
    })
    _write_json(output_root / "best_of_n_report.json", report.model_dump(mode="json"))
    return report


def resume_best_of_n_after_runtime_recovery(
    report_path: Path,
    provider: MusicGeneratorProvider,
    *,
    additional_candidates: int = 1,
    clap_provider: Any | None = None,
) -> BestOfNReport:
    """Resume a persisted batch after a provider-worker interruption.

    Recovery attempts are explicit in the persisted report and never erase the
    original bounded attempt history.  This is for worker recovery, not a
    silent retry loop.
    """
    report = BestOfNReport.model_validate_json(Path(report_path).read_text(encoding="utf-8"))
    next_attempt = max((int(item.get("attempt", 0)) for item in report.attempts), default=0) + 1
    embeddings: dict[str, Embedding] = {}
    for record in report.candidates:
        if record.clap_path and Path(record.clap_path).is_file():
            try:
                payload = json.loads(Path(record.clap_path).read_text(encoding="utf-8"))
                embeddings[record.candidate_id] = Embedding.model_validate(payload)
            except Exception:
                pass
    for offset in range(additional_candidates):
        if len(report.candidates) >= report.desired_candidates:
            break
        attempt = next_attempt + offset
        request_id = f"{report.brief.brief_id}-candidate-{attempt:02d}"
        request = GeneratorRequest(
            request_id=request_id,
            brief=report.brief,
            seed=1721 + attempt - 1,
            output_dir=report.output_root / "raw",
        )
        batch = provider.generate(request)
        row = {
            "attempt": attempt,
            "request_id": request_id,
            "seed": request.seed,
            "status": batch.status,
            "failures": batch.failures,
            "asset_count": len(batch.assets),
            "recovery_attempt": True,
        }
        report.attempts.append(row)
        for asset in batch.assets:
            candidate_id = f"candidate_{len(report.candidates) + 1:03d}"
            validation = validate_generated_audio(asset, expected_duration_s=report.brief.target_duration_s)
            bundle = _bundle_candidate(asset, report.brief, report.output_root / "candidates", validation)
            record = CandidateRecord(
                candidate_id=candidate_id,
                attempt=attempt,
                asset=asset,
                bundle_path=bundle,
                technical_validation=validation,
            )
            if validation.status == "VALID":
                embedding = analyze_candidate(
                    record,
                    brief=report.brief,
                    reference_state_token=f"reference:recovery:{candidate_id}",
                    target_state_token=f"target:recovery:{candidate_id}",
                    clap_provider=clap_provider,
                )
                if embedding is not None:
                    embeddings[candidate_id] = embedding
            report.candidates.append(record)
        _write_json(report.output_root / "attempts.json", report.attempts)
    report.duplicate_relations = _relations(report.candidates, embeddings)
    report.quality_status = "CANDIDATES_READY / HUMAN_EVALUATION_PENDING" if report.candidates else "NO_VALID_CANDIDATES"
    _write_json(report.output_root / "best_of_n_report.json", report.model_dump(mode="json"))
    return report


def freeze_quality_baseline(
    *,
    source_audio: Path,
    output_root: Path,
    project_context: dict[str, Any],
    user_verdict: str = "REJECT",
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    """Copy an existing rejected Alpha audio artifact without inventing reasons."""
    source_audio = Path(source_audio)
    baseline_dir = output_root / "QUALITY_BASELINE_001"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    target = baseline_dir / "raw.wav"
    shutil.copy2(source_audio, target)
    manifest = {
        "baseline_id": "QUALITY_BASELINE_001",
        "raw_audio": str(target),
        "source_audio": str(source_audio),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "project_context": project_context,
        "user_verdict": user_verdict,
        "rejection_reason": rejection_reason,
        "reason_status": "NOT_SUPPLIED" if rejection_reason is None else "SUPPLIED",
        "audio_preserved_untouched": True,
    }
    _write_json(baseline_dir / "baseline_manifest.json", manifest)
    return manifest


def build_blind_benchmark(
    report: BestOfNReport,
    *,
    baseline_manifest: dict[str, Any] | None,
    output_dir: Path,
    target_rms_dbfs: float = -18.0,
    shuffle_seed: int = 20260922,
) -> BlindBenchmarkReport:
    """Create loudness-fair listener copies and a private identity mapping."""
    listener_dir = output_dir / "listener_bundle"
    listener_dir.mkdir(parents=True, exist_ok=True)
    items: list[tuple[str, Path, dict[str, Any]]] = []
    if baseline_manifest is not None:
        items.append(("baseline", Path(baseline_manifest["raw_audio"]), {"kind": "baseline"}))
    for record in report.candidates:
        if record.technical_validation.status != "VALID":
            continue
        items.append((record.candidate_id, record.bundle_path / "raw.wav", {"kind": "candidate"}))
    random.Random(shuffle_seed).shuffle(items)
    mapping: dict[str, Any] = {
        "benchmark_id": "HUMAN_MUSICAL_BENCHMARK_V1",
        "shuffle_seed": shuffle_seed,
        "items": [],
        "provider_metadata_hidden_from_listener": True,
    }
    import soundfile as sf
    for index, (identity, source, private_meta) in enumerate(items, start=1):
        audio, sample_rate = sf.read(source, always_2d=True, dtype="float32")
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        target_rms = 10 ** (target_rms_dbfs / 20.0)
        if rms > 1e-9:
            audio = np.clip(audio * (target_rms / rms), -1.0, 1.0)
        blind_name = f"blind_{index:03d}.wav"
        sf.write(listener_dir / blind_name, audio, sample_rate, subtype="PCM_16")
        mapping["items"].append({"blind_id": blind_name[:-4], "identity": identity, **private_meta})
    mapping_path = output_dir / "blind_mapping_private.json"
    _write_json(mapping_path, mapping)
    _write_json(listener_dir / "listener_manifest.json", {
        "benchmark_id": "HUMAN_MUSICAL_BENCHMARK_V1",
        "items": [item["blind_id"] for item in mapping["items"]],
        "provider": "HIDDEN",
        "model": "HIDDEN",
        "seed": "HIDDEN",
        "baseline_identity": "HIDDEN",
    })
    result = BlindBenchmarkReport(
        benchmark_id="HUMAN_MUSICAL_BENCHMARK_V1",
        listener_dir=listener_dir,
        mapping_path=mapping_path,
        item_count=len(mapping["items"]),
        target_rms_dbfs=target_rms_dbfs,
        baseline_included=baseline_manifest is not None,
    )
    _write_json(output_dir / "blind_benchmark_report.json", result.model_dump(mode="json"))
    return result
