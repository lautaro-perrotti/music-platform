"""Push-only Ableton transport event stream over a dedicated localhost socket."""

from __future__ import annotations

import json
import math
import socket
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from copilot.daw.adapter import DawError
from copilot.daw.protocol import require_local_host

TRANSPORT_EVENTS_CAPABILITY = "events.transport.v1"
MAX_EVENT_FRAME_BYTES = 64 * 1024


@dataclass(frozen=True)
class TransportEventState:
    connection_state: str
    bridge_session_id: str | None = None
    sequence: int | None = None
    playing: bool | None = None
    tempo: float | None = None
    error: str | None = None


class TransportEventError(DawError):
    pass


class TransportSequenceGap(TransportEventError):
    pass


class AbletonTransportEventClient:
    """Subscribe to authoritative transport changes; never polls Live state.

    The caller supplies an already-negotiated AbletonTcpAdapter. This client
    opens a second TCP connection so unsolicited events cannot be mistaken for
    responses on the synchronous request/response command socket.
    """

    def __init__(self, daw: Any) -> None:
        self.daw = daw
        self.state = TransportEventState(connection_state="DISCONNECTED")
        self._socket: socket.socket | None = None
        self._reader: Any = None

    def connect(self, *, timeout_s: float = 5.0) -> TransportEventState:
        if TRANSPORT_EVENTS_CAPABILITY not in set(getattr(self.daw, "capabilities", ())):
            raise TransportEventError("UNSUPPORTED_CAPABILITY:events.transport.v1")
        if getattr(self.daw, "_sock", None) is None:
            raise TransportEventError("SESSION_NOT_CONNECTED")
        host = str(getattr(self.daw, "host", "127.0.0.1"))
        port = int(getattr(self.daw, "port", 9877))
        require_local_host(host)

        sock: socket.socket | None = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout_s)
            sock.settimeout(timeout_s)
            reader = sock.makefile("rb")
            request_id = "evt_" + uuid4().hex[:16]
            request = {
                "type": "subscribe_transport_events",
                "params": {},
                "request_id": request_id,
            }
            # The legacy bridge command socket parses a single JSON document,
            # not newline-delimited request frames. Event responses are JSONL,
            # but the subscription request must preserve that existing parser.
            sock.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8"))
            ack = self._read_frame(reader)
            if ack.get("request_id") != request_id or ack.get("status") != "success":
                raise TransportEventError("TRANSPORT_EVENT_SUBSCRIPTION_REJECTED")
            result = ack.get("result") or {}
            session_id = str(result.get("bridge_session_id") or "")
            ack_sequence = result.get("sequence")
            if (
                result.get("event_stream") != "transport.v1"
                or not session_id
                or isinstance(ack_sequence, bool)
                or not isinstance(ack_sequence, int)
                or ack_sequence < 0
            ):
                raise TransportEventError("TRANSPORT_EVENT_ACK_INVALID")

            self._socket = sock
            self._reader = reader
            snapshot = self._accept_snapshot(
                self._read_frame(reader),
                expected_session=session_id,
                expected_sequence=ack_sequence,
            )
            self.state = snapshot
            return self.state
        except Exception:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self._socket = None
            self._reader = None
            raise

    def read_next(self, *, timeout_s: float | None = None) -> TransportEventState:
        if self._socket is None or self._reader is None:
            raise TransportEventError("TRANSPORT_EVENT_STREAM_DISCONNECTED")
        if timeout_s is not None:
            self._socket.settimeout(timeout_s)
        try:
            event = self._read_frame(self._reader)
            return self._accept_change(event)
        except TransportSequenceGap:
            raise
        except TransportEventError as exc:
            self.state = replace(
                self.state,
                connection_state="STALE",
                error=str(exc).split(":", 1)[0],
            )
            raise
        except socket.timeout as exc:
            raise TransportEventError("TRANSPORT_EVENT_WAIT_TIMEOUT") from exc
        except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.state = replace(
                self.state,
                connection_state="DISCONNECTED",
                error=type(exc).__name__,
            )
            raise TransportEventError("TRANSPORT_EVENT_STREAM_DISCONNECTED") from exc

    def close(self) -> None:
        reader, sock = self._reader, self._socket
        self._reader = None
        self._socket = None
        if reader is not None:
            try:
                reader.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self.state = replace(self.state, connection_state="DISCONNECTED")

    @staticmethod
    def _read_frame(reader: Any) -> dict[str, Any]:
        raw = reader.readline(MAX_EVENT_FRAME_BYTES + 1)
        if not raw:
            raise EOFError("event stream closed")
        if len(raw) > MAX_EVENT_FRAME_BYTES or not raw.endswith(b"\n"):
            raise TransportEventError("TRANSPORT_EVENT_FRAME_INVALID")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TransportEventError("TRANSPORT_EVENT_FRAME_INVALID")
        return payload

    def _accept_snapshot(
        self,
        event: dict[str, Any],
        *,
        expected_session: str,
        expected_sequence: int,
    ) -> TransportEventState:
        if event.get("event_type") != "TRANSPORT_SNAPSHOT":
            raise TransportEventError("TRANSPORT_EVENT_SNAPSHOT_MISSING")
        if event.get("bridge_session_id") != expected_session:
            raise TransportEventError("BRIDGE_SESSION_MISMATCH")
        sequence = event.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise TransportEventError("TRANSPORT_EVENT_SEQUENCE_INVALID")
        if sequence != expected_sequence:
            raise TransportEventError("TRANSPORT_EVENT_SNAPSHOT_SEQUENCE_MISMATCH")
        playing, tempo = self._parse_state(event.get("state"))
        return TransportEventState(
            connection_state="CONNECTED",
            bridge_session_id=expected_session,
            sequence=sequence,
            playing=playing,
            tempo=tempo,
        )

    def _accept_change(self, event: dict[str, Any]) -> TransportEventState:
        if event.get("event_type") != "TRANSPORT_CHANGED":
            self.state = replace(self.state, connection_state="STALE", error="EVENT_TYPE_UNSUPPORTED")
            raise TransportEventError("TRANSPORT_EVENT_TYPE_UNSUPPORTED")
        if event.get("bridge_session_id") != self.state.bridge_session_id:
            self.state = replace(self.state, connection_state="STALE", error="BRIDGE_SESSION_CHANGED")
            raise TransportEventError("BRIDGE_SESSION_CHANGED")
        sequence = event.get("sequence")
        expected = int(self.state.sequence or 0) + 1
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence != expected:
            self.state = replace(self.state, connection_state="STALE", error="EVENT_SEQUENCE_GAP")
            raise TransportSequenceGap(
                "EVENT_SEQUENCE_GAP: expected {0}, received {1}".format(expected, sequence)
            )
        playing, tempo = self._parse_state(event.get("state"))
        self.state = replace(
            self.state,
            connection_state="CONNECTED",
            sequence=sequence,
            playing=playing,
            tempo=tempo,
            error=None,
        )
        return self.state

    @staticmethod
    def _parse_state(value: Any) -> tuple[bool, float]:
        if not isinstance(value, dict) or not isinstance(value.get("playing"), bool):
            raise TransportEventError("TRANSPORT_EVENT_STATE_INVALID")
        tempo = value.get("tempo")
        if isinstance(tempo, bool) or not isinstance(tempo, (int, float)):
            raise TransportEventError("TRANSPORT_EVENT_STATE_INVALID")
        parsed_tempo = float(tempo)
        if not math.isfinite(parsed_tempo) or parsed_tempo <= 0:
            raise TransportEventError("TRANSPORT_EVENT_STATE_INVALID")
        return value["playing"], parsed_tempo
