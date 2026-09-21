from __future__ import annotations

from copilot.agent.slices import run_create_c3_clip, run_undo_last
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.mock import MockAbletonAdapter
from copilot.schemas.transaction import TransactionStatus
from copilot.daw.identities import IdentityRegistry


def _tools() -> AgentTools:
    daw = MockAbletonAdapter()
    daw.connect()
    return AgentTools(daw, TransactionManager(daw))


def test_rollback_does_not_use_name_when_two_tracks_share_it() -> None:
    tools = _tools()
    tools.daw.create_midi_track("AI Test")
    first = tools.get_session_snapshot().track_by_name("AI Test")
    assert first is not None
    first_id = first.stable_id
    run_create_c3_clip(tools, "Temp")
    tools.daw.set_track_name(
        tools.get_session_snapshot().track_by_name("Temp").index, "AI Test"
    )
    names = [track.name for track in tools.get_session_snapshot().tracks]
    assert names.count("AI Test") == 2
    undone = run_undo_last(tools, expected_track="__none__")
    assert undone["status"] == TransactionStatus.ROLLED_BACK.value
    after = tools.get_session_snapshot()
    assert len(after.tracks) == 1
    assert after.tracks[0].stable_id == first_id
    assert after.tracks[0].name == "AI Test"


def test_rollback_survives_rename_when_fingerprint_holds() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    track = tools.get_session_snapshot().track_by_name("AI Test")
    assert track is not None
    tools.daw.set_track_name(track.index, "Renamed")
    undone = run_undo_last(tools, expected_track="Renamed")
    assert undone["status"] == TransactionStatus.ROLLED_BACK.value
    after = tools.get_session_snapshot()
    assert after.tracks == []


def test_rollback_conflict_when_target_deleted() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    tools.daw.delete_track(0)
    txn = tools.transactions.rollback_last()
    assert txn.status == TransactionStatus.ROLLBACK_CONFLICT
    assert tools.get_session_snapshot().tracks == []


def test_rollback_uses_current_locator_after_insert_before() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    created = tools.get_session_snapshot().track_by_name("AI Test")
    assert created is not None
    created_id = created.stable_id
    tools.daw.create_midi_track("Inserted", index=0)
    after_insert = tools.get_session_snapshot()
    moved = after_insert.track_by_id(created_id)
    assert moved.index == 1
    txn = tools.transactions.last_own()
    assert txn is not None
    assert txn.actions[0].target_locator_at_apply.track_index == 0
    undone = run_undo_last(tools)
    assert undone["status"] == TransactionStatus.ROLLED_BACK.value
    after = tools.get_session_snapshot()
    assert [track.name for track in after.tracks] == ["Inserted"]
    assert after.tracks[0].stable_id != created_id


def test_rollback_conflict_on_fingerprint_mismatch_touches_nothing() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    before = tools.get_session_snapshot()
    tools.daw.create_midi_clip(0, 1, 4.0)
    txn = tools.transactions.rollback_last()
    assert txn.status == TransactionStatus.ROLLBACK_CONFLICT
    after = tools.get_session_snapshot()
    assert len(after.tracks) == 1
    assert after.tracks[0].stable_id == before.tracks[0].stable_id
    assert len(after.tracks[0].clips) == 2


def test_recorded_action_has_stable_id_not_name_as_identity() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    txn = tools.transactions.last_own()
    assert txn is not None
    for action in txn.actions:
        assert action.target_stable_id.startswith("trk_")
        assert action.target_locator_at_apply.track_index == 0
        assert action.target_fingerprint.role == "midi"
        assert action.target_name_at_apply == "AI Test"
        assert "track_name" not in action.inverse_params


def test_creation_rollback_survives_session_identity_rebind_without_deleting_originals() -> None:
    tools = _tools()
    tools.daw.create_midi_track("Original")
    run_create_c3_clip(tools, "Alpha Created")

    # A supported reconnect can mint a new session incarnation and runtime IDs.
    # The transaction must resolve the created object structurally, not infer
    # ownership from index/name differences.
    tools.daw.session_incarnation_id = "reconnected-session"
    tools.daw.ids = IdentityRegistry()

    undone = run_undo_last(tools, expected_track="__none__")
    assert undone["status"] == TransactionStatus.ROLLED_BACK.value
    after = tools.get_session_snapshot()
    assert [track.name for track in after.tracks] == ["Original"]


def test_creation_rollback_fails_closed_when_rebound_identity_is_ambiguous() -> None:
    tools = _tools()
    tools.daw.create_midi_track("Original")
    run_create_c3_clip(tools, "Alpha Created")
    # Make the created transaction's structural target indistinguishable from
    # an original target only if the resolver cannot prove uniqueness.
    txn = tools.transactions.last_own()
    assert txn is not None
    txn.actions[0].target_fingerprint.clip_slots = []
    txn.actions[0].target_fingerprint.clip_names = []
    txn.actions[0].target_fingerprint.note_counts = []
    # Simulate a reconnect/readback in which the created track's content
    # fingerprint is no longer unique.  The safe response is no deletion.
    tools.daw.delete_clip(1, 0)
    tools.daw.session_incarnation_id = "reconnected-session"
    tools.daw.ids = IdentityRegistry()

    undone = run_undo_last(tools, expected_track="__none__")
    assert undone["status"] == TransactionStatus.ROLLBACK_CONFLICT.value
    assert len(tools.get_session_snapshot().tracks) == 2
