from __future__ import annotations

from copilot.agent.slices import run_create_c3_clip
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.write import WriteInDoubt
from copilot.schemas.transaction import TransactionStatus


def test_lost_ack_reconciles_instead_of_retrying_blindly() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    tools = AgentTools(daw, TransactionManager(daw))
    before = tools.get_session_snapshot()
    tools.transactions.begin("lost-ack", before)
    daw.lose_ack = True
    created = tools.create_midi_track("AI Test")
    assert created.get("reconciled") is True
    assert created["index"] == 0
    assert len(daw.snapshot().tracks) == 1


def test_ambiguous_timeout_stays_in_doubt() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    tools = AgentTools(daw, TransactionManager(daw))
    before = tools.get_session_snapshot()
    tools.transactions.begin("doubt", before)
    tools.create_midi_track("One")
    daw.lose_ack = True
    try:
        tools.create_midi_clip(0, 0, 4.0)
        raise AssertionError("expected in doubt")
    except WriteInDoubt:
        tools.transactions.mark_in_doubt("lost ack")
    assert tools.transactions.history[-1].status == TransactionStatus.IN_DOUBT
    assert tools.transactions.history[-1].status != TransactionStatus.FAILED
    assert tools.transactions.history[-1].status != TransactionStatus.VERIFIED


def test_slice_in_doubt_does_not_claim_verified() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    tools = AgentTools(daw, TransactionManager(daw))
    daw.lose_ack = True
    try:
        run_create_c3_clip(tools)
        # first create may reconcile; later clip write should stay in doubt
    except WriteInDoubt:
        pass
    if tools.transactions.history:
        assert tools.transactions.history[-1].status != TransactionStatus.VERIFIED
