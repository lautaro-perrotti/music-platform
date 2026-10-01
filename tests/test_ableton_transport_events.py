from __future__ import annotations

import json
import socketserver
import threading

import pytest

from copilot.daw.transport_events import (
    AbletonTransportEventClient,
    TransportEventError,
    TransportSequenceGap,
)


SESSION_ID = "bridge_test_session"


class _BridgeStub:
    host = "127.0.0.1"
    _sock = object()
    capabilities = {"events.transport.v1"}

    def __init__(self, port: int) -> None:
        self.port = port


class _EventServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _serve_frames(frames: list[dict]):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            # The existing Remote Script request parser consumes one bare JSON
            # document. Only server-to-client event frames are newline-delimited.
            request = json.loads(self.request.recv(8192).decode("utf-8"))
            ack = {
                "status": "success",
                "request_id": request["request_id"],
                "result": {
                    "event_stream": "transport.v1",
                    "bridge_session_id": SESSION_ID,
                    "sequence": frames[0]["sequence"],
                },
            }
            self.wfile.write(json.dumps(ack).encode("utf-8") + b"\n")
            for frame in frames:
                self.wfile.write(json.dumps(frame).encode("utf-8") + b"\n")
                self.wfile.flush()

    server = _EventServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _snapshot(sequence: int = 0) -> dict:
    return {
        "event_type": "TRANSPORT_SNAPSHOT",
        "bridge_session_id": SESSION_ID,
        "sequence": sequence,
        "state": {"playing": False, "tempo": 120.0},
    }


def _change(sequence: int, *, playing: bool, tempo: float = 120.0) -> dict:
    return {
        "event_type": "TRANSPORT_CHANGED",
        "bridge_session_id": SESSION_ID,
        "sequence": sequence,
        "state": {"playing": playing, "tempo": tempo},
    }


def test_transport_client_applies_play_stop_and_tempo_events_without_polling() -> None:
    server, thread = _serve_frames([
        _snapshot(),
        _change(1, playing=True),
        _change(2, playing=False),
        _change(3, playing=False, tempo=124.0),
    ])
    client = AbletonTransportEventClient(_BridgeStub(server.server_address[1]))
    try:
        initial = client.connect()
        assert (initial.sequence, initial.playing, initial.tempo) == (0, False, 120.0)

        play = client.read_next()
        assert (play.sequence, play.playing, play.connection_state) == (1, True, "CONNECTED")

        stop = client.read_next()
        assert (stop.sequence, stop.playing) == (2, False)

        tempo = client.read_next()
        assert (tempo.sequence, tempo.tempo) == (3, 124.0)
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_transport_client_fails_closed_on_sequence_gap() -> None:
    server, thread = _serve_frames([_snapshot(), _change(2, playing=True)])
    client = AbletonTransportEventClient(_BridgeStub(server.server_address[1]))
    try:
        client.connect()
        with pytest.raises(TransportSequenceGap, match="expected 1, received 2"):
            client.read_next()
        assert client.state.connection_state == "STALE"
        assert client.state.playing is False
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_transport_client_requires_negotiated_capability() -> None:
    bridge = _BridgeStub(9877)
    bridge.capabilities = {"session.transport"}
    client = AbletonTransportEventClient(bridge)
    with pytest.raises(TransportEventError, match="UNSUPPORTED_CAPABILITY"):
        client.connect()
