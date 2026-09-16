from __future__ import annotations

import random

from copilot.agent.slices import run_create_c3_clip, run_undo_last
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.identities import fingerprint_track, resolve_by_fingerprint
from copilot.daw.mock import MockAbletonAdapter
from copilot.schemas.transaction import TransactionStatus


def _tools() -> AgentTools:
    daw = MockAbletonAdapter()
    daw.connect()
    return AgentTools(daw, TransactionManager(daw))


def test_duplicate_names_do_not_cross_bind_rollback() -> None:
    tools = _tools()
    tools.daw.create_midi_track("Bass")
    run_create_c3_clip(tools, "Temp")
    tools.daw.set_track_name(tools.get_session_snapshot().track_by_name("Temp").index, "Bass")
    undone = run_undo_last(tools, expected_track="__none__")
    assert undone["status"] == TransactionStatus.ROLLED_BACK.value
    assert len(tools.get_session_snapshot().tracks) == 1


def test_insert_before_after_and_reorder() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    created = tools.get_session_snapshot().tracks[0]
    tools.daw.create_midi_track("Before", index=0)
    tools.daw.create_midi_track("After")
    state = tools.get_session_snapshot()
    moved = state.track_by_id(created.stable_id)
    assert moved.index == 1
    undone = run_undo_last(tools)
    names = [track.name for track in tools.get_session_snapshot().tracks]
    assert "AI Test" not in names
    assert "Before" in names and "After" in names


def test_same_devices_two_tracks_conflict_after_incarnation_change() -> None:
    tools = _tools()
    tools.daw.create_midi_track("A")
    tools.daw.create_midi_track("B")
    state = tools.get_session_snapshot()
    expected = fingerprint_track(state.tracks[0])
    state.session_incarnation_id = "other"
    assert resolve_by_fingerprint(state, expected) is None


def test_clip_added_or_removed_conflicts() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    tools.daw.create_midi_clip(0, 1, 4.0)
    txn = tools.transactions.rollback_last()
    assert txn.status == TransactionStatus.ROLLBACK_CONFLICT
    assert len(tools.get_session_snapshot().tracks[0].clips) == 2


def test_random_identity_never_guesses() -> None:
    rng = random.Random(7)
    for _ in range(20):
        tools = _tools()
        run_create_c3_clip(tools)
        action = tools.transactions.last_own().actions[0]
        if rng.choice([True, False]):
            tools.daw.create_midi_track("Other", index=0)
        if rng.choice([True, False]):
            tools.daw.set_track_name(tools.get_session_snapshot().tracks[-1].index, "Renamed")
        if rng.choice([True, False]):
            tools.daw.create_midi_track("Twin")
        session = tools.get_session_snapshot()
        resolved = resolve_by_fingerprint(
            session,
            action.target_fingerprint.model_dump(),
            stable_id=action.target_stable_id,
            session_incarnation_id=action.session_incarnation_id,
        )
        if resolved is None:
            try:
                tools.transactions.rollback_last()
            except Exception:
                pass
            latest = tools.transactions.history[-1]
            assert latest.status in {
                TransactionStatus.ROLLBACK_CONFLICT,
                TransactionStatus.ROLLED_BACK,
            }
            continue
        tools.transactions.rollback_last()
        leftover = [
            track
            for track in tools.get_session_snapshot().tracks
            if track.stable_id == resolved.stable_id
        ]
        assert leftover == []
