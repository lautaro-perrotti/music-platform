from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.music_source import (
    BSRoFormerInferProvider,
    DemucsInferProvider,
    SeparationRequest,
    SeparationStatus,
    SourceDiscoveryStatus,
    discover_external_source,
    ingest_external_source,
)


def _write_audio(path: Path, *, frequency: float = 220.0) -> None:
    sample_rate = 16_000
    t = np.arange(sample_rate, dtype=np.float32) / sample_rate
    sf.write(path, (0.1 * np.sin(2 * np.pi * frequency * t)).astype(np.float32), sample_rate)


def test_discovery_fails_closed_when_source_is_absent(tmp_path: Path):
    result = discover_external_source(directories=[tmp_path], max_age_days=None)
    assert result.status is SourceDiscoveryStatus.SOURCE_ASSET_REQUIRED
    assert result.selected is None


def test_discovery_rejects_ambiguous_external_sources(tmp_path: Path):
    _write_audio(tmp_path / "first.wav")
    _write_audio(tmp_path / "second.wav", frequency=330.0)
    result = discover_external_source(directories=[tmp_path], max_age_days=None)
    assert result.status is SourceDiscoveryStatus.SOURCE_SELECTION_REQUIRED
    assert len(result.candidates) == 2


def test_ingestion_preserves_exact_bytes_and_provenance(tmp_path: Path):
    source = tmp_path / "elevenlabs-source.wav"
    runtime = tmp_path / "runtime-input"
    _write_audio(source)
    original_bytes = source.read_bytes()
    manifest = ingest_external_source(
        source,
        runtime_input_dir=runtime,
        source_provider="elevenlabs-music",
        source_model="music_v2_5",
        provenance=["USER_AUTHORIZED_EXTERNAL_SOURCE"],
    )
    assert manifest.immutable_path.read_bytes() == original_bytes
    assert manifest.original_sha256 == manifest.immutable_sha256
    assert manifest.source_provider == "elevenlabs-music"
    assert manifest.immutable_source is True
    assert manifest.no_ableton_access is True


def test_separator_is_honest_when_runtime_is_not_installed(tmp_path: Path):
    source = tmp_path / "source.wav"
    _write_audio(source)
    manifest = ingest_external_source(source, runtime_input_dir=tmp_path / "runtime-input")
    provider = BSRoFormerInferProvider(executable="")
    request = SeparationRequest(
        request_id="separation-test",
        source=manifest,
        output_dir=tmp_path / "stems",
    )
    result = provider.separate(request)
    if provider.executable:
        assert result.status in {SeparationStatus.SEPARATED, SeparationStatus.INFERENCE_FAILED}
    else:
        assert result.status is SeparationStatus.PROVIDER_UNAVAILABLE
        assert result.stems == []


def test_separator_does_not_claim_success_for_missing_source(tmp_path: Path):
    source = tmp_path / "source.wav"
    _write_audio(source)
    manifest = ingest_external_source(source, runtime_input_dir=tmp_path / "runtime-input")
    manifest.immutable_path.unlink()
    result = BSRoFormerInferProvider(executable="missing-bs-roformer").separate(
        SeparationRequest(
            request_id="missing-source",
            source=manifest,
            output_dir=tmp_path / "stems",
        )
    )
    assert result.status is SeparationStatus.SOURCE_ASSET_REQUIRED


def test_demucs_provider_is_replaceable_and_fails_closed_when_unconfigured(tmp_path: Path):
    provider = DemucsInferProvider(executable="", repo_dir=tmp_path / "missing-repo")
    assert provider.describe().output_stems == ["vocals", "drums", "bass", "other"]
    assert provider.health() == "UNAVAILABLE"
