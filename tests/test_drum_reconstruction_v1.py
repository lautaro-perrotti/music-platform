from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.drum_events_v1 import DrumAttackFeatures, build_drum_event_set
from copilot.musicplan.drum_reconstruction_v1 import build_drum_reconstruction


def _event_set(tmp_path: Path):
    source = tmp_path / "drums.wav"
    samples = np.zeros((48_000, 1), dtype=np.float32)
    samples[[6_000, 18_000, 30_000, 42_000], 0] = [0.3, 0.5, 0.7, 0.9]
    sf.write(source, samples, 48_000)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = build_drum_event_set(
        source,
        source_asset_id="source-drum-stem",
        source_sha256=digest,
        tempo_bpm=120,
        tempo_source="TEST_FIXTURE",
        region_end_seconds=1,
    )
    for index, event in enumerate(result.events):
        event.role_hypothesis.role = "KICK" if index % 2 == 0 else "CLOSED_HAT"
        event.role_hypothesis.status = "INFERRED"
        event.role_hypothesis.features = DrumAttackFeatures(
            measured_window_start_seconds=event.onset_seconds,
            measured_window_end_seconds=event.onset_seconds + 0.1,
            spectral_centroid_hz=1200,
            low_band_energy_fraction_20_150_hz=0.65,
            high_band_energy_fraction_2000_12000_hz=0.4,
        )
    return result


def test_symbolic_reconstruction_preserves_source_timing_and_is_not_executable(tmp_path: Path) -> None:
    event_set = _event_set(tmp_path)
    result = build_drum_reconstruction(event_set)

    assert result.status == "SYMBOLIC_PROPOSAL_NOT_EXECUTABLE"
    assert result.source_sha256 == event_set.source_sha256
    assert result.musical_writes == 0
    assert len(result.events) == len(event_set.events)
    for source, realized in zip(event_set.events, result.events, strict=True):
        assert realized.source_event_id == source.event_id
        assert realized.onset_qn == source.musical_position.onset_qn
        assert realized.bar == source.musical_position.bar
        assert realized.beat_in_bar == source.musical_position.beat_in_bar
        assert realized.observed_onset_seconds == source.onset_seconds
        assert realized.micro_offset_ms == source.musical_position.micro_offset_ms
        assert realized.midi_note == (36 if source.effective_role == "KICK" else 42)
        assert realized.accent_rms_dbfs == source.accent_rms_dbfs
        assert 1 <= realized.velocity <= 127
        assert realized.ableton_status == "NOT_WRITTEN"
        assert realized.note_duration_qn is None
        assert realized.note_duration_status == "NOT_DERIVED_FROM_ONSET_EVIDENCE"
    assert "ROLE_HYPOTHESES_NOT_HUMAN_VERIFIED" in result.blockers
    assert "MUSICAL_GRID_NOT_HUMAN_VERIFIED" in result.blockers
    assert "SAFEWRITE_AND_LIVE_READBACK_NOT_PERFORMED" in result.blockers
    assert "MIDI_NOTE_DURATION_POLICY_NOT_DEFINED" in result.blockers


def test_unmapped_event_is_preserved_as_deferred_not_dropped(tmp_path: Path) -> None:
    event_set = _event_set(tmp_path)
    event_set.events[0].role_hypothesis.role = "UNKNOWN"
    result = build_drum_reconstruction(event_set)

    assert event_set.events[0].event_id in result.deferred_source_event_ids
    assert all(row.source_event_id != event_set.events[0].event_id for row in result.events)
    assert "UNMAPPED_SOURCE_EVENTS_PRESENT" in result.blockers
