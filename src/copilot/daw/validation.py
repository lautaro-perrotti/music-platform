from __future__ import annotations

import math
from typing import Any

from copilot.daw.adapter import DawError
from copilot.schemas.session import DeviceParameter, MidiNote


def require_finite_number(value: Any, name: str) -> float:
    if value is None:
        raise DawError(f"{name} must not be null")
    if isinstance(value, bool) or isinstance(value, str):
        raise DawError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DawError(f"{name} must be a number") from exc
    if math.isnan(number) or math.isinf(number):
        raise DawError(f"{name} must be finite")
    return number


def require_int(value: Any, name: str) -> int:
    number = require_finite_number(value, name)
    if int(number) != number:
        raise DawError(f"{name} must be an integer")
    return int(number)


def require_range(value: Any, name: str, minimum: float, maximum: float) -> float:
    number = require_finite_number(value, name)
    if number < minimum or number > maximum:
        raise DawError(f"{name} out of range: {number} not in [{minimum}, {maximum}]")
    return number


def validate_mixer_volume(volume: Any) -> float:
    return require_range(volume, "volume", 0.0, 1.0)


def validate_device_parameter(value: Any, parameter: DeviceParameter | None = None) -> float:
    if parameter is None:
        return require_finite_number(value, "parameter")
    return require_range(value, parameter.name or "parameter", parameter.min, parameter.max)


def validate_midi_note(note: MidiNote) -> MidiNote:
    pitch = require_int(note.pitch, "pitch")
    if pitch < 0 or pitch > 127:
        raise DawError(f"pitch out of range: {pitch}")
    start = require_finite_number(note.start_time, "start_time")
    if start < 0:
        raise DawError("start_time must be >= 0")
    duration = require_finite_number(note.duration, "duration")
    if duration <= 0:
        raise DawError("duration must be > 0")
    velocity = require_int(note.velocity, "velocity")
    if velocity < 0 or velocity > 127:
        raise DawError(f"velocity out of range: {velocity}")
    return note


def validate_midi_notes(notes: list[MidiNote]) -> list[MidiNote]:
    return [validate_midi_note(note) for note in notes]
