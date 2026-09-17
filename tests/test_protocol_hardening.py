from __future__ import annotations

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.daw.mock_tcp_server import MockRemoteScriptServer
from copilot.daw.protocol import ProtocolError, UnsupportedCapability, require_local_host
from copilot.daw.timeouts import TimeoutPolicy
from copilot.daw.vendor_meta import REQUIRED_COMMANDS, load_meta, script_checksum
from copilot.daw.write import WriteInDoubt


def _server() -> MockRemoteScriptServer:
    server = MockRemoteScriptServer()
    server.start()
    return server


def test_handshake_and_localhost_bind() -> None:
    require_local_host("127.0.0.1")
    try:
        require_local_host("0.0.0.0")
        raise AssertionError("wildcard must be refused")
    except DawError as exc:
        assert "LOCALHOST_TRUST_BOUNDARY" in str(exc)
    server = _server()
    adapter = AbletonTcpAdapter(server.host, server.port)
    try:
        adapter.connect()
        assert adapter.handshake_info["protocol_version"] == "1"
        assert "clip.write_notes" in adapter.capabilities
        health = adapter.health()
        assert health["status"] == "ok"
    finally:
        adapter.disconnect()
        server.stop()


def test_missing_capability_fails_before_write() -> None:
    server = _server()
    adapter = AbletonTcpAdapter(server.host, server.port)
    try:
        adapter.connect()
        adapter.capabilities.discard("track.create_midi")
        try:
            adapter.create_midi_track("X")
            raise AssertionError("missing capability must fail")
        except UnsupportedCapability:
            pass
        assert server.backend.snapshot().tracks == []
    finally:
        adapter.disconnect()
        server.stop()


def test_timeout_on_write_is_in_doubt() -> None:
    server = _server()
    adapter = AbletonTcpAdapter(
        server.host, server.port, timeouts=TimeoutPolicy(read=0.2, simple_mutation=0.2)
    )
    try:
        adapter.connect()
        server.drop_response = True
        try:
            adapter.create_midi_track("X")
            raise AssertionError("expected in doubt")
        except WriteInDoubt:
            pass
        except DawError:
            # handshake may also drop; that is a read
            pass
    finally:
        adapter.disconnect()
        server.stop()


def test_wrong_request_id_is_not_verified() -> None:
    server = _server()
    adapter = AbletonTcpAdapter(server.host, server.port)
    try:
        adapter.connect()
        server.wrong_request_id = True
        try:
            adapter.health()
            raise AssertionError("wrong id must not succeed")
        except ProtocolError:
            pass
    finally:
        adapter.disconnect()
        server.stop()


def test_invalid_and_truncated_json() -> None:
    server = _server()
    adapter = AbletonTcpAdapter(
        server.host, server.port, timeouts=TimeoutPolicy(read=0.3, simple_mutation=0.3)
    )
    try:
        adapter.connect()
        server.invalid_json = True
        try:
            adapter.health()
            raise AssertionError("invalid json must fail")
        except (DawError, ProtocolError):
            pass
        server.invalid_json = False
        server.partial_json = True
        try:
            adapter.health()
            raise AssertionError("truncated json must fail")
        except (DawError, ProtocolError):
            pass
    finally:
        adapter.disconnect()
        server.stop()


def test_hello_timeout_is_not_legacy_ready() -> None:
    server = _server()
    server.drop_response = True
    adapter = AbletonTcpAdapter(
        server.host, server.port, timeouts=TimeoutPolicy(read=0.2, connect=0.4)
    )
    try:
        try:
            adapter.connect()
            raise AssertionError("dead hello must not become LEGACY")
        except DawError as exc:
            assert "Timeout" in str(exc)
        assert adapter.handshake_info.get("mode") != "LEGACY"
    finally:
        adapter.disconnect()
        server.stop()


def test_vendor_contract_and_no_auto_update() -> None:
    meta = load_meta()
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "vendor"
        / "abletonmcp_remote_script"
        / "AbletonMCP"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    for command in REQUIRED_COMMANDS:
        assert f'"{command}"' in source or f"'{command}'" in source
    assert meta["auto_update"] is False
    assert script_checksum()
    assert "127.0.0.1" in source
