from pathlib import Path
from types import SimpleNamespace

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.integration.autonomous_producer_alpha_v1 import _capture_sections
from copilot.producer.goal import ProducerGoal
from copilot.producer.quality_gate import (
    evaluate_delivery, section_window_specs, verify_arrangement_timeline,
)
from copilot.producer.track_spec import TrackSpec


def test_delivery_refuses_unverified_audio_and_save(tmp_path):
    goal = ProducerGoal.from_prompt("BPM: 120\nDuración: 4 compases")
    spec = TrackSpec(bpm=120, primary_hook="kick rhythm", hook_role="Kick", sections=[
        {"name": "Intro", "bars": 4, "energy": .3, "active_roles": ["Kick"]}
    ])
    base = dict(
        goal=goal, spec=spec, project_path=tmp_path / "track.als",
        project_identity="copy-1", reopened_identity=None, tempo_bpm=120,
        arrangement=[{"section": "Intro", "track": "Kick", "status": "VERIFIED"}],
        arrangement_geometry=[],
        captures={}, actions=[{"status": "VERIFIED"}],
        critique_verdict="finalize", transport_stopped=True,
    )
    result = evaluate_delivery(**base)
    assert result["status"] == "DRAFT"
    assert "SAVED_ALS_NOT_VERIFIED" in result["reasons"]
    assert "SECTION_AUDIO_NOT_VERIFIED:Intro" in result["reasons"]
    assert result["verified_sections"] == 0

    Path(base["project_path"]).write_bytes(b"als")
    audio = tmp_path / "intro.wav"
    audio.write_bytes(b"wav")
    base["reopened_identity"] = "copy-1"
    windows = {
        position: {
            "capture_id": position, "path": str(audio), "rms": .1, "peak": .9,
            "start_qn": start, "end_qn": end, "project_identity": "copy-1",
        }
        for position, (start, end) in section_window_specs(spec)["Intro"].items()
    }
    base["captures"] = {"Intro": {
        "capture_id": "capture-1", "path": str(audio), "rms": .1, "peak": .9,
        "windows": windows,
    }}
    base["role_captures"] = {
        role: {"ok": True, "signal_status": "HAS_SIGNAL", "restore": {"ok": True},
               "audio_sha256": "sha", "wav_path": str(audio)}
        for role in ("Kick", "Bass")
    }
    assert evaluate_delivery(**base)["status"] == "COMPLETE"
    base["actions"] = [
        {"status": "VERIFIED"},
        {"status": "EXECUTION_DEFERRED", "reason": "PHRASE_MIDI_VARIATION_NOT_CERTIFIED"},
    ]
    assert evaluate_delivery(**base)["status"] == "DRAFT"
    assert "ESSENTIAL_ACTION_UNVERIFIED" in evaluate_delivery(**base)["reasons"]
    base["actions"] = []
    assert "NO_VERIFIED_PRODUCTION_ACTIONS" in evaluate_delivery(**base)["reasons"]
    base["actions"] = [{"status": "VERIFIED"}]
    base["captures"]["Intro"]["windows"]["ending"]["peak"] = 0.0
    assert "SECTION_WINDOW_NOT_VERIFIED:Intro:ending" in evaluate_delivery(**base)["reasons"]
    base["captures"]["Intro"]["windows"]["ending"]["peak"] = .9
    base["actions"] = [{"status": "EXECUTION_DEFERRED"}]
    assert "ESSENTIAL_ACTION_UNVERIFIED" in evaluate_delivery(**base)["reasons"]
    base["actions"] = [{"status": "VERIFIED"}]
    base["captures"]["Intro"]["peak"] = 1.0
    assert "SECTION_SILENT_OR_CLIPPING:Intro" in evaluate_delivery(**base)["reasons"]


def test_arrangement_geometry_detects_missing_role_and_wrong_duration():
    spec = TrackSpec(
        bpm=127, primary_hook="bass riff", hook_role="Bass",
        sections=[
            {"name": "A", "bars": 2, "energy": .4, "active_roles": ["Kick"]},
            {"name": "B", "bars": 2, "energy": .8, "active_roles": ["Kick", "Bass"]},
        ],
    )
    assert verify_arrangement_timeline(spec, tracks={"Kick": 0, "Bass": 1}, clips=[
        {"track_index": 0, "start_time": 0, "length": 16},
        {"track_index": 1, "start_time": 8, "length": 8},
    ]) == []
    reasons = verify_arrangement_timeline(spec, tracks={"Kick": 0, "Bass": 1}, clips=[
        {"track_index": 0, "start_time": 0, "length": 8},
    ])
    assert "ARRANGEMENT_DURATION_MISMATCH" in reasons
    assert "ARRANGEMENT_ROLE_MISSING:B:Bass" in reasons
    reasons = verify_arrangement_timeline(spec, tracks={"Kick": 0, "Bass": 1}, clips=[
        {"track_index": 0, "start_time": 0, "length": 16, "end_time": 16},
        {"track_index": 1, "start_time": 8, "length": 1, "end_time": 9},
    ])
    assert "ARRANGEMENT_ROLE_GAP:B:Bass" in reasons
    assert verify_arrangement_timeline(spec, tracks={"Kick": 0}, clips=[
        {"track_index": 0, "start_time": 0, "length": 16, "end_time": 12},
    ]) == ["ARRANGEMENT_CLIP_GEOMETRY_INVALID"]


def test_section_windows_cover_middle_end_and_both_transition_sides(monkeypatch, tmp_path):
    class ReadOnlyMock(MockAbletonAdapter):
        def snapshot(self, **_kwargs):
            return super().snapshot()

    daw = ReadOnlyMock()
    daw.connect()
    daw.session_path = str(tmp_path / "project.als")
    session = attach_tokens(daw.snapshot())
    spec = TrackSpec(
        bpm=120, primary_hook="kick pulse", hook_role="Kick",
        sections=[
            {"name": "Intro", "bars": 8, "energy": .3, "active_roles": ["Kick"]},
            {"name": "Drop", "bars": 8, "energy": .8, "active_roles": ["Kick"]},
        ],
    )
    observed = []
    def capture(_daw, start, end, *, require_signal):
        observed.append((start, end))
        file = tmp_path / f"{len(observed)}.wav"
        file.write_bytes(b"nonempty mock")
        return SimpleNamespace(
            capture_id=f"pass-{len(observed)}", file_path=file,
            rms=.1, peak=.8,
        )
    monkeypatch.setattr("copilot.audio.live_capture.capture_master_segment", capture)
    captures = _capture_sections(daw=daw, session=session, spec=spec)
    assert len(observed) == 6
    assert observed[0] == (0, 4)
    assert observed[1] == (14, 18)
    assert observed[2] == (28, 32)
    assert observed[3] == (32, 36)
    assert observed[-1] == (60, 64)
    assert captures["Intro"]["windows"]["ending"]["capture_id"] != captures["Drop"]["windows"]["opening"]["capture_id"]
