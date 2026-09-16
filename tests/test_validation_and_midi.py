from __future__ import annotations

import math

from copilot.agent.tools import AgentTools, quarter_notes_c3
from copilot.agent.transactions import TransactionManager
from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.validation import validate_device_parameter, validate_midi_note, validate_mixer_volume
from copilot.daw.adapter import DawError
from copilot.midi.time import bars_to_beats, beats_per_bar, notes_on_beats
from copilot.schemas.session import DeviceParameter, MidiNote


def test_time_signatures() -> None:
    assert beats_per_bar(4, 4) == 4
    assert beats_per_bar(3, 4) == 3
    assert beats_per_bar(6, 8) == 3
    assert bars_to_beats(4, 4, 4) == 16
    assert bars_to_beats(4, 3, 4) == 12
    assert bars_to_beats(4, 6, 8) == 12
    notes_44 = notes_on_beats(pitch=60, bars=4, numerator=4, denominator=4)
    notes_34 = notes_on_beats(pitch=60, bars=4, numerator=3, denominator=4)
    notes_68 = notes_on_beats(pitch=60, bars=4, numerator=6, denominator=8)
    assert len(notes_44) == 16
    assert len(notes_34) == 12
    assert len(notes_68) == 12
    assert all(note.pitch == 60 for note in notes_44)
    assert "C3" not in str(quarter_notes_c3())


def test_numeric_validation_rejects_illegal_values() -> None:
    for bad in (math.nan, math.inf, -1, 1.5, "loud", None):
        try:
            validate_mixer_volume(bad)
            raise AssertionError(f"accepted {bad}")
        except DawError:
            pass
    assert validate_mixer_volume(0.0) == 0.0
    assert validate_mixer_volume(1.0) == 1.0
    parameter = DeviceParameter(index=0, name="Gain", value=0.5, min=-12.0, max=12.0)
    assert validate_device_parameter(0.0, parameter) == 0.0
    try:
        validate_device_parameter(40.0, parameter)
        raise AssertionError("device max must apply")
    except DawError:
        pass
    try:
        validate_midi_note(MidiNote(pitch=200, start_time=0, duration=1))
        raise AssertionError("illegal pitch")
    except DawError:
        pass


def test_tools_reject_before_daw() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    tools = AgentTools(daw, TransactionManager(daw))
    tools.transactions.begin("val", tools.get_session_snapshot())
    daw.create_midi_track("X")
    try:
        tools.set_mixer_volume(0, float("nan"))
        raise AssertionError("nan must not reach daw")
    except DawError:
        pass
    assert daw.snapshot().tracks[0].mixer.volume == 0.85
    tools.capabilities.discard("clip.write_notes")
    daw.create_midi_clip(0, 0, 4.0)
    try:
        tools.replace_clip_notes(0, 0, [MidiNote(pitch=60, start_time=0, duration=1)])
        raise AssertionError("missing capability must fail closed")
    except Exception as exc:
        assert "capability" in str(exc).lower() or "not advertised" in str(exc)
    assert daw.get_clip_notes(0, 0)["note_count"] == 0
