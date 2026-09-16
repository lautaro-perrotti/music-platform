from __future__ import annotations

import threading

from copilot.agent.slices import run_create_c3_clip
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.mock import MockAbletonAdapter
from copilot.schemas.transaction import TransactionStatus


def test_fault_after_each_slice_step_rolls_back() -> None:
    for fail_after in (1, 2, 3):
        daw = MockAbletonAdapter()
        daw.connect()
        tools = AgentTools(daw, TransactionManager(daw))
        daw.fail_after_writes = fail_after  # fail the next write after N successes
        try:
            run_create_c3_clip(tools)
            raise AssertionError(f"step {fail_after} should fail")
        except Exception:
            pass
        txn = tools.transactions.history[-1]
        assert txn.status in {
            TransactionStatus.ROLLED_BACK,
            TransactionStatus.ROLLBACK_CONFLICT,
            TransactionStatus.FAILED,
        }
        assert txn.status != TransactionStatus.VERIFIED
        if txn.status == TransactionStatus.ROLLED_BACK:
            assert tools.get_session_snapshot().tracks == []


def test_serialized_writes_see_fresh_revision() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    tools = AgentTools(daw, TransactionManager(daw))
    shared_rev = tools.get_session_snapshot().revision
    outcomes: list[str] = []

    def writer(name: str) -> None:
        with tools.lock.write():
            try:
                before = tools.get_session_snapshot()
                tools.transactions.begin(name, before)
                tools.create_midi_track(name, expected_revision=shared_rev)
                tools.transactions.commit(
                    {"ok": True}, session=tools.get_session_snapshot()
                )
                outcomes.append("ok")
            except Exception as exc:  # noqa: BLE001
                if tools.transactions._open is not None:
                    tools.transactions.abort(str(exc))
                outcomes.append(str(exc))

    first = threading.Thread(target=writer, args=("One",))
    second = threading.Thread(target=writer, args=("Two",))
    first.start()
    second.start()
    first.join()
    second.join()
    names = [track.name for track in tools.get_session_snapshot().tracks]
    assert len(names) == 1
    assert any("Stale revision" in item for item in outcomes)
