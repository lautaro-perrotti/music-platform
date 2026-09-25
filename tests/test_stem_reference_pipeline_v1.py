from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.music_source.separation import (
    SeparationBatch,
    SeparationStatus,
    SeparatedStem,
    SeparatorDescriptor,
)
from copilot.music_source.stem_reference import run_stem_reference_pipeline


def _audio(path: Path, frequency: float) -> None:
    sr = 8_000
    t = np.arange(sr, dtype=np.float32) / sr
    sf.write(path, (0.1 * np.sin(2 * np.pi * frequency * t)).astype(np.float32), sr)


def _reference(path: Path) -> None:
    path.write_text(
        '{"tokens":{"reference_state_token":"reference","target_state_token":"target"},'
        '"window_beats":32,"windows":[{"start_beat":0,"end_beat":32}],'
        '"no_write":true,"raw_audio_included":false}',
        encoding="utf-8",
    )


class FakeProvider:
    calls = 0

    def describe(self) -> SeparatorDescriptor:
        return SeparatorDescriptor(
            provider_id="fake-local",
            model_id="fixture-v1",
            runtime="test",
            source="local",
            model_source="local",
            output_stems=["drums", "bass", "vocals", "other"],
        )

    def separate(self, request):
        type(self).calls += 1
        out = request.output_dir / request.request_id
        out.mkdir(parents=True, exist_ok=True)
        stems = []
        for role, frequency in (("drums", 80), ("bass", 110), ("vocals", 220), ("other", 440)):
            path = out / f"source_{role}.wav"
            _audio(path, frequency)
            data, sr = sf.read(path, always_2d=True)
            import hashlib

            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            stems.append(
                SeparatedStem(
                    stem_id=f"{request.request_id}:{role}",
                    role=role,
                    path=path,
                    sha256=digest,
                    bytes=path.stat().st_size,
                    duration_s=len(data) / sr,
                    sample_rate=sr,
                    channels=data.shape[1],
                    non_silent=True,
                )
            )
        return SeparationBatch(
            batch_id=request.request_id,
            status=SeparationStatus.SEPARATED,
            provider="fake-local",
            model_id="fixture-v1",
            source_asset_id=request.source.source_asset_id,
            stems=stems,
            latency_s=0.01,
            provenance=["TEST_ONLY"],
        )


def test_stem_reference_analysis_is_aligned_cached_and_read_only(tmp_path: Path) -> None:
    source = tmp_path / "reference.wav"
    _audio(source, 220)
    reference = tmp_path / "reference-analysis.json"
    _reference(reference)
    output = tmp_path / "runtime"
    FakeProvider.calls = 0

    first = run_stem_reference_pipeline(
        source_path=source,
        reference_analysis_path=reference,
        output_dir=output,
        tempo_bpm=120,
        provider=FakeProvider(),
    )
    source_bytes = source.read_bytes()
    second = run_stem_reference_pipeline(
        source_path=source,
        reference_analysis_path=reference,
        output_dir=output,
        tempo_bpm=120,
        provider=FakeProvider(),
    )

    assert FakeProvider.calls == 1
    assert set(first.stems) == {"DRUMS", "BASS", "VOCALS", "OTHER"}
    assert first.stems["BASS"].artifact is not None
    assert first.stems["BASS"].observations[0].start_qn == 0
    assert first.stems["BASS"].observations[0].end_qn == 32
    assert first.no_write is True
    assert first.provenance["model_api_calls"] == 0
    assert second.model_dump() == first.model_dump()
    assert source.read_bytes() == source_bytes
