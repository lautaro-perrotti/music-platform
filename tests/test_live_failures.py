from __future__ import annotations

from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.daw.mock import MockAbletonAdapter
from copilot.schemas.transaction import TransactionStatus


def test_missing_port_is_blocked() -> None:
    adapter = AbletonTcpAdapter("127.0.0.1", 59999)
    try:
        adapter.connect()
    except DawError as exc:
        assert "BLOCKED_BY_ENVIRONMENT" in str(exc)
        return
    raise AssertionError("missing port must not connect")


def test_missing_track_and_clip() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    try:
        daw.get_clip_notes(0, 0)
        raise AssertionError("expected missing track")
    except DawError as exc:
        assert "out of range" in str(exc)
    daw.create_midi_track("X")
    try:
        daw.get_clip_notes(0, 0)
        raise AssertionError("expected missing clip")
    except DawError as exc:
        assert "No clip" in str(exc)


def test_stale_revision_fails_and_is_not_verified() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    tools = AgentTools(daw, TransactionManager(daw))
    before = tools.get_session_snapshot()
    tools.transactions.begin("stale", before)
    daw.create_midi_track("Other")
    try:
        tools.create_midi_track("AI Test", expected_revision=before.revision)
        raise AssertionError("expected stale revision")
    except DawError as exc:
        assert "Stale revision" in str(exc)
        txn = tools.transactions.abort(str(exc))
    assert txn.status in {TransactionStatus.FAILED, TransactionStatus.ROLLED_BACK}
    assert txn.status != TransactionStatus.VERIFIED


def test_out_of_range_volume() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    tools = AgentTools(daw, TransactionManager(daw))
    tools.transactions.begin("vol", tools.get_session_snapshot())
    daw.create_midi_track("X")
    try:
        tools.set_mixer_volume(0, 1.5)
        raise AssertionError("expected range error")
    except DawError as exc:
        assert "out of range" in str(exc)


def test_partial_apply_aborts_instead_of_verified() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    tools = AgentTools(daw, TransactionManager(daw))
    before = tools.get_session_snapshot()
    tools.transactions.begin("partial", before)
    tools.create_midi_track("AI Test")
    try:
        tools.create_midi_clip(0, 99, 16.0)
        raise AssertionError("expected clip index failure")
    except DawError as exc:
        txn = tools.transactions.abort(str(exc))
    assert txn.status == TransactionStatus.ROLLED_BACK
    assert daw.snapshot().track_by_name("AI Test") is None
