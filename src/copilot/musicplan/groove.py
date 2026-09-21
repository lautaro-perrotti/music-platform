"""Groovy/Latin tech house drum patterns (RHYTHM_V1).

Pure functions -> list[MidiNote]. pitch = drum slot (General-MIDI-ish), start_time
in beats, duration in beats, velocity = accent. Velocity + micro-timing variation
IS the groove (syncopation, ghost hits, offbeat push, swing).

Hierarchy: GROOVE > SOUND SELECTION > RHYTHM > ARRANGEMENT > MIXING.
"""

from __future__ import annotations

from copilot.schemas.session import MidiNote

# Slot map: pitch -> drum role. A Drum Rack / Simpler loads the sample on this pad.
DRUM_MAP: dict[str, int] = {
    "Kick": 36,        # C1
    "Clap": 38,        # D1
    "Closed Hat": 42,  # F#1
    "Open Hat": 46,    # A#1
    "Shaker": 44,      # G#1
    "Conga": 62,       # D3
    "Clave": 75,       # D#5
    "Rim": 37,         # C#1
    "Bass": 35,        # B0
}

SWING_16TH = 0.03  # push offbeat 16ths slightly late (controlled swing)


def kick_pattern(bars: int = 1, velocity: int = 100) -> list[MidiNote]:
    """Four-on-the-floor with subtle velocity variation (not perfectly flat)."""
    vels = [velocity, velocity - 2, velocity + 2, velocity - 4]
    notes: list[MidiNote] = []
    for bar in range(bars):
        for beat in range(4):
            notes.append(
                MidiNote(
                    pitch=DRUM_MAP["Kick"],
                    start_time=float(bar * 4 + beat),
                    duration=0.25,
                    velocity=vels[beat],
                )
            )
    return notes


def clap_pattern(bars: int = 1, velocity: int = 90) -> list[MidiNote]:
    """Clap on 2 & 4, plus a ghost clap before beat 4 (subtle variation)."""
    notes: list[MidiNote] = []
    for bar in range(bars):
        base = float(bar * 4)
        notes.append(MidiNote(pitch=DRUM_MAP["Clap"], start_time=base + 1.0, duration=0.25, velocity=velocity))
        notes.append(MidiNote(pitch=DRUM_MAP["Clap"], start_time=base + 3.0, duration=0.25, velocity=velocity))
        notes.append(MidiNote(pitch=DRUM_MAP["Clap"], start_time=base + 3.75, duration=0.125, velocity=velocity - 35))
    return notes


def closed_hat_pattern(bars: int = 1, velocity: int = 70) -> list[MidiNote]:
    """Offbeat 16ths (swung) + two ghost 16ths for movement."""
    notes: list[MidiNote] = []
    for bar in range(bars):
        base = float(bar * 4)
        for beat in range(4):
            notes.append(
                MidiNote(
                    pitch=DRUM_MAP["Closed Hat"],
                    start_time=base + beat + 0.5 + SWING_16TH,
                    duration=0.125,
                    velocity=velocity,
                )
            )
        for ghost in (0.25, 0.75):
            notes.append(
                MidiNote(
                    pitch=DRUM_MAP["Closed Hat"],
                    start_time=base + ghost,
                    duration=0.125,
                    velocity=velocity - 30,
                )
            )
    return notes


def shaker_pattern(bars: int = 1, base_velocity: int = 60) -> list[MidiNote]:
    """16ths with offbeat accent — the shaker pushes the groove forward."""
    notes: list[MidiNote] = []
    for bar in range(bars):
        base = float(bar * 4)
        for i in range(16):
            on_offbeat = (i % 2) == 1
            vel = base_velocity + 15 if on_offbeat else base_velocity - 5
            notes.append(
                MidiNote(
                    pitch=DRUM_MAP["Shaker"],
                    start_time=base + i * 0.25,
                    duration=0.125,
                    velocity=vel,
                )
            )
    return notes


def conga_pattern(bars: int = 1, velocity: int = 80) -> list[MidiNote]:
    """Latin syncopated conga (tumbao-ish) with call-and-response velocity."""
    hits = [0.0, 0.75, 1.5, 2.0, 2.75, 3.5]
    vels = [velocity, velocity - 15, velocity, velocity - 10, velocity - 15, velocity]
    notes: list[MidiNote] = []
    for bar in range(bars):
        base = float(bar * 4)
        for h, v in zip(hits, vels):
            notes.append(
                MidiNote(pitch=DRUM_MAP["Conga"], start_time=base + h, duration=0.25, velocity=v)
            )
    return notes


def clave_pattern(bars: int = 1, velocity: int = 85) -> list[MidiNote]:
    """Son clave 3-2: 0, 0.75, 1.5 | 2.5, 3.0."""
    hits = [0.0, 0.75, 1.5, 2.5, 3.0]
    notes: list[MidiNote] = []
    for bar in range(bars):
        base = float(bar * 4)
        for h in hits:
            notes.append(
                MidiNote(pitch=DRUM_MAP["Clave"], start_time=base + h, duration=0.125, velocity=velocity)
            )
    return notes


def bass_pattern(bars: int = 1, velocity: int = 95) -> list[MidiNote]:
    """Funky syncopated bass: short notes interacting with the kick (offbeat + pickup)."""
    hits = [(0.5, 0.25), (1.25, 0.25), (1.5, 0.25), (2.5, 0.5), (3.5, 0.25)]
    notes: list[MidiNote] = []
    for bar in range(bars):
        base = float(bar * 4)
        for start, dur in hits:
            notes.append(
                MidiNote(pitch=DRUM_MAP["Bass"], start_time=base + start, duration=dur, velocity=velocity)
            )
    return notes


def full_groove(bars: int = 1) -> list[MidiNote]:
    """Combined groove (kick+clap+hats+shaker+conga+clave+bass), sorted by time."""
    notes = (
        kick_pattern(bars)
        + clap_pattern(bars)
        + closed_hat_pattern(bars)
        + shaker_pattern(bars)
        + conga_pattern(bars)
        + clave_pattern(bars)
        + bass_pattern(bars)
    )
    return sorted(notes, key=lambda n: (n.start_time, n.pitch))
