from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from copilot.audio.capture_capability import CaptureMode
from copilot.audio.live_capture import (
    EXPECTED_TAP_PROTOCOL,
    SONG_TIME_RESTORE_TOLERANCE_BEATS,
    _restore_mixer,
    _wav_poll_status,
    mixer_from_track_infos,
    outputs_from_track_infos,
)
from copilot.audio.tap_trust import UDP_REMOVED_PROTOCOL
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError


class _DummyDaw:
    def __init__(self) -> None:
        self.reads = 0

    def get_track_info(self, index: int) -> dict:
        self.reads += 1
        return {"index": index, "mute": False, "solo": False, "arm": False}


def test_mixer_restore_skipped_when_mutation_plan_untouched() -> None:
    daw = _DummyDaw()
    before = [{"index": 0, "mute": False, "solo": False, "arm": False}]
    errors = _restore_mixer(daw, before, touched=False)
    assert errors == []
    assert daw.reads == 0


def test_mixer_restore_runs_when_mutation_plan_touched() -> None:
    daw = _DummyDaw()
    before = [{"index": 0, "mute": False, "solo": False, "arm": False}]
    errors = _restore_mixer(daw, before, touched=True)
    assert errors == []
    assert daw.reads >= 1


def test_wav_duration_alone_is_not_ready(tmp_path: Path) -> None:
    path = tmp_path / "partial.wav"
    sr = 44100
    sf.write(str(path), np.zeros((sr, 2), dtype=np.float32), sr)
    status = _wav_poll_status(path, min_duration_s=2.0, expected_sr=sr)
    assert status["candidate"] is False
    assert status["reason"] == "short"


def test_wav_header_and_duration_can_be_candidate(tmp_path: Path) -> None:
    path = tmp_path / "ok.wav"
    sr = 44100
    sf.write(str(path), np.zeros((sr * 2, 2), dtype=np.float32), sr)
    status = _wav_poll_status(path, min_duration_s=2.0, expected_sr=sr)
    assert status["candidate"] is True
    assert int(status["frames"]) == sr * 2


def test_outputs_come_from_one_track_info_read() -> None:
    infos = {
        0: {
            "name": "Kick",
            "mute": False,
            "solo": False,
            "arm": False,
            "output_routing_type": "Main",
            "output_routing_channel": "",
        },
        1: {
            "name": "Bus",
            "mute": False,
            "solo": False,
            "arm": False,
            "output_routing_type": "Sends Only",
            "output_routing_channel": "",
        },
    }
    mixer = mixer_from_track_infos(infos)
    outputs = outputs_from_track_infos(infos)
    assert mixer[0]["name"] == "Kick"
    assert outputs[1]["type"] == "Sends Only"


def test_tcp_stats_count_commands() -> None:
    adapter = AbletonTcpAdapter()
    adapter.reset_tcp_stats()
    adapter.tcp_counts["get_track_info"] += 3
    adapter.snapshot_calls = 1
    stats = adapter.tcp_stats()
    assert stats["total"] == 3
    assert stats["get_track_info"] == 3
    assert stats["full_session_snapshots"] == 1


def test_tcp_stats_measure_bounded_bridge_round_trip_latency(monkeypatch) -> None:
    import json

    import copilot.daw.ableton_tcp as tcp_module

    class SocketStub:
        def __init__(self) -> None:
            self.request = None

        def sendall(self, payload: bytes) -> None:
            self.request = json.loads(payload)

        def settimeout(self, _timeout: float) -> None:
            return None

    adapter = AbletonTcpAdapter()
    adapter.capabilities = {"health"}
    adapter._sock = SocketStub()
    adapter._recv_json = lambda: {
        "request_id": adapter._sock.request["request_id"],
        "result": {"status": "ok"},
    }
    clock = iter([10.0, 10.0125])
    monkeypatch.setattr(tcp_module.time, "perf_counter", lambda: next(clock))

    assert adapter._command("health_check") == {"status": "ok"}
    stats = adapter.tcp_stats()["rpc_round_trip_ms"]
    assert stats["scope"] == "CORE_BRIDGE_REQUEST_RESPONSE"
    assert stats["all"] == {"count": 1, "p50": 12.5, "p95": 12.5, "max": 12.5}
    assert stats["by_command"]["health_check"]["count"] == 1
    assert stats["outcomes"] == {"RESPONSE": 1}


def test_tcp_stats_record_timed_out_read_without_claiming_success(monkeypatch) -> None:
    import copilot.daw.ableton_tcp as tcp_module

    class SocketStub:
        def sendall(self, _payload: bytes) -> None:
            return None

        def settimeout(self, _timeout: float) -> None:
            return None

    adapter = AbletonTcpAdapter()
    adapter.capabilities = {"health"}
    adapter._sock = SocketStub()
    adapter._recv_json = lambda: (_ for _ in ()).throw(TimeoutError())
    clock = iter([20.0, 20.025])
    monkeypatch.setattr(tcp_module.time, "perf_counter", lambda: next(clock))

    with pytest.raises(DawError, match="Timeout waiting for Ableton"):
        adapter._command("health_check")

    stats = adapter.tcp_stats()["rpc_round_trip_ms"]
    assert stats["all"] == {"count": 1, "p50": 25.0, "p95": 25.0, "max": 25.0}
    assert stats["outcomes"] == {"ERROR": 1}


def test_runtime_protocol_expectation_matches_udp_removed() -> None:
    assert EXPECTED_TAP_PROTOCOL == UDP_REMOVED_PROTOCOL == 3
    assert SONG_TIME_RESTORE_TOLERANCE_BEATS == 0.08
    assert CaptureMode.PRODUCTION.value == "PRODUCTION"
