"""LIVE_SESSION_READINESS_V1 — operational Live readiness.

Port presence is not a session.

STATUS: VERIFIED / FROZEN. Do not retune handshake, probe timeouts, or
reopen this milestone unless a reproducible bug appears.
"""

from __future__ import annotations

import socket
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from copilot.daw.ableton_tcp import AbletonTcpAdapter, DEFAULT_HOST, DEFAULT_PORT
from copilot.daw.adapter import DawError
from copilot.daw.protocol import ProtocolError
from copilot.daw.state_tokens import attach_tokens
from copilot.daw.timeouts import PROBE_TIMEOUTS, TimeoutPolicy
from copilot.schemas.session import SessionState

MILESTONE = "LIVE_SESSION_READINESS_V1"
STATUS = "VERIFIED"
FROZEN = True

SESSION_READY = "SESSION_READY"
SESSION_NOT_READY = "SESSION_NOT_READY"
LIVE_UNAVAILABLE = "LIVE_UNAVAILABLE"
ZOMBIE_PORT = "ZOMBIE_PORT"
STALE_PLAN = "STALE_PLAN"
PROJECT_REVALIDATED = "PROJECT_REVALIDATED"

PROBE_BACKOFF = (1.0, 2.0, 4.0, 8.0)


@dataclass(frozen=True)
class SessionReadyProbe:
    status: str
    port_open: bool
    handshake_ok: bool
    request_id_ok: bool
    snapshot_ok: bool
    project_identity: str
    project_path: str
    project_name: str
    reason: str
    zombie_port: bool
    writes_permitted: bool
    handshake_protocol: str = ""
    track_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["NO WRITE"] = not self.writes_permitted
        return payload


def tcp_connectable(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 0.6) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def probe_session_ready(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeouts: TimeoutPolicy | None = None,
) -> SessionReadyProbe:
    """TCP → protocol_hello/request-id → lightweight snapshot → project identity.

    Never queues writes. A listening socket that does not complete this sequence
    is ZOMBIE_PORT or SESSION_NOT_READY, not CONNECTED.
    """
    port_open = tcp_connectable(host, port)
    if not port_open:
        return _blocked(
            LIVE_UNAVAILABLE,
            port_open=False,
            reason="PORT_CLOSED",
        )

    daw = AbletonTcpAdapter(host, port, timeouts=timeouts or PROBE_TIMEOUTS)
    try:
        daw.connect()
        hello = daw.handshake_info or {}
        if hello.get("mode") == "LEGACY" or not hello.get("protocol_version"):
            return _blocked(
                SESSION_NOT_READY,
                port_open=True,
                reason="HANDSHAKE_INVALID",
                handshake_ok=False,
                request_id_ok=False,
            )
        info = daw.get_session_info()
        if not isinstance(info, dict):
            return _blocked(
                SESSION_NOT_READY,
                port_open=True,
                reason="SNAPSHOT_INVALID",
                handshake_ok=True,
                request_id_ok=True,
            )
        try:
            path_info = daw.get_session_path()
        except DawError:
            path_info = {}
        path = str((path_info or {}).get("path") or "").strip()
        name = str((path_info or {}).get("name") or info.get("name") or "").strip()
        track_count = int(info.get("track_count") or 0)
        if not path and not name:
            return _blocked(
                SESSION_NOT_READY,
                port_open=True,
                reason="PROJECT_IDENTITY_MISSING",
                handshake_ok=True,
                request_id_ok=True,
                snapshot_ok=True,
                track_count=track_count,
            )
        session = SessionState(daw="ableton", connected=True)
        attach_tokens(session, path=path or None, name=name or None)
        identity = session.project_identity or session.project_token or ""
        if not identity:
            return _blocked(
                SESSION_NOT_READY,
                port_open=True,
                reason="PROJECT_IDENTITY_MISSING",
                handshake_ok=True,
                request_id_ok=True,
                snapshot_ok=True,
                track_count=track_count,
            )
        return SessionReadyProbe(
            status=SESSION_READY,
            port_open=True,
            handshake_ok=True,
            request_id_ok=True,
            snapshot_ok=True,
            project_identity=identity,
            project_path=path,
            project_name=name,
            reason=SESSION_READY,
            zombie_port=False,
            writes_permitted=True,
            handshake_protocol=str(hello.get("protocol_version") or ""),
            track_count=track_count,
        )
    except ProtocolError as exc:
        return _blocked(
            SESSION_NOT_READY,
            port_open=True,
            reason=f"HANDSHAKE_REQUEST_ID:{exc}",
            handshake_ok=False,
            request_id_ok=False,
        )
    except DawError as exc:
        message = str(exc)
        zombie = "Timeout" in message or "socket disconnect" in message
        return _blocked(
            ZOMBIE_PORT if zombie else SESSION_NOT_READY,
            port_open=True,
            reason=message,
            zombie_port=zombie,
        )
    finally:
        try:
            daw.disconnect()
        except Exception:
            pass


