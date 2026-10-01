from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import soundfile as sf
import pytest

from copilot.audio.drum_events_v1 import DrumAttackFeatures, build_drum_event_set
from copilot.sample_library.drum_matching_v1 import (
    match_drum_events,
    rank_event_candidates,
    rank_role_family_candidates,
)
from copilot.sample_library.schemas import (
    AssetStatus,
    AudioDescriptors,
    LibraryIndex,
    SampleAsset,
    SampleRole,
    SampleType,
)


def _event_set(tmp_path: Path):
    source = tmp_path / "drums.wav"
    signal = np.zeros((48_000, 1), dtype=np.float32)
    signal[3_000, 0] = 0.8
    sf.write(source, signal, 48_000)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    event_set = build_drum_event_set(
        source,
        source_asset_id="source-drums",
        source_sha256=digest,
        tempo_bpm=120,
        tempo_source="TEST",
        region_end_seconds=1,
    )
    event = event_set.events[0]
    event.role_hypothesis.role = "KICK"
    event.role_hypothesis.status = "INFERRED"
    event.role_hypothesis.features = DrumAttackFeatures(
        measured_window_start_seconds=0.05,
        measured_window_end_seconds=0.16,
        spectral_centroid_hz=120.0,
        low_band_energy_fraction_20_150_hz=0.9,
        mid_band_energy_fraction_150_2000_hz=0.08,
        high_band_energy_fraction_2000_12000_hz=0.02,
    )
    return event


def _asset(path: Path, *, centroid: float, low: float, sha: str, status=AssetStatus.INDEXED) -> SampleAsset:
    path.write_bytes(b"indexed sample fixture")
    return SampleAsset(
        id=f"sample-{sha[:8]}",
        path=str(path),
        filename=path.name,
        library_root=str(path.parent),
        relative_path=path.name,
        extension=".wav",
        size_bytes=path.stat().st_size,
        sha256=sha,
        sample_type=SampleType.ONE_SHOT,
        semantic_role=SampleRole.UNKNOWN,
        descriptors=AudioDescriptors(
            duration_s=0.4,
            spectral_centroid_hz=centroid,
            low_band_energy=low,
            mid_band_energy=0.08,
            high_band_energy=0.02,
        ),
        status=status,
    )


def test_kick_candidates_are_ranked_by_explicit_components_not_filename_role(tmp_path: Path) -> None:
    event = _event_set(tmp_path)
    close = _asset(tmp_path / "candidate-a.wav", centroid=180, low=0.86, sha="a" * 64)
    far = _asset(tmp_path / "candidate-b.wav", centroid=7_000, low=0.02, sha="b" * 64)
    index = LibraryIndex(roots=[str(tmp_path)], assets={close.sha256: close, far.sha256: far})

    result = rank_event_candidates(event, index)

    assert [item["asset_id"] for item in result] == [close.id, far.id]
    assert result[0]["ranking_distance"] < result[1]["ranking_distance"]
    assert {item["feature"] for item in result[0]["components"]} == {
        "spectral_centroid", "low_frequency_energy_tendency"
    }
    assert "not perceptual quality" in result[0]["ranking_distance_semantics"]
    assert result[0]["sample_role_metadata"]["used_to_filter_or_rank"] is False
    assert result[0]["provisional"] is True
    assert result[0]["selected_for_realization"] is False


def test_unclassified_events_and_non_indexed_assets_are_not_matched(tmp_path: Path) -> None:
    event = _event_set(tmp_path)
    unsupported = _asset(
        tmp_path / "unavailable.wav", centroid=120, low=0.9,
        sha="c" * 64, status=AssetStatus.DECODE_FAILED,
    )
    index = LibraryIndex(roots=[str(tmp_path)], assets={unsupported.sha256: unsupported})
    event.role_hypothesis.role = "UNKNOWN"
    assert rank_event_candidates(event, index) == []

    event.role_hypothesis.role = "KICK"
    assert rank_event_candidates(event, index) == []


def test_matching_is_bounded_and_deterministic_with_deduplicated_index(tmp_path: Path) -> None:
    event = _event_set(tmp_path)
    asset = _asset(tmp_path / "one.wav", centroid=130, low=0.8, sha="d" * 64)
    index = LibraryIndex(
        roots=[str(tmp_path)],
        assets={asset.sha256: asset},
        duplicates={asset.sha256: [str(tmp_path / "same-audio-copy.wav")]},
    )

    first = match_drum_events([event], index, top_k=1)
    second = match_drum_events([event], index, top_k=1)
    assert first == second
    assert first["status"] == "CANDIDATES_READY"
    assert first["events_with_candidates"] == 1
    assert len(first["event_candidates"][event.event_id]) == 1

    with pytest.raises(ValueError, match="top_k"):
        rank_event_candidates(event, index, top_k=MAX_TOP_K + 1)


def test_role_family_matching_uses_only_explicit_provider_pool_and_aggregates_events(tmp_path: Path) -> None:
    first = _event_set(tmp_path)
    second = first.model_copy(deep=True)
    second.event_id = "second-kick-event"
    second.onset_seconds = 0.25
    close = _asset(tmp_path / "provider-kick.wav", centroid=180, low=0.86, sha="e" * 64)
    far = _asset(tmp_path / "provider-hat.wav", centroid=7_000, low=0.02, sha="f" * 64)
    index = LibraryIndex(roots=[str(tmp_path)], assets={close.sha256: close, far.sha256: far})

    result = rank_role_family_candidates(
        [first, second],
        index,
        role="KICK",
        eligible_sha256={close.sha256},
    )

    assert [row["asset_id"] for row in result] == [close.id]
    assert result[0]["compared_source_event_count"] == 2
    assert result[0]["reference_source_event_count"] == 2
    assert result[0]["provisional"] is True
    assert result[0]["human_selected"] is False
    assert result[0]["selected_for_realization"] is False


MAX_TOP_K = 5
