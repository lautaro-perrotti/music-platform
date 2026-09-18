from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from copilot.sample_library.embeddings import (
    ClapEmbeddingProvider,
    EmbeddingProviderUnavailable,
    StubEmbeddingProvider,
    get_embedding_provider,
)
from copilot.sample_library.library_v1 import compute_embeddings
from copilot.sample_library.schemas import AssetStatus, LibraryIndex, SampleAsset


def _make_wav(path: Path):
    sf.write(str(path), np.zeros(2205, dtype=np.float32), 44100)


def test_stub_is_deterministic(tmp_path):
    p = tmp_path / "kick.wav"
    _make_wav(p)
    prov = StubEmbeddingProvider()
    a = prov.embed_audio(p)
    b = prov.embed_audio(p)
    assert a.vector == b.vector
    assert a.dim == 64
    assert prov.is_semantic is False


def test_stub_text_differs_from_audio():
    prov = StubEmbeddingProvider()
    ta = prov.embed_text("dark kick")
    tb = prov.embed_text("bright hat")
    assert ta.vector != tb.vector


def test_clap_raises_when_not_installed():
    with pytest.raises(EmbeddingProviderUnavailable):
        ClapEmbeddingProvider()


def test_factory_default_stub():
    assert isinstance(get_embedding_provider(), StubEmbeddingProvider)
    assert isinstance(get_embedding_provider("stub"), StubEmbeddingProvider)
    with pytest.raises(EmbeddingProviderUnavailable):
        get_embedding_provider("clap")
    with pytest.raises(ValueError):
        get_embedding_provider("nope")


def test_compute_embeddings_stores_ref(tmp_path):
    _make_wav(tmp_path / "kick.wav")
    sha = "a" * 64
    asset = SampleAsset(
        id="asset_1", path=str(tmp_path / "kick.wav"), filename="kick.wav",
        library_root=str(tmp_path), relative_path="kick.wav", extension=".wav",
        size_bytes=10, sha256=sha, status=AssetStatus.INDEXED,
    )
    idx = LibraryIndex(assets={sha: asset})
    counts = compute_embeddings(idx, StubEmbeddingProvider())
    assert counts["embedded"] == 1
    assert idx.assets[sha].embedding is not None
    assert idx.assets[sha].embedding.provider == "stub"
    assert idx.assets[sha].embedding.reference == sha
