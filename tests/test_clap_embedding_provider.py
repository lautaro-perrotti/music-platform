import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from copilot.audio.advanced_perception_v1 import compare_embeddings
from copilot.sample_library.embeddings import (
    ClapEmbeddingProvider,
    Embedding,
    cosine_similarity,
)


def _embedding(
    *, version: str = ClapEmbeddingProvider.DEFAULT_REVISION, vector: list[float] | None = None
) -> Embedding:
    values = vector or [1.0, 0.0]
    return Embedding(
        vector=values,
        dim=len(values),
        provider="clap",
        model="laion/clap-htsat-unfused",
        version=version,
    )


def test_cosine_similarity_requires_matching_provenance() -> None:
    assert cosine_similarity(_embedding(), _embedding(vector=[0.0, 1.0])) == pytest.approx(0.0)
    result = compare_embeddings(_embedding(), _embedding(version="other"))
    assert result["status"] == "NOT_COMPARABLE"
    assert result["similarity"] is None


def test_clap_long_audio_windows_are_cached_and_keep_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_rate = 16_000
    path = tmp_path / "long.wav"
    sf.write(path, np.zeros(sample_rate * 21, dtype=np.float32), sample_rate)

    calls = 0

    def fake_embed(self, audio: np.ndarray, *, start_s: float, end_s: float) -> Embedding:
        nonlocal calls
        calls += 1
        return Embedding(
            vector=[1.0, 0.0],
            dim=2,
            provider=self.name,
            model=self.model_id,
            version=self.revision,
            sample_rate=self.SAMPLE_RATE,
            duration_s=end_s - start_s,
            window_start_s=start_s,
            window_end_s=end_s,
            preprocessing=self.preprocessing_metadata(),
        )

    monkeypatch.setattr(ClapEmbeddingProvider, "_embed_audio_array", fake_embed)
    provider = ClapEmbeddingProvider()

    windows = provider.embed_audio_windows(path)
    assert len(windows) == 3
    assert calls == 3
    assert [(item.window_start_s, item.window_end_s) for item in windows] == [
        (0.0, 10.0),
        (10.0, 20.0),
        (20.0, pytest.approx(21.0)),
    ]

    cached = provider.embed_audio_windows(path)
    assert provider.last_cache_hit is True
    assert calls == 3
    assert cached[0].model == "laion/clap-htsat-unfused"


@pytest.mark.skipif(
    os.getenv("COPILOT_RUN_CLAP_TESTS", "0").lower() not in {"1", "true", "yes"},
    reason="real CLAP integration is opt-in because it loads a 600+ MB checkpoint",
)
def test_real_clap_audio_text_and_self_similarity() -> None:
    path = Path("logs/live3r/REGION_A/kick_capture_558d5e03be7c.wav")
    if not path.exists():
        pytest.skip("real controlled-working-copy capture is not present")
    provider = ClapEmbeddingProvider()
    audio = provider.embed_audio(path)
    same = provider.embed_audio(path)
    text = provider.embed_text("kick drum")

    assert audio.dim == 512
    assert cosine_similarity(audio, same) == pytest.approx(1.0, abs=1e-5)
    assert -1.0 <= cosine_similarity(audio, text) <= 1.0
    assert audio.preprocessing["sample_rate"] == 48_000
    assert audio.preprocessing["normalization"] == "unit_l2"
