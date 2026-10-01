from __future__ import annotations

import hashlib

import numpy as np
import soundfile as sf
import pytest

from copilot.audio.drum_events_v1 import build_drum_event_set


def _write_impulses(path, *, rate: int = 16_000, offset_samples: int = 0) -> None:
    samples = np.zeros(rate * 2, dtype=np.float32)
    for onset_s in (0.25, 0.75, 1.25, 1.75):
        index = int(onset_s * rate) + offset_samples
        if index + 16 < len(samples):
            samples[index:index + 16] = np.linspace(0.8, 0.05, 16, dtype=np.float32)
    sf.write(path, samples, rate)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_drum_events_keep_source_identity_time_and_unknown_roles(tmp_path):
    source = tmp_path / "drums.wav"
    _write_impulses(source)
    event_set = build_drum_event_set(
        source,
        source_asset_id="external-source-test",
        source_sha256=_sha256(source),
        tempo_bpm=120,
        tempo_source="test-hypothesis",
        tempo_label_hint_bpm=120,
        region_start_seconds=0,
        region_end_seconds=2,
    )

    assert len(event_set.events) == 4
    event = event_set.events[0]
    assert event.event_id.startswith("drum_evt_")
    assert event.source_asset_id == "external-source-test"
    assert event.source_slice.start_seconds <= event.onset_seconds
    assert event.source_slice.end_seconds > event.onset_seconds
    assert event.musical_position.tempo_status == "PROVISIONAL"
    assert event.role_hypothesis.role == "UNKNOWN"
    assert event.role_hypothesis.status == "UNCLASSIFIED"
    assert event.realized_midi_velocity is None
    assert event.accent_rms_dbfs is not None


def test_drum_event_ids_are_repeatable_and_content_bound(tmp_path):
    source = tmp_path / "drums.wav"
    other = tmp_path / "drums-offset.wav"
    _write_impulses(source)
    _write_impulses(other, offset_samples=1)

    kwargs = dict(
        source_asset_id="asset-a",
        source_sha256=_sha256(source),
        tempo_bpm=120,
        tempo_source="test-hypothesis",
        region_start_seconds=0,
        region_end_seconds=2,
    )
    first = build_drum_event_set(source, **kwargs)
    second = build_drum_event_set(source, **kwargs)
    assert [event.event_id for event in first.events] == [event.event_id for event in second.events]

    changed_source = build_drum_event_set(
        other,
        **{**kwargs, "source_sha256": _sha256(other), "source_asset_id": "asset-b"},
    )
    assert set(event.event_id for event in first.events).isdisjoint(
        event.event_id for event in changed_source.events
    )


def test_drum_event_builder_rejects_invalid_region(tmp_path):
    source = tmp_path / "drums.wav"
    _write_impulses(source)
    with pytest.raises(ValueError, match="DRUM_ANALYSIS_REGION_OUT_OF_BOUNDS"):
        build_drum_event_set(
            source,
            source_asset_id="asset",
            source_sha256=_sha256(source),
            tempo_bpm=120,
            tempo_source="test",
            region_start_seconds=0,
            region_end_seconds=3,
        )


def test_drum_event_builder_rejects_wrong_source_hash(tmp_path):
    source = tmp_path / "drums.wav"
    _write_impulses(source)
    with pytest.raises(ValueError, match="DRUM_SOURCE_SHA256_MISMATCH"):
        build_drum_event_set(
            source,
            source_asset_id="asset",
            source_sha256="0" * 64,
            tempo_bpm=120,
            tempo_source="test",
        )
