from __future__ import annotations

from pathlib import Path

import numpy as np

from copilot.audio.asset_cache import (
    REVISION_FENCE_REQUIRED,
    AssetCache,
    AssetCacheKey,
    lookup,
)
from copilot.audio.batch_capture import (
    _routing_already_correct,
    ensure_capture_host_ready,
)
from copilot.audio.capture_alignment import measure_alignment_envelope
from copilot.audio.capture_capability import (
    CERT_TAP_PROTOCOL_3,
    CaptureMode,
    CapabilityCache,
    capability_key,
)
from copilot.audio.live_capture import (
    SONG_TIME_RESTORE_TOLERANCE_BEATS,
    AudioCaptureError,
    _restore_transport,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.mock_tcp_server import MockRemoteScriptServer


def test_capability_cache_invalidates_on_key_change(tmp_path: Path) -> None:
    cache = CapabilityCache(tmp_path / "caps.json")
    key = capability_key(
        live_version="12.4.5",
        max_version="9.0",
        remote_script_protocol="1",
        tap_protocol=3,
        sample_rate=44100,
    )
    cache.bind(key)
    cache.put(CERT_TAP_PROTOCOL_3, status="YES")
    assert cache.certified(CERT_TAP_PROTOCOL_3)
    other = dict(key)
    other["tap_protocol"] = 2
    cache.bind(other)
    assert cache.certified(CERT_TAP_PROTOCOL_3) is False


def test_production_and_certification_modes_are_explicit() -> None:
    assert CaptureMode.PRODUCTION.value == "PRODUCTION"
    assert CaptureMode.CERTIFICATION.value == "CERTIFICATION"
    assert CaptureMode.PRODUCTION != CaptureMode.CERTIFICATION


def test_routing_skip_when_already_correct() -> None:
    info = {
        "input_routing_type": "LIVE22 Kick",
        "input_routing_channel": "Post Mixer",
        "output_routing_type": "Sends Only",
        "monitoring": "in",
    }
    assert _routing_already_correct(info, target_name="LIVE22 Kick") is True
    info["monitoring"] = "auto"
    assert _routing_already_correct(info, target_name="LIVE22 Kick") is False


class _RestoreDaw:
    def __init__(self, song_time: float, playing: bool) -> None:
        self.song_time = song_time
        self.playing = playing

    def stop_playback(self) -> dict:
        self.playing = False
        return {}

    def stop_clip(self, *_args: object) -> dict:
        return {}

    def set_current_song_time(self, time: float) -> dict:
        self.song_time = float(time)
        return {}

    def set_arrangement_loop(self, *_args: object) -> dict:
        return {}

    def start_playback(self) -> dict:
        self.playing = True
        return {}

    def get_playback_position(self) -> dict:
        return {
            "is_playing": self.playing,
            "current_song_time": self.song_time,
        }


def test_transport_restore_returns_pre_state() -> None:
    daw = _RestoreDaw(song_time=26.5, playing=True)
    saved = {
        "is_playing": False,
        "current_song_time": 4.0,
        "loop": False,
        "loop_start": 0.0,
        "loop_length": 0.0,
    }
    result = _restore_transport(daw, saved, None)
    assert result["ok"] is True
    assert daw.song_time == 4.0
    assert daw.playing is False
    assert result["song_time_error_beats"] <= SONG_TIME_RESTORE_TOLERANCE_BEATS


class _BadRestoreDaw(_RestoreDaw):
    def set_current_song_time(self, time: float) -> dict:
        return {}


def test_transport_restore_fail_closed_when_readback_wrong() -> None:
    daw = _BadRestoreDaw(song_time=26.5, playing=False)
    saved = {
        "is_playing": False,
        "current_song_time": 0.0,
        "loop": False,
        "loop_start": 0.0,
        "loop_length": 0.0,
    }
    try:
        _restore_transport(daw, saved, None)
        raised = False
    except AudioCaptureError as exc:
        raised = True
        assert exc.code == "STATE_RESTORE_FAILED"
    assert raised is True


def test_alignment_envelope_uses_known_clicks_not_xcorr() -> None:
    sr = 44100
    tempo = 120.0
    n = int(4.0 * sr)
    click = np.zeros(n, dtype=np.float64)
    for beat in (1.0, 2.0, 3.0):
        index = int(round(beat * 60.0 / tempo * sr))
        if 0 <= index < n:
            click[index : index + 32] = 1.0
    envelope = measure_alignment_envelope(
        {"master": click, "a": click, "b": click},
        sr=sr,
        tempo=tempo,
    )
    assert envelope["sample_accurate"] is False or envelope["max_abs_error_ms"] < 5
    assert envelope["claim"] in {"FRAME_GROUNDED", "LIMITED"}
    assert "xcorr" not in envelope["note"].lower() or "not used" in envelope["note"]


def test_asset_cache_hits_when_provenance_matches() -> None:
    assert REVISION_FENCE_REQUIRED is False
    key = AssetCacheKey(
        project_identity="p",
        audible_token="a",
        region="REGION_A",
        source="MASTER",
        view="MASTER_CONTEXT",
        signal_point="MAIN_FINAL",
        capture_protocol="3",
        sample_rate=44100,
    )
    store: dict = {}
    cache = AssetCache(store)
    cache.put(key, {"payload": "wav", "journal_status": "VERIFIED"})
    assert lookup(key, store) == "wav"
    stale = AssetCacheKey(
        project_identity="p",
        audible_token="changed",
        region="REGION_A",
        source="MASTER",
        view="MASTER_CONTEXT",
        signal_point="MAIN_FINAL",
        capture_protocol="3",
        sample_rate=44100,
    )
    assert lookup(stale, store) is None


def test_snapshot_uses_capture_topology_not_n_get_track_info() -> None:
    server = MockRemoteScriptServer()
    host, port = server.start()
    adapter = AbletonTcpAdapter(host, port)
    try:
        adapter.connect()
        adapter.reset_tcp_stats()
        adapter.snapshot(include_notes=False)
        stats = adapter.tcp_stats()
        assert stats["snapshot_source"] == "topology"
        assert stats["get_track_info"] == 0
        assert stats["get_capture_topology"] == 1
    finally:
        adapter.disconnect()
        server.stop()


class _HostDaw:
    def __init__(self) -> None:
        self.last_track_infos = {
            6: {
                "index": 6,
                "name": "Copilot Capture",
                "input_routing_type": "LIVE22 Kick",
                "input_routing_channel": "Post Mixer",
                "output_routing_type": "Sends Only",
                "monitoring": "in",
                "sends": [
                    {"send_index": 0, "level": 0.0, "min": 0.0, "max": 1.0, "name": "A"}
                ],
                "devices": [{"index": 0, "name": "Copilot Audio Tap"}],
            }
        }
        self.writes = 0

    def get_track_info(self, index: int) -> dict:
        return self.last_track_infos[index]

    def get_track_sends(self, index: int) -> list:
        return list(self.last_track_infos[index]["sends"])

    def set_tap_slot(self, *args, **kwargs):
        raise AssertionError("slot should not be rewritten")

    def set_send_level(self, *args, **kwargs):
        self.writes += 1


def test_ensure_host_ready_does_not_rewrite_correct_routing(monkeypatch) -> None:
    daw = _HostDaw()

    def _fail_set(*_a, **_k):
        raise AssertionError("set_tap_slot should be skipped")

    monkeypatch.setattr("copilot.audio.batch_capture.set_tap_slot", _fail_set)
    monkeypatch.setattr(
        "copilot.audio.batch_capture.route_host_post_mixer",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("routing rewrite")),
    )
    result = ensure_capture_host_ready(
        daw,
        name="Copilot Capture",
        target_name="LIVE22 Kick",
        slot=1,
        inventory=[
            {
                "track_index": 6,
                "device_index": 0,
                "slot": 1,
                "slot_param_index": 2,
                "rec_param_index": 1,
            }
        ],
    )
    assert result["routing_mutations"] == 0
    assert daw.writes == 0
