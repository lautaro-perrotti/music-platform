from __future__ import annotations

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError


def test_real_ableton_is_blocked_without_live() -> None:
    adapter = AbletonTcpAdapter("127.0.0.1", 9877)
    try:
        adapter.connect()
    except DawError as exc:
        assert "BLOCKED_BY_ENVIRONMENT" in str(exc)
        return
    adapter.disconnect()
    # If Ableton is actually running, the probe is not blocked.
    health = adapter.health()
    assert health.get("status") == "ok"
