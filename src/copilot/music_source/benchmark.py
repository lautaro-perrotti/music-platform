"""Provider-neutral benchmark contracts for source-separation candidates.

This module deliberately separates objective file/runtime facts from the human
judgement required to call a stem usable.  A technically valid candidate is
never promoted to a production stem by Core.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import shutil
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field


class WeightsLicenseStatus(StrEnum):
    VERIFIED = "VERIFIED"
    COMMERCIAL_ALLOWED = "COMMERCIAL_ALLOWED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    NOASSERTION = "NOASSERTION"
    UNKNOWN = "UNKNOWN"


class CandidateDisposition(StrEnum):
    TECHNICALLY_VALID = "TECHNICALLY_VALID"
    PERCEPTUALLY_UNREVIEWED = "PERCEPTUALLY_UNREVIEWED"
    HUMAN_PREFERRED = "HUMAN_PREFERRED"
    HUMAN_USABLE = "HUMAN_USABLE"
    HUMAN_REFERENCE_ONLY = "HUMAN_REFERENCE_ONLY"
    HUMAN_REJECTED = "HUMAN_REJECTED"


class SeparatorRuntimeManifest(BaseModel):
    provider_id: str
    runtime_version: str | None = None
    runtime_source: str | None = None
    code_license: str = "UNKNOWN"
    python_executable: str | None = None
    device: str | None = None
    precision: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class SeparatorCheckpointManifest(BaseModel):
    model_id: str
    source_url: str | None = None
    upstream_release: str | None = None
    revision: str | None = None
    sha256: str | None = None
    weights_license: str = "UNKNOWN"
    weights_license_status: WeightsLicenseStatus = WeightsLicenseStatus.UNKNOWN


class SeparatorManifest(BaseModel):
    provider_id: str
    model_id: str
    architecture: str | None = None
    runtime: SeparatorRuntimeManifest
    checkpoint: SeparatorCheckpointManifest
    output_roles: list[str] = Field(default_factory=list)
    benchmark_role: str = "BROAD_4_STEM"
    source_rate_hz: int | None = None
    expected_rate_hz: int | None = None
    provenance: list[str] = Field(default_factory=list)


class SeparatorRunRequest(BaseModel):
    run_id: str
    source_asset_id: str
    source_path: Path
    output_dir: Path
    separator: SeparatorManifest
    timeout_s: float = Field(default=86_400.0, gt=0)
    no_ableton_access: bool = True


class TechnicalStemValidation(BaseModel):
    path: Path
    role: str
    exists: bool
    readable: bool
    duration_s: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    frames: int | None = None
    finite: bool = False
    non_silent: bool = False
    clipping: bool = False
    gross_truncation: bool = False
    sha256: str | None = None
    errors: list[str] = Field(default_factory=list)


class StemCandidate(BaseModel):
    candidate_id: str
    run_id: str
    provider_id: str
    model_id: str
    role: str
    path: Path
    sha256: str
    bytes: int = Field(ge=0)
    validation: TechnicalStemValidation
    disposition: CandidateDisposition = CandidateDisposition.PERCEPTUALLY_UNREVIEWED
    no_ableton_access: bool = True


class StemCandidateSet(BaseModel):
    benchmark_id: str
    source_asset_id: str
    candidates: list[StemCandidate] = Field(default_factory=list)
    control_candidate_ids: list[str] = Field(default_factory=list)
    status: str = "TECHNICAL_VALIDATION_PENDING"
    human_evaluation_required: bool = True
    no_ableton_access: bool = True


class BlindCandidateMapping(BaseModel):
    benchmark_id: str
    public_to_private: dict[str, str]
    roles: list[str]
    gain_db_by_public_role: dict[str, float] = Field(default_factory=dict)
    created_at: str


class HumanStemEvaluation(BaseModel):
    benchmark_id: str
    role: str
    public_candidate_id: str
    category: str
    dimensions: dict[str, int] = Field(default_factory=dict)
    notes: str | None = None
    evaluator: str = "human"


class StemSelectionDecision(BaseModel):
    benchmark_id: str
    selected_by_role: dict[str, str] = Field(default_factory=dict)
    status: str = "HUMAN_SELECTION_PENDING"
    provenance: list[HumanStemEvaluation] = Field(default_factory=list)


class StemBenchmarkManifest(BaseModel):
    benchmark_id: str
    source_asset_id: str
    source_sha256: str
    source_path: Path
    runs: list[dict[str, Any]] = Field(default_factory=list)
    technical_validation: list[TechnicalStemValidation] = Field(default_factory=list)
    blind_bundle_dir: Path | None = None
    private_mapping_path: Path | None = None
    human_selection: str = "PENDING"
    ableton_import: str = "HOLD"
    lucas_files_modified: int = 0
    original_project_writes: int = 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_stem_file(path: Path, *, role: str, source_info: sf._SoundFileInfo | None = None) -> TechnicalStemValidation:
    """Validate only objective audio facts; never infer source purity."""

    path = Path(path)
    result = TechnicalStemValidation(path=path, role=role, exists=path.is_file(), readable=False)
    if not result.exists:
        result.errors.append("FILE_MISSING")
        return result
    try:
        info = sf.info(path)
        audio, _ = sf.read(path, always_2d=True, dtype="float32")
        finite = bool(np.isfinite(audio).all())
        result.readable = True
        result.duration_s = float(info.duration)
        result.sample_rate = int(info.samplerate)
        result.channels = int(info.channels)
        result.frames = int(info.frames)
        result.finite = finite
        result.non_silent = bool(audio.size and float(np.mean(audio * audio)) > 1e-12)
        result.clipping = bool(audio.size and float(np.max(np.abs(audio))) > 1.0)
        if not finite:
            result.errors.append("NON_FINITE_AUDIO")
        if result.duration_s <= 0 or result.frames <= 0:
            result.errors.append("INVALID_DURATION_OR_FRAMES")
        if source_info is not None:
            result.gross_truncation = result.duration_s < float(source_info.duration) * 0.98
            if result.gross_truncation:
                result.errors.append("GROSS_TRUNCATION")
            if result.sample_rate != int(source_info.samplerate):
                result.errors.append("SAMPLE_RATE_DIFFERS_FROM_SOURCE")
    except (OSError, RuntimeError, ValueError) as exc:
        result.errors.append(type(exc).__name__)
        return result
    result.sha256 = _sha256(path)
    return result


def build_stem_candidate_set(
    *,
    benchmark_id: str,
    source_asset_id: str,
    candidates: Iterable[StemCandidate],
) -> StemCandidateSet:
    rows = list(candidates)
    invalid = [c for c in rows if c.validation.errors or not c.validation.readable]
    return StemCandidateSet(
        benchmark_id=benchmark_id,
        source_asset_id=source_asset_id,
        candidates=rows,
        control_candidate_ids=[c.candidate_id for c in rows if c.provider_id == "bs-roformer-infer" and "sw" in c.model_id],
        status="TECHNICALLY_VALID" if rows and not invalid else "TECHNICAL_VALIDATION_INCOMPLETE",
    )


def build_blind_bundle(
    *,
    benchmark_id: str | None = None,
    candidate_paths_by_private_id: Mapping[str, Mapping[str, Path]],
    output_dir: Path,
    mapping_path: Path,
    source_info: sf._SoundFileInfo,
    excerpt_windows_s: Iterable[tuple[float, float]] = ((8.0, 16.0), (48.0, 64.0), (80.0, 96.0), (104.0, 116.0)),
) -> BlindCandidateMapping:
    """Build anonymous gain-only listening files and a private mapping.

    The mapping is stored separately from the listener-facing directory.  No
    provider/model/path is encoded in public filenames.
    """

    private_ids = sorted(candidate_paths_by_private_id)
    if not private_ids:
        raise ValueError("BLIND_BUNDLE_REQUIRES_CANDIDATES")
    labels = [f"candidate_{chr(ord('A') + index)}" for index in range(len(private_ids))]
    shuffled = labels[:]
    secrets.SystemRandom().shuffle(shuffled)
    public_to_private = dict(zip(shuffled, private_ids, strict=True))
    output_dir.mkdir(parents=True, exist_ok=True)
    roles = sorted({role for rows in candidate_paths_by_private_id.values() for role in rows})
    gain_by_private_role: dict[tuple[str, str], float] = {}
    for role in roles:
        rms_values: list[float] = []
        for private_id in private_ids:
            path = candidate_paths_by_private_id[private_id].get(role)
            if path is None:
                continue
            audio, _ = sf.read(path, always_2d=True, dtype="float32")
            rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
            if rms > 1e-8 and math.isfinite(rms):
                rms_values.append(rms)
        target_rms = float(np.median(rms_values)) if rms_values else 0.0
        for private_id in private_ids:
            path = candidate_paths_by_private_id[private_id].get(role)
            if path is None:
                continue
            audio, _ = sf.read(path, always_2d=True, dtype="float32")
            rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
            gain = target_rms / rms if target_rms > 0.0 and rms > 1e-8 else 1.0
            gain = min(4.0, max(0.25, gain))
            gain_by_private_role[(private_id, role)] = gain
        role_dir = output_dir / role
        role_dir.mkdir(parents=True, exist_ok=True)
        for public_id, private_id in public_to_private.items():
            path = candidate_paths_by_private_id[private_id].get(role)
            if path is None:
                continue
            audio, rate = sf.read(path, always_2d=True, dtype="float32")
            gain = gain_by_private_role[(private_id, role)]
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak > 0.0:
                gain = min(gain, 0.98 / peak)
            gain_by_private_role[(private_id, role)] = gain
            sf.write(role_dir / f"{public_id}.wav", (audio * gain).astype(np.float32), rate, subtype="PCM_16")
            for index, (start_s, end_s) in enumerate(excerpt_windows_s, start=1):
                start = max(0, int(start_s * rate))
                end = min(audio.shape[0], int(end_s * rate))
                sf.write(role_dir / f"{public_id}_excerpt_{index:02d}.wav", (audio[start:end] * gain).astype(np.float32), rate, subtype="PCM_16")
    mapping = BlindCandidateMapping(
        benchmark_id=benchmark_id or output_dir.parent.name,
        public_to_private=public_to_private,
        roles=roles,
        gain_db_by_public_role={
            f"{public_id}:{role}": 20.0 * math.log10(max(gain_by_private_role[(private_id, role)], 1e-12))
            for public_id, private_id in public_to_private.items()
            for role in roles
            if (private_id, role) in gain_by_private_role
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")
    return mapping
