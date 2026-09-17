from __future__ import annotations

from pathlib import Path

import pytest

from copilot.audio.live_capture import AudioCaptureError
from copilot.audio.tap_trust import (
    FAILED,
    IN_DOUBT,
    PREPARED,
    RECORDING,
    VERIFIED,
    CaptureJournal,
    assert_unique_slots,
    duplicate_slots,
    incomplete_assets_are_invalid,
    interpret_send_level,
    reserve_capture_dests,
    routing_claim,
    slot_ownership_map,
    tap_instance_id,
    udp_control_audit,
)


def _tap(track: int, device: int, slot: int, name: str) -> dict:
    return {
        "tap_instance_id": tap_instance_id(track, device),
        "track_index": track,
        "track_name": name,
        "device_index": device,
        "device_on": 1.0,
        "rec": 0.0,
        "slot": slot,
        "tap_protocol": 2,
    }


def test_duplicate_slot_fail_closed() -> None:
    inventory = [
        _tap(-1, 0, 0, "MASTER"),
        _tap(6, 0, 1, "Copilot Capture"),
        _tap(5, 0, 2, "LIVE21 Silent"),
        _tap(15, 0, 2, "Copilot Capture Bass"),
    ]
    found = duplicate_slots(inventory)
    assert len(found) == 1
    assert found[0]["slot"] == 2
    assert {item["track_name"] for item in found[0]["holders"]} == {
        "LIVE21 Silent",
        "Copilot Capture Bass",
    }
    with pytest.raises(AudioCaptureError, match="TAP_SLOT_COLLISION"):
        assert_unique_slots(inventory)


def test_unique_slots_pass() -> None:
    inventory = [
        _tap(-1, 0, 0, "MASTER"),
        _tap(6, 0, 1, "Copilot Capture"),
        _tap(15, 0, 2, "Copilot Capture Bass"),
    ]
    assert slot_ownership_map(inventory)[0][0]["track_name"] == "MASTER"
    assert_unique_slots(inventory)


def test_two_recorders_same_destination_fail_closed(tmp_path: Path) -> None:
    recorders = [
        {"key": "a", "staging": "_next.wav", "slot": 0},
        {"key": "b", "staging": "_next.wav", "slot": 0},
    ]
    with pytest.raises(AudioCaptureError, match="CAPTURE_PATH_COLLISION"):
        reserve_capture_dests(recorders, pass_id="abc", root=tmp_path)


def test_existing_dest_is_not_overwritten(tmp_path: Path) -> None:
    dest = tmp_path / "capture_abc_slot0.wav"
    dest.write_bytes(b"old")
    recorders = [{"key": "master", "staging": "_next.wav", "slot": 0}]
    with pytest.raises(AudioCaptureError, match="CAPTURE_PATH_COLLISION"):
        reserve_capture_dests(recorders, pass_id="abc", root=tmp_path)
    assert dest.read_bytes() == b"old"


def test_send_zero_is_silence_only_at_mixer_minimum() -> None:
    silent = interpret_send_level({"level": 0.0, "min": 0.0, "max": 1.0, "name": "A"})
    assert silent["semantic_silence"] is True
    assert silent["zero_is_not_0dB"] is True
    unityish = interpret_send_level({"level": 0.85, "min": 0.0, "max": 1.0, "name": "A"})
    assert unityish["semantic_silence"] is False
    unknown = interpret_send_level({"level": 0.0})
    assert unknown["semantic_silence"] is False


def test_off_mix_graph_requires_silent_sends() -> None:
    sends = [{"level": 0.0, "min": 0.0, "max": 1.0, "name": "A-Reverb"}]
    claim = routing_claim(
        input_type="LIVE22 Kick",
        input_channel="Post Mixer",
        output="Sends Only",
        monitoring="in",
        through_main=False,
        sends=sends,
        target_name="LIVE22 Kick",
    )
    assert claim["claim"] == "OFF_MIX_GRAPH"
    loud = routing_claim(
        input_type="LIVE22 Kick",
        input_channel="Post Mixer",
        output="Sends Only",
        monitoring="in",
        through_main=False,
        sends=[{"level": 0.8, "min": 0.0, "max": 1.0, "name": "A-Reverb"}],
        target_name="LIVE22 Kick",
    )
    assert loud["claim"] == "OFF_DIRECT_MAIN"


