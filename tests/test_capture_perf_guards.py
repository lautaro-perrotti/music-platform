from __future__ import annotations

from pathlib import Path

import numpy as np
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


def test_runtime_protocol_expectation_matches_udp_removed() -> None:
    assert EXPECTED_TAP_PROTOCOL == UDP_REMOVED_PROTOCOL == 3
    assert SONG_TIME_RESTORE_TOLERANCE_BEATS == 0.08
    assert CaptureMode.PRODUCTION.value == "PRODUCTION"
