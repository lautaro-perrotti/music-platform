from __future__ import annotations

import pytest

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.daw.detect import detect_ableton


def test_real_ableton_is_blocked_without_live() -> None:
    detection = detect_ableton()
    if detection.found and detection.port_open:
        pytest.skip("Live is active; this test requires the environment-down probe")
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
