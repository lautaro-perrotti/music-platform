from __future__ import annotations

from pathlib import Path

from copilot.agent.journal import DurableJournal
from copilot.agent.slices import run_create_c3_clip, run_undo_last
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.mock_tcp_server import MockRemoteScriptServer


def test_stress_mock_and_journal(tmp_path: Path) -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    journal = DurableJournal(tmp_path / "journal.jsonl")
    tools = AgentTools(daw, TransactionManager(daw, journal=journal))
    for _ in range(200):
        daw.snapshot()
    for index in range(20):
        run_create_c3_clip(tools, f"T{index}")
        run_undo_last(tools, expected_track=f"T{index}")
    assert tools.get_session_snapshot().tracks == []
    assert journal.read_all()
    assert not tools.lock.locked()


def test_connect_disconnect_cycles() -> None:
    server = MockRemoteScriptServer()
    host, port = server.start()
    try:
        for _ in range(10):
            adapter = AbletonTcpAdapter(host, port)
            adapter.connect()
            adapter.health()
            adapter.disconnect()
            assert adapter._sock is None
    finally:
        server.stop()