def wait_for_session(
    *,
    deadline_s: float,
    probe: Callable[[], SessionReadyProbe] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    previous_identity: str = "",
) -> SessionReadyProbe:
    """Bounded backoff until SESSION_READY. No writes while disconnected."""
    probe_fn = probe or probe_session_ready
    started = clock()
    attempt = 0
    last = _blocked(LIVE_UNAVAILABLE, port_open=False, reason="NOT_PROBED")
    while clock() < started + deadline_s:
        last = probe_fn()
        if last.writes_permitted or last.status == SESSION_READY:
            if previous_identity:
                check = revalidate_project(previous_identity, last)
                if check["status"] == STALE_PLAN:
                    return SessionReadyProbe(
                        status=SESSION_READY,
                        port_open=last.port_open,
                        handshake_ok=last.handshake_ok,
                        request_id_ok=last.request_id_ok,
                        snapshot_ok=last.snapshot_ok,
                        project_identity=last.project_identity,
                        project_path=last.project_path,
                        project_name=last.project_name,
                        reason=STALE_PLAN,
                        zombie_port=False,
                        writes_permitted=False,
                        handshake_protocol=last.handshake_protocol,
                        track_count=last.track_count,
                    )
            return last
        delay = PROBE_BACKOFF[min(attempt, len(PROBE_BACKOFF) - 1)]
        remaining = (started + deadline_s) - clock()
        if remaining <= 0:
            break
        sleep(min(delay, remaining))
        attempt += 1
    if last.status == SESSION_READY:
        return last
    return SessionReadyProbe(
        status=last.status if last.status != SESSION_READY else SESSION_NOT_READY,
        port_open=last.port_open,
        handshake_ok=last.handshake_ok,
        request_id_ok=last.request_id_ok,
        snapshot_ok=last.snapshot_ok,
        project_identity=last.project_identity,
        project_path=last.project_path,
        project_name=last.project_name,
        reason=last.reason or "LIVE_SESSION_NOT_READY",
        zombie_port=last.zombie_port,
        writes_permitted=False,
        handshake_protocol=last.handshake_protocol,
        track_count=last.track_count,
    )


def revalidate_project(previous_identity: str, current: SessionReadyProbe) -> dict[str, Any]:
    """If the open set changed during a disconnect, prior plans are stale."""
    if current.status != SESSION_READY:
        return {
            "status": current.status,
            "ok": False,
            "writes_permitted": False,
            "PROJECT_REVALIDATED": False,
            "reason": current.reason,
        }
    if previous_identity and previous_identity != current.project_identity:
        return {
            "status": STALE_PLAN,
            "ok": False,
            "writes_permitted": False,
            "PROJECT_REVALIDATED": False,
            "reason": "PROJECT_CHANGED",
            "previous_identity": previous_identity,
            "current_identity": current.project_identity,
        }
    return {
        "status": PROJECT_REVALIDATED,
        "ok": True,
        "writes_permitted": True,
        "PROJECT_REVALIDATED": True,
        "project_identity": current.project_identity,
        "project_path": current.project_path,
        "project_name": current.project_name,
    }


def _blocked(
    status: str,
    *,
    port_open: bool,
    reason: str,
    handshake_ok: bool = False,
    request_id_ok: bool = False,
    snapshot_ok: bool = False,
    zombie_port: bool | None = None,
    track_count: int = 0,
) -> SessionReadyProbe:
    return SessionReadyProbe(
        status=status,
        port_open=port_open,
        handshake_ok=handshake_ok,
        request_id_ok=request_id_ok,
        snapshot_ok=snapshot_ok,
        project_identity="",
        project_path="",
        project_name="",
        reason=reason,
        zombie_port=bool(zombie_port if zombie_port is not None else status == ZOMBIE_PORT),
        writes_permitted=False,
        track_count=track_count,
    )
