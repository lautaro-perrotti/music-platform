import pytest

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.mock_tcp_server import MockRemoteScriptServer
from copilot.daw.track_monitoring_v1 import (
    NOT_APPLICABLE,
    SUPPORTED,
    monitoring_capability,
    read_monitoring,
)


def test_return_monitoring_is_not_applicable() -> None:
    assert (
        monitoring_capability(can_be_armed=False, is_return=True, is_main=False)
        == NOT_APPLICABLE
    )
    assert (
        read_monitoring(can_be_armed=False, is_return=True, raw_state=None)
        == NOT_APPLICABLE
    )


def test_main_monitoring_is_not_applicable() -> None:
    assert (
        monitoring_capability(can_be_armed=False, is_main=True, is_return=False)
        == NOT_APPLICABLE
    )
    assert (
        read_monitoring(can_be_armed=False, is_main=True, raw_state=None)
        == NOT_APPLICABLE
    )


def test_normal_track_monitoring_is_authoritative() -> None:
    assert (
        monitoring_capability(can_be_armed=True, is_main=False, is_return=False)
        == SUPPORTED
    )
    assert (
        read_monitoring(can_be_armed=True, is_main=False, is_return=False, raw_state=0)
        == "in"
    )
    assert (
        read_monitoring(can_be_armed=True, is_main=False, is_return=False, raw_state=2)
        == "off"
    )


def test_supported_track_missing_state_is_not_swallowed() -> None:
    with pytest.raises(ValueError, match="supported track missing monitoring state"):
        read_monitoring(can_be_armed=True, is_main=False, is_return=False, raw_state=None)


def test_snapshot_keeps_main_return_and_normal_monitoring() -> None:
    server = MockRemoteScriptServer()
    host, port = server.start()
    adapter = AbletonTcpAdapter(host, port)
    try:
        server.backend.create_midi_track("Kick")
        adapter.connect()
        session = adapter.snapshot(include_notes=False)
        assert adapter.snapshot_source == "topology"
        assert adapter.last_master_info is not None
        assert adapter.last_master_info["monitoring"] == NOT_APPLICABLE
        returns = adapter.last_return_tracks or {}
        rows = returns.get("return_tracks") or []
        assert rows and rows[0]["monitoring"] == NOT_APPLICABLE
        assert session.tracks
        assert session.tracks[0].routing.monitoring == "in"
    finally:
        adapter.disconnect()
        server.stop()
