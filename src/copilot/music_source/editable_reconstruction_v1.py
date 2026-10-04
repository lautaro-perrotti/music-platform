"""Evidence-bound drums/bass MIDI material from existing analyzers.

This module neither separates audio nor writes to Live. It preserves source
times and uses authoritative audio-clip warp markers for Live QN placement.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copilot.audio.drum_events_v1 import DrumEventSetV1
from copilot.audio.musical_understanding_v1 import BassPitchEvent
from copilot.musicplan.drum_reconstruction_v1 import build_drum_reconstruction


PPQ = 960


@dataclass(frozen=True)
class EditableNote:
    role: str
    pitch: int
    start_qn: float
    duration_qn: float
    velocity: int
    source_onset_s: float
    source_end_s: float | None
    source_event_id: str
    confidence_status: str


def source_seconds_to_live_qn(seconds: float, markers: list[dict[str, float]]) -> float:
    """Interpolate observed source-second/Live-beat pairs, never snap to grid."""
    if not math.isfinite(seconds) or seconds < 0 or len(markers) < 2:
        raise ValueError("WARP_MAPPING_INVALID")
    ordered = sorted(markers, key=lambda row: float(row["source_s"]))
    if any(
        not math.isfinite(float(row["source_s"]))
        or not math.isfinite(float(row["live_qn"]))
        for row in ordered
    ):
        raise ValueError("WARP_MARKER_NONFINITE")
    if any(
        float(right["source_s"]) <= float(left["source_s"])
        or float(right["live_qn"]) <= float(left["live_qn"])
        for left, right in zip(ordered, ordered[1:])
    ):
        raise ValueError("WARP_MARKERS_NOT_MONOTONIC")
    if seconds < float(ordered[0]["source_s"]) or seconds > float(ordered[-1]["source_s"]):
        raise ValueError("SOURCE_TIME_OUTSIDE_WARP_MAPPING")
    for left, right in zip(ordered, ordered[1:]):
        a, b = float(left["source_s"]), float(right["source_s"])
        if a <= seconds <= b:
            fraction = (seconds - a) / (b - a)
            return float(left["live_qn"]) + fraction * (
                float(right["live_qn"]) - float(left["live_qn"])
            )
    raise ValueError("SOURCE_TIME_NOT_MAPPED")


def reconstruct_drum_notes(
    event_set: DrumEventSetV1,
    *,
    beat_timestamps_s: list[float],
    warp_markers: list[dict[str, float]],
    kick_beat_tolerance_s: float = 0.09,
) -> tuple[list[EditableNote], dict[str, Any]]:
    """One supported LF attack per observed beat; conservative hats only.

    The beat map selects a measured attack but never supplies its MIDI onset.
    All other detected attacks remain visible in the deferred count.
    """
    if not beat_timestamps_s or kick_beat_tolerance_s <= 0:
        raise ValueError("BEAT_EVIDENCE_REQUIRED")
    selected: dict[str, Any] = {}
    missing_beats = 0
    for beat in beat_timestamps_s:
        candidates = [
            event for event in event_set.events
            if abs(event.onset_seconds - beat) <= kick_beat_tolerance_s
            and event.effective_role == "KICK"
            and (event.role_hypothesis.features is not None)
            and (event.role_hypothesis.features.low_band_energy_fraction_20_150_hz or 0) >= 0.55
        ]
        if not candidates:
            missing_beats += 1
            continue
        chosen = max(candidates, key=lambda item: item.strength)
        selected[chosen.event_id] = chosen
    for event in event_set.events:
        if event.effective_role == "CLOSED_HAT":
            selected[event.event_id] = event
    ordered = sorted(selected.values(), key=lambda event: event.onset_seconds)
    # Reuse the existing per-role accent→velocity mapping. The role and gate
    # remain inferred/symbolic, not human-confirmed acoustic note duration.
    selected_set = event_set.model_copy(update={"events": ordered})
    symbolic = build_drum_reconstruction(selected_set)
    notes = [
        EditableNote(
            role=event.role,
            pitch=event.midi_note,
            start_qn=source_seconds_to_live_qn(event.observed_onset_seconds, warp_markers),
            duration_qn=float(event.note_duration_qn or 1 / 32),
            velocity=int(event.velocity or 64),
            source_onset_s=event.observed_onset_seconds,
            source_end_s=None,
            source_event_id=event.source_event_id,
            confidence_status="INFERRED_UNCALIBRATED",
        )
        for event in symbolic.events
    ]
    deferred = len(event_set.events) - len(ordered)
    return notes, {
        "candidate_attacks": len(event_set.events),
        "selected_events": len(notes),
        "kick_events": sum(note.role == "KICK" for note in notes),
        "closed_hat_events": sum(note.role == "CLOSED_HAT" for note in notes),
        "deferred_attacks": deferred,
        "beat_candidates_without_kick": missing_beats,
        "role_authority": "INFERRED_UNCALIBRATED",
        "duration_authority": "SYMBOLIC_TRIGGER_GATE_NOT_ACOUSTIC_DURATION",
        "velocity_authority": "ROLE_RELATIVE_ACCENT_PROXY",
    }


def reconstruct_bass_notes(
    pitch_events: list[BassPitchEvent], *, warp_markers: list[dict[str, float]],
) -> tuple[list[EditableNote], dict[str, Any]]:
    notes: list[EditableNote] = []
    deferred = 0
    for event in pitch_events:
        if event.status != "RELIABLE" or event.midi_note is None or event.offset_s <= 0:
            deferred += 1
            continue
        onset = float(event.grid.onset_s)
        end = onset + float(event.offset_s)
        start_qn = source_seconds_to_live_qn(onset, warp_markers)
        end_qn = source_seconds_to_live_qn(end, warp_markers)
        if end_qn <= start_qn:
            deferred += 1
            continue
        notes.append(EditableNote(
            role="BASS", pitch=event.midi_note,
            start_qn=start_qn, duration_qn=end_qn - start_qn,
            velocity=80,  # explicit neutral proxy; audio loudness is not MIDI velocity
            source_onset_s=onset, source_end_s=end,
            source_event_id=event.event_id,
            confidence_status="AUDIO_PYIN_RELIABLE_UNCALIBRATED",
        ))
    return notes, {
        "pitch_candidates": len(pitch_events),
        "accepted_notes": len(notes), "deferred_candidates": deferred,
        "velocity_authority": "NEUTRAL_PROXY_NOT_MEASURED",
        "pitch_authority": "AUDIO_PYIN_INFERRED_NOT_HUMAN_VERIFIED",
        "expression": "PITCH_BEND_UNREPRESENTED",
    }


def _vlq(value: int) -> bytes:
    if value < 0:
        raise ValueError("NEGATIVE_MIDI_DELTA")
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(parts)


def encode_standard_midi(
    notes: list[EditableNote], *, tempo_bpm: float, channel: int, track_name: str,
) -> bytes:
    """Minimal SMF-0 serialization; only tick rounding, no musical quantize."""
    if not notes or not (0 <= channel <= 15) or channel == 9 and track_name == "BASS":
        raise ValueError("MIDI_NOTES_OR_CHANNEL_INVALID")
    if not math.isfinite(tempo_bpm) or tempo_bpm <= 0:
        raise ValueError("MIDI_TEMPO_INVALID")
    tempo_us = round(60_000_000 / tempo_bpm)
    if not (1 <= tempo_us < 1 << 24):
        raise ValueError("MIDI_TEMPO_OUT_OF_RANGE")
    name = track_name.encode("utf-8")
    events: list[tuple[int, int, bytes]] = [
        (0, 0, b"\xff\x03" + _vlq(len(name)) + name),
        (0, 1, b"\xff\x51\x03" + tempo_us.to_bytes(3, "big")),
    ]
    for note in notes:
        if not (0 <= note.pitch <= 127 and 1 <= note.velocity <= 127):
            raise ValueError("MIDI_NOTE_VALUE_OUT_OF_RANGE")
        if not math.isfinite(note.start_qn) or not math.isfinite(note.duration_qn) or note.start_qn < 0 or note.duration_qn <= 0:
            raise ValueError("MIDI_NOTE_TIME_INVALID")
        start = round(note.start_qn * PPQ)
        end = max(start + 1, round((note.start_qn + note.duration_qn) * PPQ))
        events.append((start, 3, bytes([0x90 | channel, note.pitch, note.velocity])))
        events.append((end, 2, bytes([0x80 | channel, note.pitch, 0])))
    events.sort(key=lambda event: (event[0], event[1]))
    body = bytearray()
    previous = 0
    for tick, _, payload in events:
        body.extend(_vlq(tick - previous))
        body.extend(payload)
        previous = tick
    body.extend(b"\x00\xff\x2f\x00")
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ) + b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def write_standard_midi(
    path: Path, notes: list[EditableNote], *, tempo_bpm: float, channel: int, track_name: str,
) -> None:
    payload = encode_standard_midi(notes, tempo_bpm=tempo_bpm, channel=channel, track_name=track_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
