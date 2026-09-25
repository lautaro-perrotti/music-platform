"""Local stem separation plus deterministic reference-stem measurements.

This module is deliberately additive to ``ReferenceAnalysisPack``.  It owns
no Ableton access and never asks a reasoning provider for interpretation.  A
separation result is a technical artifact; the measurements below do not make
claims about purity or musical quality.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field, model_validator

from copilot.audio.fullmix import compute_fullmix_observation
from copilot.audio.measure import measure_audio
from copilot.music_source.ingestion import ExternalSourceManifest, ingest_external_source
from copilot.music_source.separation import (
    BSRoFormerInferProvider,
    SeparationBatch,
    SeparationRequest,
    SeparationStatus,
    SeparatedStem,
)
from copilot.schemas.reference_analysis import ReferenceAnalysisPack
from copilot.schemas.music_analysis import MusicAnalysisPack


STEM_REFERENCE_ANALYSIS_VERSION = "stem-reference-analysis-v1"
STEM_REFERENCE_ANALYZER_ID = "stem-reference-dsp-v1"
STEM_ROLES = ("DRUMS", "BASS", "VOCALS", "OTHER")
_SILENCE_RMS = 1.0e-4
_DURATION_TOLERANCE_S = 0.15
_MIN_WINDOW_BEATS = 0.01


class StemReferenceArtifact(BaseModel):
    role: str
    source_reference_id: str
    provider: str
    provider_model: str
    path: Path
    artifact_id: str
    sha256: str
    bytes: int = Field(ge=0)
    duration_s: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    non_silent: bool
    provenance: list[str] = Field(default_factory=list)


class StemReferenceObservation(BaseModel):
    start_bar: float = Field(ge=0)
    end_bar: float = Field(gt=0)
    start_qn: float = Field(ge=0)
    end_qn: float = Field(gt=0)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    active: bool
    rms: float = Field(ge=0)
    peak: float = Field(ge=0)
    lufs: float | None = None
    spectral_centroid_hz: float | None = Field(default=None, ge=0)
    low_band_energy_ratio: float | None = Field(default=None, ge=0)
    transient_count: int = Field(ge=0)
    transient_density_per_s: float = Field(ge=0)
    event_locations_qn: list[float] = Field(default_factory=list)
    repetition_strength: float | None = Field(default=None, ge=0)
    silence_fraction: float = Field(ge=0, le=1)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_span(self) -> "StemReferenceObservation":
        if self.end_qn <= self.start_qn or self.end_s <= self.start_s:
            raise ValueError("stem observation must have positive spans")
        if self.end_bar <= self.start_bar:
            raise ValueError("stem observation must have positive bar span")
        return self


class StemReferenceRoleAnalysis(BaseModel):
    role: str
    artifact: StemReferenceArtifact | None = None
    observations: list[StemReferenceObservation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class StemReferenceAnalysis(BaseModel):
    schema_version: str = STEM_REFERENCE_ANALYSIS_VERSION
    reference_id: str
    source_analysis_id: str
    tempo_bpm: float = Field(gt=0)
    timeline: dict[str, Any]
    stems: dict[str, StemReferenceRoleAnalysis]
    global_limitations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    no_write: bool = True
    raw_audio_included: bool = False

    @model_validator(mode="after")
    def validate_contract(self) -> "StemReferenceAnalysis":
        if not self.no_write:
            raise ValueError("stem reference analysis must be NO_WRITE")
        if self.raw_audio_included:
            raise ValueError("raw audio cannot be embedded in stem analysis")
        missing = set(STEM_ROLES) - set(self.stems)
        if missing:
            raise ValueError(f"missing stem roles: {sorted(missing)}")
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_key(source_hash: str, provider: str, model: str, config: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"source": source_hash, "provider": provider, "model": model, "config": dict(config)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _role_for_stem(stem: SeparatedStem) -> str | None:
    role = stem.role.casefold()
    return {"drums": "DRUMS", "bass": "BASS", "vocals": "VOCALS", "other": "OTHER"}.get(role)


def _read_audio(path: Path) -> tuple[np.ndarray, int]:
    data, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    return np.asarray(data, dtype=np.float32), int(sample_rate)


def _combine_audio(paths: list[Path], target: Path) -> Path:
    if not paths:
        raise ValueError("cannot combine empty stem list")
    arrays: list[np.ndarray] = []
    sample_rate: int | None = None
    for path in paths:
        data, sr = _read_audio(path)
        sample_rate = sample_rate or sr
        if sr != sample_rate:
            raise ValueError("stem sample rates do not match")
        arrays.append(data)
    length = max(len(item) for item in arrays)
    channels = max(item.shape[1] for item in arrays)
    mixed = np.zeros((length, channels), dtype=np.float32)
    for item in arrays:
        padded = np.zeros((length, channels), dtype=np.float32)
        padded[: len(item), : item.shape[1]] = item
        mixed += padded
    # Combination is only for a missing aggregate role.  Preserve the
    # deterministic source relationship and avoid clipping the artifact.
    mixed /= max(1.0, float(len(arrays)))
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, mixed, sample_rate)
    return target


def _aggregate_events(observation: Any, *, start_s: float, end_s: float, tempo_bpm: float) -> tuple[list[float], float | None]:
    locations: list[float] = []
    strengths: list[float] = []
    for event in getattr(observation, "energy_events", []) or []:
        event_start = float(getattr(event, "start_s", 0.0))
        if start_s <= event_start < end_s:
            locations.append(event_start * tempo_bpm / 60.0)
            strength = getattr(event, "repetition_strength", None)
            if strength is not None:
                strengths.append(float(strength))
    return sorted(locations), (float(np.mean(strengths)) if strengths else None)


def _window_measurement(
    path: Path,
    *,
    observation: Any,
    start_qn: float,
    end_qn: float,
    tempo_bpm: float,
) -> StemReferenceObservation:
    data, sample_rate = _read_audio(path)
    start_s = start_qn * 60.0 / tempo_bpm
    end_s = end_qn * 60.0 / tempo_bpm
    a = max(0, int(round(start_s * sample_rate)))
    b = min(len(data), int(round(end_s * sample_rate)))
    window = data[a:b]
    if len(window) == 0:
        window = np.zeros((1, data.shape[1]), dtype=np.float32)
    measured = measure_audio(window.T, sample_rate, "stem", f"{start_qn}->{end_qn}qn")
    signal = measured.signal
    frames = [
        frame for frame in (getattr(observation, "energy_frames", []) or [])
        if start_s <= float(frame.t_s) < end_s
    ]
    low_values: list[float] = []
    for point in (getattr(observation, "spectral_trajectory", []) or []):
        if start_s <= float(point.t_s) < end_s:
            bands = point.bands
            total = float(sum(float(value) for value in bands.values()))
            low = float(bands.get("SUB", 0.0) + bands.get("LOW", 0.0))
            if total > 0:
                low_values.append(low / total)
    event_locations, repetition = _aggregate_events(
        observation, start_s=start_s, end_s=end_s, tempo_bpm=tempo_bpm
    )
    rms_values = [float(frame.rms) for frame in frames]
    silence_fraction = (
        sum(1 for value in rms_values if value <= _SILENCE_RMS) / len(rms_values)
        if rms_values
        else (1.0 if float(signal.rms or 0.0) <= _SILENCE_RMS else 0.0)
    )
    transient = getattr(observation, "transient", None)
    transient_count = int(getattr(transient, "transient_count", 0) or 0)
    duration = max(0.001, end_s - start_s)
    limitations = list(getattr(observation, "limitations", []) or [])
    if not frames:
        limitations.append("NO_FULLMIX_FRAMES_IN_WINDOW")
    return StemReferenceObservation(
        start_bar=start_qn / 4.0,
        end_bar=end_qn / 4.0,
        start_qn=start_qn,
        end_qn=end_qn,
        start_s=start_s,
        end_s=end_s,
        active=bool(float(signal.rms or 0.0) > _SILENCE_RMS),
        rms=float(signal.rms or 0.0),
        peak=float(signal.peak or 0.0),
        lufs=signal.lufs,
        spectral_centroid_hz=signal.spectral_centroid_hz,
        low_band_energy_ratio=(float(np.mean(low_values)) if low_values else signal.bass_energy_ratio),
        transient_count=transient_count,
        transient_density_per_s=float(transient_count / duration),
        event_locations_qn=event_locations,
        repetition_strength=repetition,
        silence_fraction=float(silence_fraction),
        limitations=limitations,
    )


def _artifact(role: str, source_id: str, provider: str, model: str, stem: SeparatedStem, provenance: list[str]) -> StemReferenceArtifact:
    return StemReferenceArtifact(
        role=role,
        source_reference_id=source_id,
        provider=provider,
        provider_model=model,
        path=stem.path,
        artifact_id=f"stem-artifact-{stem.sha256[:16]}",
        sha256=stem.sha256,
        bytes=stem.bytes,
        duration_s=stem.duration_s,
        sample_rate=stem.sample_rate,
        channels=stem.channels,
        non_silent=stem.non_silent,
        provenance=list(provenance),
    )


def _cached_separation(cache_dir: Path, *, batch_id: str, source_asset_id: str, model_id: str) -> SeparationBatch | None:
    """Recover validated WAV outputs without invoking the separator again."""
    candidates = list((cache_dir / "separation").rglob("*.wav"))
    if not candidates:
        return None
    stems: list[SeparatedStem] = []
    for path in sorted(candidates):
        try:
            info = sf.info(path)
            samples, _ = sf.read(path, always_2d=True, dtype="float32")
        except (OSError, RuntimeError, ValueError):
            continue
        role = path.stem.casefold().rsplit("_", 1)[-1]
        stems.append(
            SeparatedStem(
                stem_id=f"{batch_id}:{role}",
                role=role,
                path=path,
                sha256=_sha256(path),
                bytes=path.stat().st_size,
                duration_s=float(info.duration),
                sample_rate=int(info.samplerate),
                channels=int(info.channels),
                non_silent=bool(samples.size and (samples * samples).mean() > 1e-10),
            )
        )
    roles = {item.role for item in stems}
    if not {"drums", "bass", "vocals", "other"}.issubset(roles):
        return None
    metadata: dict[str, Any] = {}
    metadata_path = cache_dir / "separation_metadata.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            metadata = {}
    return SeparationBatch(
        batch_id=batch_id,
        status=SeparationStatus.SEPARATED,
        provider="cached-local-separator",
        model_id=model_id,
        source_asset_id=source_asset_id,
        stems=stems,
        latency_s=float(metadata["latency_s"]) if metadata.get("latency_s") is not None else None,
        command=list(metadata.get("command") or []) or None,
        provenance=["SEPARATION_CACHE_HIT", "NO_PROVIDER_INVOCATION"],
        no_ableton_access=True,
    )


def _fallback_manifest(source_path: Path, runtime_dir: Path) -> ExternalSourceManifest:
    return ingest_external_source(
        source_path,
        runtime_input_dir=runtime_dir,
        provenance=["REFERENCE_ANALYSIS_V1_EXISTING_AUTHORITATIVE_AUDIO", "NO_ABLETON_ACCESS"],
    )


def run_stem_reference_pipeline(
    *,
    source_path: Path,
    reference_analysis_path: Path,
    output_dir: Path,
    tempo_bpm: float,
    provider: BSRoFormerInferProvider | None = None,
    executable: str | None = None,
    model_dir: Path | None = None,
    use_cache: bool = True,
) -> StemReferenceAnalysis:
    """Separate one immutable reference and build aligned factual stem data."""
    source = Path(source_path).resolve()
    reference_payload = Path(reference_analysis_path).read_text(encoding="utf-8")
    schema_version = json.loads(reference_payload).get("schema_version", "")
    if str(schema_version).startswith("music-analysis"):
        # The frozen live reference artifacts are MusicAnalysisPack instances;
        # they carry the same authoritative QN windows plus richer DSP
        # families.  The discriminator is intentional: ReferenceAnalysisPack
        # ignores unknown fields, which would otherwise erase reference_id.
        reference: ReferenceAnalysisPack | MusicAnalysisPack = MusicAnalysisPack.model_validate_json(reference_payload)
    else:
        reference = ReferenceAnalysisPack.model_validate_json(reference_payload)
    provider = provider or BSRoFormerInferProvider(executable=executable)
    descriptor = provider.describe()
    source_manifest = _fallback_manifest(source, output_dir / "immutable_source")
    config = {
        "analysis": STEM_REFERENCE_ANALYZER_ID,
        "reference_analysis": reference_analysis_path.name,
        "roles": list(STEM_ROLES),
    }
    key = _cache_key(source_manifest.immutable_sha256, descriptor.provider_id, descriptor.model_id, config)
    cache_dir = output_dir / "cache" / key
    contract_path = cache_dir / "stem_reference_analysis.json"
    if use_cache and contract_path.is_file():
        cached = StemReferenceAnalysis.model_validate_json(contract_path.read_text(encoding="utf-8"))
        valid_cached = all(
            float(window.get("end_qn", 0.0)) > float(window.get("start_qn", 0.0)) + _MIN_WINDOW_BEATS
            for window in cached.timeline.get("windows_reused", [])
        )
        if valid_cached:
            return cached

    separation_batch_id = f"stem-reference-{key[:16]}"
    batch = _cached_separation(
        cache_dir,
        batch_id=separation_batch_id,
        source_asset_id=source_manifest.source_asset_id,
        model_id=descriptor.model_id,
    )
    if batch is None:
        batch = provider.separate(
            SeparationRequest(
                request_id=separation_batch_id,
                source=source_manifest,
                output_dir=cache_dir / "separation",
                model_id=descriptor.model_id,
                model_dir=model_dir,
                no_ableton_access=True,
            )
        )
        if batch.status is SeparationStatus.SEPARATED:
            (cache_dir / "separation_metadata.json").write_text(
                json.dumps(
                    {
                        "provider": descriptor.provider_id,
                        "model": descriptor.model_id,
                        "latency_s": batch.latency_s,
                        "command": batch.command,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
    if batch.status is not SeparationStatus.SEPARATED:
        raise RuntimeError(json.dumps(batch.model_dump(mode="json"), ensure_ascii=False))

    role_stems: dict[str, list[SeparatedStem]] = {role: [] for role in STEM_ROLES}
    for stem in batch.stems:
        role = _role_for_stem(stem)
        if role:
            role_stems[role].append(stem)
        elif stem.role.casefold() in {"guitar", "piano", "instrumental"}:
            role_stems["OTHER"].append(stem)

    if not role_stems["OTHER"]:
        raise RuntimeError("SEPARATOR_DID_NOT_RETURN_OTHER_OR_INSTRUMENTAL_AUDIO")
    if len(role_stems["OTHER"]) > 1:
        combined_path = cache_dir / "stems" / "other_combined.wav"
        _combine_audio([item.path for item in role_stems["OTHER"]], combined_path)
        data, sr = _read_audio(combined_path)
        role_stems["OTHER"] = [SeparatedStem(
            stem_id=f"{batch.batch_id}:other_combined",
            role="other",
            path=combined_path,
            sha256=_sha256(combined_path),
            bytes=combined_path.stat().st_size,
            duration_s=len(data) / sr,
            sample_rate=sr,
            channels=data.shape[1],
            non_silent=bool(np.mean(data * data) > 1e-10),
        )]
    missing = [role for role in STEM_ROLES if not role_stems[role]]
    if missing:
        raise RuntimeError(f"SEPARATOR_MISSING_REQUIRED_ROLES:{','.join(missing)}")

    role_results: dict[str, StemReferenceRoleAnalysis] = {}
    source_id = str(getattr(reference, "reference_id", None) or source_manifest.source_asset_id)
    valid_windows = [
        window for window in reference.windows
        if float(window.end_beat) > float(window.start_beat) + _MIN_WINDOW_BEATS
    ]
    source_duration = source_manifest.duration_s
    for role in STEM_ROLES:
        stem = role_stems[role][0]
        if abs(stem.duration_s - source_duration) > _DURATION_TOLERANCE_S:
            raise RuntimeError(f"STEM_DURATION_MISMATCH:{role}:{stem.duration_s}:{source_duration}")
        fullmix = compute_fullmix_observation(stem.path, region_id=f"stem:{role}", use_cache=True)
        observations = [
            _window_measurement(
                stem.path,
                observation=fullmix,
                start_qn=float(window.start_beat),
                end_qn=float(window.end_beat),
                tempo_bpm=tempo_bpm,
            )
            for window in valid_windows
        ]
        limitations = [
            "MEASURE_ONLY_NO_MUSICAL_JUDGMENT",
            "SEPARATION_OUTPUT_IS_NOT_PROOF_OF_SOURCE_PURITY",
            "GROUND_TRUTH_SOURCE_ISOLATION_UNAVAILABLE",
        ]
        role_results[role] = StemReferenceRoleAnalysis(
            role=role,
            artifact=_artifact(role, source_id, descriptor.provider_id, descriptor.model_id, stem, batch.provenance),
            observations=observations,
            limitations=limitations,
        )

    result = StemReferenceAnalysis(
        reference_id=source_id,
        source_analysis_id=Path(reference_analysis_path).stem,
        tempo_bpm=tempo_bpm,
        timeline={
            "source_duration_s": source_duration,
            "window_count": len(valid_windows),
            "windows_reused": [
                {"start_qn": item.start_beat, "end_qn": item.end_beat}
                for item in valid_windows
            ],
            "alignment_tolerance_s": _DURATION_TOLERANCE_S,
        },
        stems=role_results,
        global_limitations=[
            "ReferenceAnalysis timing is the sole musical timeline.",
            "Separation is technical reconstruction, not perceptual isolation verification.",
        ],
        provenance={
            "source_asset_id": source_manifest.source_asset_id,
            "source_sha256": source_manifest.immutable_sha256,
            "provider": descriptor.provider_id,
            "provider_model": descriptor.model_id,
            "provider_runtime": descriptor.runtime,
            "provider_latency_s": batch.latency_s,
            "cache_key": key,
            "analysis_cache_hit": False,
            "separation_cache_hit": "SEPARATION_CACHE_HIT" in batch.provenance,
            "no_ableton_access": True,
            "musical_writes": 0,
            "model_api_calls": 0,
        },
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def render_stem_reference_report(result: StemReferenceAnalysis) -> str:
    lines = ["REFERENCE STEM ANALYSIS", "", f"Reference: {result.reference_id}", f"Tempo: {result.tempo_bpm:g} BPM", ""]
    for role in STEM_ROLES:
        item = result.stems[role]
        lines.extend([role, f"- artifact: {item.artifact.path if item.artifact else 'missing'}"])
        for obs in item.observations:
            lines.append(
                f"- bars {obs.start_bar:g}-{obs.end_bar:g}: active={obs.active} "
                f"rms={obs.rms:.6f} events={obs.transient_count} "
                f"density={obs.transient_density_per_s:.3f}/s "
                f"low_ratio={obs.low_band_energy_ratio if obs.low_band_energy_ratio is not None else 'unknown'}"
            )
        for limitation in item.limitations:
            lines.append(f"- limitation: {limitation}")
        lines.append("")
    lines.extend(["Global limitations:", *[f"- {item}" for item in result.global_limitations]])
    lines.extend(["", "MODEL/API CALLS: 0", "MUSICAL WRITES: 0"])
    return "\n".join(lines) + "\n"


__all__ = [
    "STEM_REFERENCE_ANALYSIS_VERSION",
    "STEM_ROLES",
    "StemReferenceAnalysis",
    "StemReferenceArtifact",
    "StemReferenceObservation",
    "StemReferenceRoleAnalysis",
    "render_stem_reference_report",
    "run_stem_reference_pipeline",
]
