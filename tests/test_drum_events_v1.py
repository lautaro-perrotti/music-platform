from __future__ import annotations

import hashlib

import numpy as np
import soundfile as sf
import pytest

from copilot.audio.drum_events_v1 import apply_human_role_correction, build_drum_event_set


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


def test_conservative_role_hypotheses_include_features_and_keep_unknown(tmp_path):
    source = tmp_path / "two-roles.wav"
    rate = 16_000
    audio = np.zeros(rate * 2, dtype=np.float32)
    for onset, frequency, duration in ((0.4, 80.0, 0.08), (1.0, 6_000.0, 0.03)):
        start = int(onset * rate)
        count = int(duration * rate)
        time = np.arange(count, dtype=np.float32) / rate
        audio[start : start + count] = 0.8 * np.sin(2 * np.pi * frequency * time) * np.exp(-time / 0.01)
    sf.write(source, audio, rate)
    result = build_drum_event_set(
        source,
        source_asset_id="asset-roles",
        source_sha256=_sha256(source),
        tempo_bpm=120,
        tempo_source="fixture-only",
        region_start_seconds=0,
        region_end_seconds=2,
    )

    inferred = [event for event in result.events if event.role_hypothesis.status == "INFERRED"]
    assert {event.role_hypothesis.role for event in inferred} == {"KICK", "CLOSED_HAT"}
    assert all(event.role_hypothesis.confidence is None for event in inferred)
    assert all(event.role_hypothesis.confidence_basis == "UNCALIBRATED_RULES" for event in inferred)
    assert all(event.role_hypothesis.features is not None for event in result.events)
    assert result.role_classifier_version == "drum-role-rules-v1"


def test_human_role_correction_preserves_inference_and_is_idempotent(tmp_path):
    source = tmp_path / "drums.wav"
    _write_impulses(source)
    original = build_drum_event_set(
        source,
        source_asset_id="asset-human-correction",
        source_sha256=_sha256(source),
        tempo_bpm=120,
        tempo_source="fixture-only",
        region_start_seconds=0,
        region_end_seconds=2,
    )
    target = original.events[0]
    corrected = apply_human_role_correction(
        original,
        event_id=target.event_id,
        role="PERCUSSION",
        reviewer_id="producer-1",
        note="Heard as hand percussion",
        recorded_at_utc="2026-10-01T12:00:00Z",
    )
    changed = next(event for event in corrected.events if event.event_id == target.event_id)
    assert target.human_correction is None
    assert changed.role_hypothesis.model_dump() == target.role_hypothesis.model_dump()
    assert changed.effective_role == "PERCUSSION"
    assert changed.revision == 2
    assert changed.change_history[0].previous_effective_role == target.role_hypothesis.role
    assert changed.change_history[0].new_role == "PERCUSSION"

    repeated = apply_human_role_correction(
        corrected,
        event_id=target.event_id,
        role="PERCUSSION",
        reviewer_id="producer-1",
        note="Heard as hand percussion",
        recorded_at_utc="2026-10-01T12:01:00Z",
    )
    repeated_event = next(event for event in repeated.events if event.event_id == target.event_id)
    assert repeated_event.revision == 2
    assert len(repeated_event.change_history) == 1
