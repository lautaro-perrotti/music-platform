import struct

import pytest

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.protocol import COMMAND_CAPABILITY
from copilot.music_source.editable_reconstruction_v1 import (
    PPQ, EditableNote, encode_standard_midi, reconstruct_bass_notes,
    source_seconds_to_live_qn, write_standard_midi,
)
from copilot.schemas.musical_understanding import BassPitchEvent, MusicalGridPoint


MARKERS = [
    {"source_s": 0.0, "live_qn": 0.0},
    {"source_s": 30.0, "live_qn": 64.0},
]


def test_authoritative_warp_mapping_preserves_off_grid_onset():
    actual = source_seconds_to_live_qn(4.037, MARKERS)
    assert actual == pytest.approx(4.037 * 64 / 30)
    assert actual != round(actual * 4) / 4
    with pytest.raises(ValueError, match="OUTSIDE"):
        source_seconds_to_live_qn(31.0, MARKERS)


def test_warp_mapping_rejects_nonmonotonic_or_invalid_evidence():
    with pytest.raises(ValueError, match="NOT_MONOTONIC"):
        source_seconds_to_live_qn(1.0, [MARKERS[0], {"source_s": 30, "live_qn": -1}])
    with pytest.raises(ValueError, match="INVALID"):
        source_seconds_to_live_qn(float("nan"), MARKERS)


def test_warp_readback_is_typed_and_read_only(monkeypatch):
    assert COMMAND_CAPABILITY["get_warp_markers"] == "session.read"
    assert COMMAND_CAPABILITY["get_clip_start_end_markers"] == "session.read"
    calls = []
    adapter = AbletonTcpAdapter()
    monkeypatch.setattr(adapter, "_command", lambda command, payload: calls.append((command, payload)) or {"ok": True})
    adapter.get_warp_markers(4, 0)
    adapter.get_clip_start_end_markers(4, 0)
    assert calls == [
        ("get_warp_markers", {"track_index": 4, "clip_index": 0}),
        ("get_clip_start_end_markers", {"track_index": 4, "clip_index": 0}),
    ]


def _pitch(onset: float, *, status: str = "RELIABLE") -> BassPitchEvent:
    return BassPitchEvent(
        event_id=f"bass:{onset}",
        grid=MusicalGridPoint(
            onset_s=onset, onset_qn=onset * 2, bar=1, beat_in_bar=1,
            subdivision="none", nearest_grid_qn=0,
            deviation_qn=0, deviation_ms=0,
        ),
        offset_s=0.333, midi_note=35, confidence=0.7, status=status,
    )


def test_bass_excludes_uncertain_notes_and_preserves_source_duration():
    notes, report = reconstruct_bass_notes(
        [_pitch(4.037), _pitch(5.0, status="UNKNOWN")], warp_markers=MARKERS,
    )
    assert len(notes) == 1
    assert report["deferred_candidates"] == 1
    assert notes[0].start_qn == pytest.approx(4.037 * 64 / 30)
    assert notes[0].duration_qn == pytest.approx(0.333 * 64 / 30)
    assert notes[0].confidence_status == "AUDIO_PYIN_RELIABLE_UNCALIBRATED"


def _read_varlen(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position


def test_midi_serialization_preserves_microtiming_at_tick_precision(tmp_path):
    note = EditableNote(
        role="BASS", pitch=35, start_qn=4.037,
        duration_qn=0.333, velocity=80,
        source_onset_s=1.9, source_end_s=2.233,
        source_event_id="e1", confidence_status="INFERRED",
    )
    path = tmp_path / "bass.mid"
    write_standard_midi(path, [note], tempo_bpm=125.0, channel=0, track_name="BASS")
    data = path.read_bytes()
    assert data[:4] == b"MThd"
    assert struct.unpack(">IHHH", data[4:14]) == (6, 0, 1, PPQ)
    assert data[14:18] == b"MTrk"
    position = 22
    tick = 0
    onsets = []
    ends = []
    while position < len(data):
        delta, position = _read_varlen(data, position)
        tick += delta
        status = data[position]
        position += 1
        if status == 0xFF:
            meta = data[position]
            position += 1
            length, position = _read_varlen(data, position)
            position += length
            if meta == 0x2F:
                break
        else:
            pitch, velocity = data[position:position + 2]
            position += 2
            if status == 0x90 and velocity:
                onsets.append((tick, pitch, velocity))
            if status == 0x80:
                ends.append(tick)
    assert onsets == [(round(4.037 * PPQ), 35, 80)]
    assert ends == [round((4.037 + 0.333) * PPQ)]
    with pytest.raises(FileExistsError):
        write_standard_midi(path, [note], tempo_bpm=125.0, channel=0, track_name="BASS")
    assert encode_standard_midi([note], tempo_bpm=125, channel=0, track_name="BASS") == data
