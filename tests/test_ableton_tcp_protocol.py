from __future__ import annotations

from copilot.agent.slices import run_create_c3_clip, run_undo_last
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.mock_tcp_server import MockRemoteScriptServer


def test_tcp_protocol_create_and_rollback() -> None:
    server = MockRemoteScriptServer()
    host, port = server.start()
    adapter = AbletonTcpAdapter(host, port)
    try:
        adapter.connect()
        health = adapter.health()
        assert health["status"] == "ok"
        tools = AgentTools(adapter, TransactionManager(adapter))
        created = run_create_c3_clip(tools)
        assert created["verification"]["note_count"] == 16
        undone = run_undo_last(tools)
        assert undone["tracks"] == []
    finally:
        adapter.disconnect()
        server.stop()