def test_journal_incomplete_is_not_verified(tmp_path: Path) -> None:
    journal = CaptureJournal("deadbeef", directory=tmp_path)
    journal.record(PREPARED, region="REGION_A")
    journal.record(RECORDING)
    assert incomplete_assets_are_invalid(journal.last_status() or "")
    assert journal.claim_verified() == IN_DOUBT
    journal.record(FAILED, error="crash")
    assert journal.claim_verified() == FAILED
    ok = CaptureJournal("okpass", directory=tmp_path)
    ok.record(PREPARED)
    ok.record(VERIFIED)
    assert ok.claim_verified() == VERIFIED


def test_udp_audit_names_global_port() -> None:
    audit = udp_control_audit()
    assert audit["udp_port"] == 19877
    assert "global" in audit["udp_scope"]


def test_idle_locked_staging_is_not_stale_armed(monkeypatch, tmp_path: Path) -> None:
    from copilot.audio.tap_trust import reconcile_stale_taps

    locked = tmp_path / "_next_kick.wav"
    locked.write_bytes(b"")

    monkeypatch.setattr("copilot.audio.tap_trust.set_tap_recording", lambda *a, **k: None)
    monkeypatch.setattr("copilot.audio.tap_trust.set_tap_enabled", lambda *a, **k: None)
    monkeypatch.setattr("copilot.audio.tap_trust.staging_path", lambda name: locked)
    monkeypatch.setattr(
        "copilot.audio.tap_trust._exclusive_open_ok",
        lambda p: {"exists": True, "exclusive": False, "error": "winerror=32", "size": 0},
    )
    monkeypatch.setattr(
        "copilot.audio.tap_trust.wav_shared_read_ok",
        lambda p: {"exists": True, "readable": True, "error": None, "size": 0},
    )
    monkeypatch.setattr(
        "copilot.audio.tap_trust.wav_lock_owners",
        lambda p: [{"pid": 14228, "app": "Ableton Live 12 Trial"}],
    )

    class Daw:
        def get_device_parameters(self, *a, **k):
            return {
                "parameters": [
                    {"name": "Rec", "value": 0.0, "index": 1},
                    {"name": "Device On", "value": 0.0, "index": 0},
                ]
            }

    inventory = [_tap(29, 0, 1, "Copilot Capture")]
    out = reconcile_stale_taps(Daw(), inventory, recorder_ids={"tap:-1:0", "tap:30:0"})
    assert out["ok"] is True
    assert out["failed"] == []
    assert out["excluded"][0]["idle_handle_held"] is True
    assert out["excluded"][0]["rec_after"] == 0.0


def test_armed_rec_still_stale_armed(monkeypatch, tmp_path: Path) -> None:
    from copilot.audio.tap_trust import reconcile_stale_taps

    locked = tmp_path / "_next_kick.wav"
    locked.write_bytes(b"")
    monkeypatch.setattr("copilot.audio.tap_trust.set_tap_recording", lambda *a, **k: None)
    monkeypatch.setattr("copilot.audio.tap_trust.set_tap_enabled", lambda *a, **k: None)
    monkeypatch.setattr("copilot.audio.tap_trust.staging_path", lambda name: locked)
    monkeypatch.setattr(
        "copilot.audio.tap_trust._exclusive_open_ok",
        lambda p: {"exists": True, "exclusive": False, "error": "winerror=32", "size": 0},
    )
    monkeypatch.setattr(
        "copilot.audio.tap_trust.wav_shared_read_ok",
        lambda p: {"exists": True, "readable": True, "error": None, "size": 0},
    )
    monkeypatch.setattr("copilot.audio.tap_trust.wav_lock_owners", lambda p: [])

    class Daw:
        def get_device_parameters(self, *a, **k):
            return {
                "parameters": [
                    {"name": "Rec", "value": 1.0, "index": 1},
                    {"name": "Device On", "value": 1.0, "index": 0},
                ]
            }

    with pytest.raises(AudioCaptureError, match="TAP_STALE_ARMED"):
        reconcile_stale_taps(Daw(), [_tap(29, 0, 1, "Copilot Capture")], recorder_ids=set())
