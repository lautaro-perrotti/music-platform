from __future__ import annotations

from pathlib import Path

from copilot.audio.doctor_v1 import doctor
from copilot.daw.mock_tcp_server import MockRemoteScriptServer
from copilot.daw.session_ready_v1 import (
    FROZEN,
    LIVE_UNAVAILABLE,
    MILESTONE,
    PROJECT_REVALIDATED,
    SESSION_NOT_READY,
    SESSION_READY,
    STATUS,
    STALE_PLAN,
    ZOMBIE_PORT,
    SessionReadyProbe,
    probe_session_ready,
    revalidate_project,
    wait_for_session,
)
from copilot.daw.timeouts import TimeoutPolicy

FAST = TimeoutPolicy(connect=0.4, read=0.4, simple_mutation=0.4, large_operation=0.4)


def _ready(**overrides) -> SessionReadyProbe:
    payload = dict(
        status=SESSION_READY,
        port_open=True,
        handshake_ok=True,
        request_id_ok=True,
        snapshot_ok=True,
        project_identity="id-a",
        project_path=r"C:\songs\a.als",
        project_name="a",
        reason=SESSION_READY,
        zombie_port=False,
        writes_permitted=True,
        handshake_protocol="1",
        track_count=2,
    )
    payload.update(overrides)
    return SessionReadyProbe(**payload)


def test_zombie_port_is_not_session_ready() -> None:
    server = MockRemoteScriptServer()
    server.drop_response = True
    server.start()
    try:
        probe = probe_session_ready(server.host, server.port, timeouts=FAST)
        assert probe.port_open is True
        assert probe.status == ZOMBIE_PORT
        assert probe.zombie_port is True
        assert probe.writes_permitted is False
        assert probe.handshake_ok is False
        assert probe.to_dict()["NO WRITE"] is True
    finally:
        server.stop()


def test_false_ready_prevented_when_request_id_wrong() -> None:
    server = MockRemoteScriptServer()
    server.wrong_request_id = True
    server.start()
    try:
        probe = probe_session_ready(server.host, server.port, timeouts=FAST)
        assert probe.port_open is True
        assert probe.status == SESSION_NOT_READY
        assert probe.writes_permitted is False
        assert probe.request_id_ok is False
        assert probe.status != SESSION_READY
    finally:
        server.stop()


def test_real_session_handshake_and_identity() -> None:
    server = MockRemoteScriptServer()
    server.backend.session_path = r"C:\songs\fixture.als"
    server.backend.session_name = "fixture"
    server.start()
    try:
        probe = probe_session_ready(server.host, server.port, timeouts=FAST)
        assert probe.status == SESSION_READY
        assert probe.handshake_ok is True
        assert probe.request_id_ok is True
        assert probe.snapshot_ok is True
        assert probe.project_path.endswith("fixture.als")
        assert probe.project_identity
        assert probe.writes_permitted is True
        assert probe.zombie_port is False
    finally:
        server.stop()


def test_bounded_retry_then_session_ready() -> None:
    zombie = _ready(
        status=ZOMBIE_PORT,
        writes_permitted=False,
        zombie_port=True,
        handshake_ok=False,
        request_id_ok=False,
        snapshot_ok=False,
        project_identity="",
        reason="Timeout waiting for Ableton",
    )
    good = _ready()
    calls = {"n": 0}
    sleeps: list[float] = []

    def probe() -> SessionReadyProbe:
        calls["n"] += 1
        return zombie if calls["n"] < 3 else good

    out = wait_for_session(
        deadline_s=30,
        probe=probe,
        sleep=sleeps.append,
        clock=lambda: 0.0 if calls["n"] < 4 else 0.0,
    )
    assert out.status == SESSION_READY
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]
    assert max(sleeps) <= 8.0
    assert out.writes_permitted is True


def test_project_changed_is_stale_and_must_revalidate() -> None:
    previous = _ready()
    current = _ready(
        project_identity="id-b",
        project_path=r"C:\songs\b.als",
        project_name="b",
    )
    check = revalidate_project(previous.project_identity, current)
    assert check["status"] == STALE_PLAN
    assert check["ok"] is False
    assert check["writes_permitted"] is False
    assert check["PROJECT_REVALIDATED"] is False
    same = revalidate_project(current.project_identity, current)
    assert same["status"] == PROJECT_REVALIDATED
    assert same["writes_permitted"] is True


def test_disconnected_probe_never_permits_writes() -> None:
    probe = probe_session_ready("127.0.0.1", 1, timeouts=FAST)
    assert probe.status == LIVE_UNAVAILABLE
    assert probe.writes_permitted is False
    assert probe.to_dict()["NO WRITE"] is True


def test_doctor_zombie_port_is_not_ready(tmp_path: Path) -> None:
    probe = SessionReadyProbe(
        status=ZOMBIE_PORT,
        port_open=True,
        handshake_ok=False,
        request_id_ok=False,
        snapshot_ok=False,
        project_identity="",
        project_path="",
        project_name="",
        reason="Timeout waiting for Ableton",
        zombie_port=True,
        writes_permitted=False,
    )
    report = doctor(evidence=tmp_path, daw=None, session_probe=probe)
    assert report["status"] == "BLOCKED"
    assert "zombie_port" in report["failures"]
    assert "session_not_ready" in report["failures"]
    assert report["checks"]["ableton_reachable"] is False
    assert report["checks"]["writes_permitted"] is False
    assert report["NO WRITE"] is True


def test_live_session_readiness_is_frozen() -> None:
    assert MILESTONE == "LIVE_SESSION_READINESS_V1"
    assert STATUS == "VERIFIED"
    assert FROZEN is True
