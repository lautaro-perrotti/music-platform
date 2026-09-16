from __future__ import annotations

from copilot.schemas.session import MidiNote

# Ableton song time: 1 beat = 1 quarter-note time unit (quarter_note_position).
# This is not the felt pulse. In 6/8 the bar has 3 quarter-note units,
# while a musician may count 2 dotted-quarter pulses. API callers must
# say bar / quarter_note_position / subdivision, not an ambiguous "beat".


def beats_per_bar(numerator: int, denominator: int) -> float:
    if numerator <= 0 or denominator <= 0:
        raise ValueError("invalid time signature")
    return numerator * (4.0 / denominator)


def bars_to_beats(bars: int, numerator: int, denominator: int) -> float:
    return float(bars) * beats_per_bar(numerator, denominator)


def quarter_starts(bars: int, numerator: int, denominator: int) -> list[float]:
    total = bars_to_beats(bars, numerator, denominator)
    starts: list[float] = []
    beat = 0.0
    while beat < total - 1e-9:
        starts.append(beat)
        beat += 1.0
    return starts


def notes_on_beats(
    *,
    pitch: int = 60,
    bars: int = 4,
    numerator: int = 4,
    denominator: int = 4,
    duration: float = 1.0,
    velocity: int = 100,
) -> list[MidiNote]:
    return [
        MidiNote(pitch=pitch, start_time=start, duration=duration, velocity=velocity)
        for start in quarter_starts(bars, numerator, denominator)
    ]
