from __future__ import annotations

from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_hash import canonical_session, state_hash
from copilot.schemas.session import MidiNote


def _daw() -> MockAbletonAdapter:
    daw = MockAbletonAdapter()
    daw.connect()
    return daw


def test_identical_snapshots_keep_revision() -> None:
    daw = _daw()
    first = daw.snapshot()
    second = daw.snapshot()
    assert first.revision == second.revision == 0
    assert first.state_hash == second.state_hash
    assert first.state_hash != ""


def test_external_mutations_bump_revision() -> None:
    daw = _daw()
    start = daw.snapshot()
    daw.create_midi_track("User")
    after_insert = daw.snapshot()
    assert after_insert.revision == start.revision + 1
    daw.set_track_name(0, "Renamed")
    after_rename = daw.snapshot()
    assert after_rename.revision == after_insert.revision + 1
    daw.create_midi_clip(0, 0, 4.0)
    after_clip = daw.snapshot()
    assert after_clip.revision == after_rename.revision + 1
    daw.replace_clip_notes(
        0, 0, [MidiNote(pitch=60, start_time=0.0, duration=1.0)]
    )
    after_notes = daw.snapshot()
    assert after_notes.revision == after_clip.revision + 1
    daw.set_device_parameter(0, 0, 0, 0.1)
    after_param = daw.snapshot()
    assert after_param.revision == after_notes.revision + 1
    daw.delete_track(0)
    after_delete = daw.snapshot()
    assert after_delete.revision == after_param.revision + 1


def test_playback_cursor_does_not_bump_revision() -> None:
    daw = _daw()
    before = daw.snapshot()
    daw.transport.playing = True
    daw.transport.position_beats = 12.5
    after = daw.snapshot()
    assert after.revision == before.revision
    assert after.state_hash == before.state_hash


def test_float_jitter_does_not_change_hash() -> None:
    daw = _daw()
    daw.create_midi_track("X")
    state = daw.snapshot()
    state.tracks[0].mixer.volume += 1e-9
    assert state_hash(state) == daw.snapshot().state_hash
    canonical = canonical_session(state)
    assert "stable_id" not in str(canonical)


def test_stale_revision_sees_external_edit() -> None:
    daw = _daw()
    tools = AgentTools(daw, TransactionManager(daw))
    before = tools.get_session_snapshot()
    tools.transactions.begin("stale-external", before)
    daw.create_midi_track("External")
    try:
        tools.create_midi_track("AI Test", expected_revision=before.revision)
        raise AssertionError("expected stale revision")
    except Exception as exc:
        assert "Stale revision" in str(exc)
        tools.transactions.abort(str(exc))
    assert tools.transactions.history[-1].status.value != "VERIFIED"
