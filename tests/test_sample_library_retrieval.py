from __future__ import annotations

from copilot.sample_library.retrieval import (
    SampleRetriever,
    build_astra_sample_context,
    descriptor_distance,
)
from copilot.sample_library.schemas import (
    AssetStatus,
    AudioDescriptors,
    LibraryIndex,
    SampleAsset,
    SampleRole,
    SampleType,
)


def _asset(i, role, *, dur=0.4, centroid=800.0, low=0.6, mid=0.3, high=0.1, bpm=None, stype=SampleType.ONE_SHOT):
    sha = f"{i}" * 64
    return sha, SampleAsset(
        id=f"asset_{i}", path=f"/x/s{i}.wav", filename=f"s{i}.wav",
        library_root="/x", relative_path=f"s{i}.wav", extension=".wav",
        size_bytes=10, sha256=sha, semantic_role=role, sample_type=stype,
        bpm=__import__("copilot.sample_library.schemas", fromlist=["BpmEstimate"]).BpmEstimate(value=bpm, confidence=0.8 if bpm else None),
        descriptors=AudioDescriptors(duration_s=dur, spectral_centroid_hz=centroid,
                                     low_band_energy=low, mid_band_energy=mid, high_band_energy=high),
        status=AssetStatus.INDEXED,
    )


def _index():
    assets = {}
    sha, a = _asset(0, SampleRole.KICK, dur=0.4, centroid=800.0, low=0.6, bpm=128)
    assets[sha] = a
    sha, a = _asset(1, SampleRole.KICK, dur=0.5, centroid=2500.0, low=0.2, high=0.5, bpm=128)
    assets[sha] = a
    sha, a = _asset(2, SampleRole.OPEN_HAT, dur=0.8, centroid=6000.0, low=0.05, high=0.7)
    assets[sha] = a
    return LibraryIndex(assets=assets)


def test_descriptor_distance_closer_for_similar():
    a = AudioDescriptors(duration_s=0.4, spectral_centroid_hz=800.0, low_band_energy=0.6)
    b = AudioDescriptors(duration_s=0.45, spectral_centroid_hz=900.0, low_band_energy=0.55)
    c = AudioDescriptors(duration_s=2.0, spectral_centroid_hz=5000.0, low_band_energy=0.05)
    assert descriptor_distance(a, b) < descriptor_distance(a, c)


def test_search_samples_filters_role_and_bpm():
    r = SampleRetriever(_index())
    hits = r.search_samples(role=SampleRole.KICK, bpm=128.0)
    assert all(h.asset.semantic_role == SampleRole.KICK for h in hits)
    assert len(hits) == 2


def test_search_samples_one_shot_filter():
    r = SampleRetriever(_index())
    hits = r.search_samples(role=SampleRole.KICK, one_shot_or_loop=SampleType.ONE_SHOT)
    assert len(hits) == 2  # both kicks are one-shots


def test_retrieve_reference_like_ranks_by_descriptor():
    r = SampleRetriever(_index())
    ref = AudioDescriptors(duration_s=0.42, spectral_centroid_hz=850.0, low_band_energy=0.58)
    hits = r.retrieve_reference_like(role=SampleRole.KICK, reference_descriptors=ref)
    assert hits[0].asset.filename == "s0.wav"  # s0 (centroid 800) closer than s1 (2500)
    assert hits[0].descriptor_distance is not None


def test_build_astra_sample_context_no_write():
    r = SampleRetriever(_index())
    ctx = build_astra_sample_context(
        r, task_id="t1", reference_character={"role": "KICK", "notes": "short dry kick"},
        wanted_roles=[SampleRole.KICK, SampleRole.OPEN_HAT], bpm=128.0, per_role=2,
    )
    assert ctx["NO_WRITE"] is True
    assert "KICK" in ctx["candidates"]
