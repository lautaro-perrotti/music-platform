"""Small tests for the reusable chord event and voicing boundary."""

from __future__ import annotations

import numpy as np
import pytest

from copilot.audio.chord_phrase_v1 import _rank_triads
from copilot.musicplan.chord_voicing import chord_events_to_midi
from copilot.musicplan.full_groove_lab_v1 import _same_notes
from copilot.schemas.chord_events import ChordEvent
from copilot.schemas.session import MidiNote


def test_ranked_triad_keeps_alternative_instead_of_claiming_ground_truth():
    weights = np.zeros(12)
    weights[2], weights[5], weights[9], weights[7] = .30, .23, .17, .10
    weights[10] = .03
    weights /= weights.sum()
    ranked = _rank_triads(weights)
    assert ranked[0]["label"] == "Dm"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert len(ranked) == 24


def test_chord_voicing_is_editable_and_inferred_only():
    event = ChordEvent(start_qn=0, duration_qn=16, root="D", quality="minor",
                       pitch_classes=["D", "F", "A"], confidence=.5,
                       evidence_refs=["sha256:source"])
    notes = chord_events_to_midi([event], clip_length_qn=16)
    assert [note.pitch for note in notes] == [50, 53, 57]
    assert all(note.start_time == 0 and note.duration == 15.875 for note in notes)
    with pytest.raises(ValueError, match="human authority"):
        ChordEvent.model_validate(event.model_dump() | {"authority": "HUMAN_VERIFIED"})


def test_authoritative_readback_uses_note_identity_not_lom_sort_order():
    expected = [MidiNote(pitch=50, start_time=0, duration=15.875, velocity=75),
                MidiNote(pitch=53, start_time=0, duration=15.875, velocity=75),
                MidiNote(pitch=50, start_time=16, duration=15.875, velocity=75)]
    observed = [note.model_dump() for note in (expected[2], expected[0], expected[1])]
    assert _same_notes(observed, expected)
    observed[0]["pitch"] = 51
    assert not _same_notes(observed, expected)
