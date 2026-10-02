"""Deterministic, conservative triad voicings for inferred chord events."""

from __future__ import annotations

from copilot.audio.harmonic_understanding_v1 import PITCH_CLASSES
from copilot.schemas.chord_events import ChordEvent
from copilot.schemas.session import MidiNote


def chord_events_to_midi(events: list[ChordEvent], *, clip_length_qn: float) -> list[MidiNote]:
    notes: list[MidiNote] = []
    previous: tuple[int, int, int] | None = None
    for event in events:
        if event.quality not in {"major", "minor"}:
            raise ValueError("UNSUPPORTED_CHORD_QUALITY")
        root_pc = PITCH_CLASSES.index(event.root)
        third = 3 if event.quality == "minor" else 4
        options = []
        for octave_base in (48, 60):
            root = octave_base + root_pc
            root_position = (root, root + third, root + 7)
            options.extend((root_position,
                            (root + third, root + 7, root + 12),
                            (root + 7, root + 12, root + 12 + third)))
        chosen = min(options, key=lambda pitches: (
            sum(abs(a - b) for a, b in zip(pitches, previous)) if previous else abs(pitches[0] - 50),
            max(pitches), pitches,
        ))
        end_qn = min(clip_length_qn, event.start_qn + event.duration_qn)
        duration = end_qn - event.start_qn - 0.125
        if duration <= 0:
            raise ValueError("CHORD_EVENT_TOO_SHORT")
        notes.extend(MidiNote(pitch=pitch, start_time=event.start_qn,
                              duration=duration, velocity=75) for pitch in chosen)
        previous = chosen
    return notes
