from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from copilot.daw.mock import MockAbletonAdapter
from copilot.integration.mixing_mastering_v1 import (
    ActiveRegionSelectionError,
    execute_lucas_mix_master_iteration,
    select_capturable_active_region,
)
from copilot.integration.autonomous_producer_alpha_v1 import _execute_goal_mix
from copilot.producer.goal import ProducerGoal
from copilot.producer.quality_gate import (
    evaluate_delivery, section_window_specs, verify_arrangement_timeline,
)
from copilot.producer.track_spec import TrackSpec


class MasterMock(MockAbletonAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.capabilities = {"device.physical_units_v1"}
        self.master_devices = [
            {
                "index": 0,
                "name": "Limiter",
                "class_name": "Limiter",
                "is_active": True,
                "parameters": [
                    {"index": 0, "name": "Device On", "value": 1.0, "min": 0.0, "max": 1.0},
                    {"index": 1, "name": "Ceiling", "value": 0.9, "min": 0.0, "max": 1.0},
                ],
            }
        ]

    def get_master_info(self):
        return {
            "name": "Master",
            "volume": 0.85,
            "panning": 0.0,
            "devices": [
                {key: value for key, value in device.items() if key != "parameters"}
                for device in self.master_devices
            ],
        }

    def get_device_parameters(self, track_index: int, device_index: int):
        if track_index == -1:
            device = self.master_devices[device_index]
            return {
                "track_index": -1,
                "device_index": device_index,
                "device_name": device["name"],
                "device_class": device["class_name"],
                "parameters": [dict(row) for row in device["parameters"]],
            }
        return super().get_device_parameters(track_index, device_index)

    def set_device_parameter(self, track_index, device_index, parameter_index, value):
        if track_index == -1:
            self._before_write("set_device_parameter")
            parameter = self.master_devices[device_index]["parameters"][parameter_index]
            parameter["value"] = max(parameter["min"], min(parameter["max"], value))
            return self._after_write(
                "set_device_parameter",
                {
                    "track_index": -1,
                    "device_index": device_index,
                    "parameter_index": parameter_index,
                    "value": parameter["value"],
                },
            )
        return super().set_device_parameter(track_index, device_index, parameter_index, value)

    def load_instrument_or_effect(self, track_index: int, uri: str):
        if track_index != -1:
            return super().load_instrument_or_effect(track_index, uri)
        self._before_write("load_instrument_or_effect")
        name = uri.rsplit("/", 1)[-1]
        index = len(self.master_devices)
        self.master_devices.append(
            {
                "index": index,
                "name": name,
                "class_name": name,
                "is_active": True,
                "parameters": [
                    {"index": 0, "name": "Device On", "value": 1.0, "min": 0.0, "max": 1.0},
                    {"index": 1, "name": "Mix", "value": 0.5, "min": 0.0, "max": 1.0},
                ],
            }
        )
        return self._after_write("load_instrument_or_effect", {"device_index": index})

    def delete_device(self, track_index: int, device_index: int):
        if track_index != -1:
            return super().delete_device(track_index, device_index)
        self._before_write("delete_device")
        self.master_devices.pop(device_index)
        for index, device in enumerate(self.master_devices):
            device["index"] = index
        return self._after_write("delete_device", {"deleted": True})


def test_real_capability_shape_executes_mix_and_master_then_rolls_back(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    daw.create_audio_track("Bassline")
    session = daw.snapshot()
    track = session.tracks[0]
    observations = {}

    def post_apply(phase, plan, pre, post):
        observations[phase] = {
            "plan_actions": [action.action_type.value for action in plan.actions],
            "pre_volume": pre.track_by_id(track.stable_id).mixer.volume,
            "post_volume": post.track_by_id(track.stable_id).mixer.volume,
            "post_master": post.track_by_name("Master").devices[0].parameters[1].value,
        }
        return {"decision": "ROLLBACK", "reason": "bounded test iteration"}

    report = execute_lucas_mix_master_iteration(
        strategy={
            "project_token": session.project_token,
            "mix": {
                "volume_actions": [
                    {
                        "operation": "set_track_volume",
                        "track_stable_id": track.stable_id,
                        "delta": -0.1,
                    }
                ]
            },
            "master": {
                "parameter_actions": [
                    {
                        "operation": "set_device_parameter",
                        "device_name": "Limiter",
                        "parameter_name": "Ceiling",
                        "normalized_value": 0.8,
                    }
                ]
            },
        },
        daw=daw,
        session=session,
        persist_dir=tmp_path,
        post_apply=post_apply,
    )

    assert report["status"] == "MIX_MASTER_ITERATION_COMPLETE"
    assert observations["mix"]["post_volume"] == pytest.approx(0.75)
    assert observations["master"]["post_master"] == pytest.approx(0.8)
    assert report["phases"]["mix"]["rollback_verified"] is True
    assert report["phases"]["master"]["rollback_verified"] is True
    assert daw.snapshot().tracks[0].mixer.volume == pytest.approx(0.85)
    assert daw.master_devices[0]["parameters"][1]["value"] == pytest.approx(0.9)


def test_ambiguous_track_target_is_deferred(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    daw.create_audio_track("Duplicate")
    daw.create_audio_track("Duplicate")
    session = daw.snapshot()
    report = execute_lucas_mix_master_iteration(
        strategy={
            "project_token": session.project_token,
            "mix": {"volume_actions": [{"operation": "set_track_volume", "track_name": "Duplicate", "delta": -0.1}]},
            "master": [],
        },
        daw=daw,
        session=session,
        persist_dir=tmp_path,
    )
    assert report["status"] == "MIX_MASTER_ITERATION_COMPLETE"
    assert report["phases"]["mix"]["writes_verified"] == 0
    assert report["phases"]["mix"]["deferred"][0]["status"] == "EXECUTION_DEFERRED"


def test_typed_limiter_ceiling_defers_without_unit_and_executes_with_verified_unit(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    session = daw.snapshot()
    strategy = {
        "master": {"parameter_actions": [{
            "operation": "set_typed_parameter", "device_name": "Limiter",
            "control": "ceiling", "value": -1.0, "unit": "db",
        }]},
    }
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=session, persist_dir=tmp_path,
        post_apply=lambda *_: {"decision": "ROLLBACK"},
    )
    assert report["phases"]["master"]["deferred"][0]["reason"] == "PHYSICAL_UNIT_UNCERTIFIED"
    assert report["phases"]["master"]["writes_verified"] == 0
    daw.master_devices[0]["parameters"][1].update(value=-0.5, min=-36.0, max=0.0, unit="db")
    seen = []
    def post_apply(phase, _plan, _pre, post):
        if phase == "master":
            seen.append(post.track_by_name("Master").devices[0].parameters[1].value)
        return {"decision": "ROLLBACK"}
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=daw.snapshot(), persist_dir=tmp_path,
        post_apply=post_apply,
    )
    assert report["phases"]["master"]["deferred"] == []
    assert report["phases"]["master"]["writes_verified"] == 1
    assert seen == [-1.0]
    assert daw.master_devices[0]["parameters"][1]["value"] == -0.5
    assert report["phases"]["master"]["rollback_verified"]


def test_typed_mixer_requires_capability_and_post_write_physical_readback(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    row = daw.master_devices[0]["parameters"][1]
    row.update(value=-0.5, min=-36.0, max=0.0, unit="db")
    strategy = {"master": {"parameter_actions": [{
        "operation": "set_typed_parameter", "device_name": "Limiter",
        "control": "ceiling", "value": -1.0, "unit": "db",
    }]}}
    daw.capabilities = set()
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=daw.snapshot(), persist_dir=tmp_path,
        post_apply=lambda *_: {"decision": "KEEP"},
    )
    assert report["phases"]["master"]["deferred"][0]["reason"] == "PHYSICAL_UNIT_CAPABILITY_UNAVAILABLE"
    assert row["value"] == -0.5
    daw.capabilities = {"device.physical_units_v1"}
    daw.handshake_info = {"mode": "LEGACY"}
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=daw.snapshot(), persist_dir=tmp_path,
        post_apply=lambda *_: {"decision": "KEEP"},
    )
    assert report["phases"]["master"]["deferred"][0]["reason"] == "PHYSICAL_UNIT_CAPABILITY_UNAVAILABLE"
    daw.handshake_info = {"mode": "NEGOTIATED"}
    original = daw.get_device_parameters

    def strip_unit_after_write(track_index, device_index):
        payload = original(track_index, device_index)
        if row["value"] == -1.0:
            payload["parameters"][1].pop("unit")
        return payload

    daw.get_device_parameters = strip_unit_after_write
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=daw.snapshot(), persist_dir=tmp_path,
        post_apply=lambda *_: {"decision": "KEEP"},
    )
    assert report["status"] == "FAILED"
    assert report["phases"]["master"]["rollback_verified"]
    assert row["value"] == -0.5


def test_goal_limiter_apply_uses_safe_write_and_fresh_audio(monkeypatch, tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    daw.session_path = str(tmp_path / "Mix.als")
    row = daw.master_devices[0]["parameters"][1]
    row.update(value=-0.5, min=-36.0, max=0.0, unit="db")
    audio = tmp_path / "before.wav"
    after_audio = tmp_path / "after.wav"
    wave = .2 * np.sin(2 * np.pi * 120 * np.arange(8000) / 8000)
    before_wave = wave.copy()
    after_wave = wave.copy()
    before_wave[2000] = .8
    after_wave[2000] = .35
    sf.write(audio, before_wave, 8000)
    sf.write(after_audio, after_wave, 8000)
    next_capture = iter((("before", audio, .8), ("after", after_audio, .35)))
    def captured(*_args, **_kwargs):
        capture_id, path, peak = next(next_capture)
        return SimpleNamespace(capture_id=capture_id, file_path=path, rms=.14, peak=peak)
    monkeypatch.setattr(
        "copilot.audio.live_capture.capture_master_segment",
        captured,
    )
    spec = TrackSpec(
        bpm=120, primary_hook="kick", hook_role="Kick",
        sections=[{"name": "A", "bars": 4, "energy": .5, "active_roles": ["Kick"]}],
    )
    report, actions = _execute_goal_mix(
        daw=daw, spec=spec, project_identity=daw.snapshot().project_identity,
        persist_dir=tmp_path,
        decisions={"eq_eight": {"decision": "NONE", "reason": "No EQ"},
                   "limiter": {"decision": "APPLY", "reason": "Audition a lower ceiling",
                               "objective": "reduce_peak", "hypothesis": "Limiter controls excursions",
                               "action": {"track_name": "Master", "control": "ceiling",
                                          "value": -1.0, "unit": "db"}}},
    )
    assert actions[0]["status"] == "VERIFIED", (actions, report["result"]["phases"]["master"]["post_apply"])
    assert report["captures"]["master"]["capture_id"] == "after"
    assert row["value"] == -1.0
    project = Path(daw.session_path)
    project.write_bytes(b"saved mock Live set")
    source = {
        "ok": True, "signal_status": "HAS_SIGNAL", "restore": {"ok": True},
        "audio_sha256": "mock-audio-digest", "wav_path": str(audio),
    }
    geometry = verify_arrangement_timeline(
        spec, tracks={"Kick": 0},
        clips=[{"track_index": 0, "start_time": 0, "length": 16, "end_time": 16}],
    )
    gate = dict(
        goal=ProducerGoal.from_prompt("BPM: 120\nDuración: 4 compases"),
        spec=spec, project_path=project, project_identity=daw.snapshot().project_identity,
        reopened_identity=daw.snapshot().project_identity, tempo_bpm=120,
        arrangement=[{"section": "A", "track": "Kick", "status": "VERIFIED"}],
        arrangement_geometry=geometry,
        captures={"A": {
            "capture_id": "after", "path": str(audio), "rms": .2, "peak": .8,
            "windows": {
                position: {
                    "capture_id": position, "path": str(audio), "rms": .2, "peak": .8,
                    "start_qn": start, "end_qn": end,
                    "project_identity": daw.snapshot().project_identity,
                }
                for position, (start, end) in section_window_specs(spec)["A"].items()
            },
        }},
        role_captures={"Kick": source, "Bass": source},
        actions=actions, critique_verdict="finalize", transport_stopped=True,
    )
    assert evaluate_delivery(**gate)["status"] == "COMPLETE"
    gate["actions"] = [{**actions[0], "status": "EXECUTION_DEFERRED"}]
    assert evaluate_delivery(**gate)["status"] == "DRAFT"
    gate["actions"] = actions
    gate["tempo_bpm"] = 121
    assert "LIVE_TEMPO_MISMATCH" in evaluate_delivery(**gate)["reasons"]


def test_goal_limiter_apply_without_unit_capability_defers(monkeypatch, tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    daw.session_path = str(tmp_path / "Mix.als")
    daw.capabilities = set()
    audio = tmp_path / "listened.wav"
    sf.write(audio, .2 * np.sin(2 * np.pi * 120 * np.arange(8000) / 8000), 8000)
    monkeypatch.setattr(
        "copilot.audio.live_capture.capture_master_segment",
        lambda *_args, **_kwargs: SimpleNamespace(
            capture_id="before", file_path=audio, rms=.2, peak=.8,
        ),
    )
    spec = TrackSpec(
        bpm=120, primary_hook="kick", hook_role="Kick",
        sections=[{"name": "A", "bars": 4, "energy": .5, "active_roles": ["Kick"]}],
    )
    _, actions = _execute_goal_mix(
        daw=daw, spec=spec, project_identity=daw.snapshot().project_identity,
        persist_dir=tmp_path,
        decisions={"eq_eight": {"decision": "NONE", "reason": "No EQ"},
                   "limiter": {"decision": "APPLY", "reason": "Audition",
                               "objective": "reduce_peak", "hypothesis": "Peaks are too large",
                               "action": {"track_name": "Master", "control": "ceiling",
                                          "value": -1.0, "unit": "db"}}},
    )
    assert actions[0]["status"] == "EXECUTION_DEFERRED"
    assert actions[0]["reason"] == "PHYSICAL_UNIT_CAPABILITY_UNAVAILABLE"
    assert daw.master_devices[0]["parameters"][1]["value"] == 0.9


def test_goal_limiter_rolls_back_when_audio_objective_does_not_improve(monkeypatch, tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    daw.session_path = str(tmp_path / "Mix.als")
    parameter = daw.master_devices[0]["parameters"][1]
    parameter.update(value=-0.5, min=-36.0, max=0.0, unit="db")
    before = tmp_path / "before.wav"
    after = tmp_path / "after.wav"
    wave = .2 * np.sin(2 * np.pi * 120 * np.arange(8000) / 8000)
    sf.write(before, wave, 8000)
    sf.write(after, wave + 0.001, 8000)
    captures = iter((("before", before), ("after", after)))
    def capture(*_args, **_kwargs):
        identity, path = next(captures)
        return SimpleNamespace(capture_id=identity, file_path=path, rms=.14, peak=.21)
    monkeypatch.setattr("copilot.audio.live_capture.capture_master_segment", capture)
    spec = TrackSpec(
        bpm=120, primary_hook="kick", hook_role="Kick",
        sections=[{"name": "A", "bars": 4, "energy": .5, "active_roles": ["Kick"]}],
    )
    report, rows = _execute_goal_mix(
        daw=daw, spec=spec, project_identity=daw.snapshot().project_identity,
        persist_dir=tmp_path,
        decisions={
            "eq_eight": {"decision": "NONE", "reason": "No measured need"},
            "limiter": {"decision": "APPLY", "reason": "Try lower ceiling",
                        "hypothesis": "Lower ceiling reduces peak",
                        "objective": "reduce_peak",
                        "action": {"track_name": "Master", "control": "ceiling",
                                   "value": -1.0, "unit": "db"}},
        },
    )
    assert rows[0]["status"] == "FAILED"
    assert rows[0]["reason"] == "MIX_OBJECTIVE_NOT_IMPROVED"
    assert report["result"]["phases"]["master"]["rollback_verified"]
    assert parameter["value"] == -0.5


def test_typed_eq_requires_native_units_and_rejects_normalized_value(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    daw.create_audio_track("Bassline")
    row = daw.tracks[0]["devices"][0]["parameters"][0]
    row.update(value=-2.0, min=-15.0, max=15.0, unit="db")
    session = daw.snapshot()
    strategy = {"mix": {"parameter_actions": [{
        "operation": "set_typed_parameter", "track_stable_id": session.tracks[0].stable_id,
        "device_name": "EQ Eight", "control": "gain", "band": 1, "value": -3.0,
        "unit": "normalized",
    }]}}
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=session, persist_dir=tmp_path,
        post_apply=lambda *_: {"decision": "ROLLBACK"},
    )
    assert report["phases"]["mix"]["deferred"][0]["reason"] == "VALUE_UNIT_UNCERTIFIED"
    assert row["value"] == -2.0
    strategy["mix"]["parameter_actions"][0]["unit"] = "db"
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=daw.snapshot(), persist_dir=tmp_path,
        post_apply=lambda *_: {"decision": "ROLLBACK"},
    )
    assert report["phases"]["mix"]["writes_verified"] == 1
    assert report["phases"]["mix"]["rollback_verified"]
    assert row["value"] == -2.0


def test_typed_limiter_bypass_requires_quantized_device_on_and_rolls_back(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    on = daw.master_devices[0]["parameters"][0]
    on["is_quantized"] = True
    strategy = {"master": {"parameter_actions": [{
        "operation": "set_typed_parameter", "device_name": "Limiter",
        "control": "enabled", "value": False, "unit": "boolean",
    }]}}
    seen = []
    def post_apply(phase, _plan, _pre, post):
        if phase == "master":
            seen.append(post.track_by_name("Master").devices[0].parameters[0].value)
        return {"decision": "ROLLBACK"}
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=daw.snapshot(), persist_dir=tmp_path, post_apply=post_apply,
    )
    assert report["phases"]["master"]["writes_verified"] == 1
    assert report["phases"]["master"]["rollback_verified"] is True
    assert seen == [0.0]
    assert on["value"] == 1.0


def test_typed_parameter_rejects_stale_read_and_legacy_unit_spoof(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    daw.master_devices[0]["parameters"][1].update(value=-0.5, min=-36.0, max=0.0, unit="db")
    original = daw.get_device_parameters
    def stale(track_index, device_index):
        payload = original(track_index, device_index)
        if track_index == -1:
            payload["parameters"][1]["value"] = -2.0
        return payload
    # Only the second parameter fetch is stale; the master snapshot remains
    # authoritative and the write is deferred.
    calls = [0]
    def intermittent(track_index, device_index):
        calls[0] += 1
        return stale(track_index, device_index) if calls[0] % 2 == 0 else original(track_index, device_index)
    daw.get_device_parameters = intermittent
    strategy = {"master": {"parameter_actions": [{
        "operation": "set_typed_parameter", "device_name": "Limiter",
        "control": "ceiling", "value": -1.0, "unit": "db",
    }]}}
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=daw.snapshot(), persist_dir=tmp_path,
        post_apply=lambda *_: {"decision": "ROLLBACK"},
    )
    assert report["phases"]["master"]["writes_verified"] == 0
    assert report["phases"]["master"]["deferred"][0]["reason"] == "PARAMETER_SNAPSHOT_MISMATCH"
    daw.get_device_parameters = original
    strategy["master"]["parameter_actions"][0] = {
        "operation": "set_device_parameter", "device_name": "Limiter",
        "parameter_name": "Ceiling", "normalized_value": 0.8, "unit": "db",
    }
    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=daw.snapshot(), persist_dir=tmp_path,
        post_apply=lambda *_: {"decision": "ROLLBACK"},
    )
    assert report["phases"]["master"]["writes_verified"] == 0
    assert report["phases"]["master"]["deferred"][0]["reason"] == "PHYSICAL_UNIT_UNCERTIFIED"


def test_null_parameter_max_defers_write_without_inventing_range(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    parameter = daw.master_devices[0]["parameters"][1]
    parameter["max"] = None
    strategy = {"master": {"parameter_actions": [{
        "operation": "set_device_parameter", "device_name": "Limiter",
        "parameter_name": "Ceiling", "normalized_value": 0.5,
    }]}}

    report = execute_lucas_mix_master_iteration(
        strategy=strategy, daw=daw, session=daw.snapshot(), persist_dir=tmp_path,
        post_apply=lambda *_: {"decision": "ROLLBACK"},
    )

    assert report["phases"]["master"]["writes_verified"] == 0
    assert report["phases"]["master"]["deferred"][0]["reason"] == "PARAMETER_RANGE_UNAVAILABLE"
    assert parameter["value"] == 0.9


def test_unsupported_strategy_does_not_write(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    daw.create_audio_track("Bassline")
    session = daw.snapshot()
    before = daw.snapshot().state_hash
    report = execute_lucas_mix_master_iteration(
        strategy={
            "project_token": session.project_token,
            "mix": {"parameter_actions": [{"operation": "load_device_preset", "track_name": "Bassline", "device_name": "EQ Eight"}]},
            "master": [],
        },
        daw=daw,
        session=session,
        persist_dir=tmp_path,
    )
    assert report["status"] == "MIX_MASTER_ITERATION_COMPLETE"
    assert report["phases"]["mix"]["writes_verified"] == 0
    assert report["phases"]["mix"]["deferred"][0]["reason"] == "EXECUTION_DEFERRED_UNSUPPORTED_OPERATION"
    assert daw.snapshot().state_hash == before


def test_post_apply_failure_fails_closed_and_rolls_back(tmp_path: Path):
    daw = MasterMock()
    daw.connect()
    daw.create_audio_track("Bassline")
    session = daw.snapshot()

    def broken_post_apply(*_args):
        raise RuntimeError("analysis unavailable")

    report = execute_lucas_mix_master_iteration(
        strategy={
            "project_token": session.project_token,
            "mix": {
                "volume_actions": [
                    {"operation": "set_track_volume", "track_stable_id": session.tracks[0].stable_id, "delta": -0.1}
                ]
            },
            "master": [],
        },
        daw=daw,
        session=session,
        persist_dir=tmp_path,
        post_apply=broken_post_apply,
    )

    assert report["status"] == "MIX_MASTER_ITERATION_COMPLETE"
    assert report["phases"]["mix"]["decision"] == "ROLLBACK"
    assert report["phases"]["mix"]["post_apply"]["reason"] == "POST_APPLY_FAILED"
    assert report["phases"]["mix"]["rollback_verified"] is True
    assert daw.snapshot().tracks[0].mixer.volume == pytest.approx(0.85)


def test_active_region_selector_skips_leading_silence_and_muted_tracks(tmp_path: Path, monkeypatch):
    daw = MasterMock()
    daw.connect()
    daw.create_audio_track("Muted source")
    daw.create_audio_track("Active source")
    session = daw.snapshot()
    session = session.model_copy(update={"project_path": str(tmp_path / "working.als")})
    Path(session.project_path).write_bytes(b"fixture")
    muted = session.track_by_name("Muted source")
    active = session.track_by_name("Active source")
    assert muted is not None and active is not None
    muted.mixer.mute = True

    monkeypatch.setattr(
        "copilot.integration.mixing_mastering_v1.load_arrangement_clips",
        lambda _path: [
            {"track": "Muted source", "start_qn": 0.0, "end_qn": 64.0, "kind": "AudioClip"},
            {"track": "Active source", "start_qn": 40.0, "end_qn": 72.0, "kind": "AudioClip"},
        ],
    )

    region = select_capturable_active_region(session)

    assert region["start_beat"] == pytest.approx(40.0)
    assert region["end_beat"] == pytest.approx(56.0)
    assert region["coverage_ratio"] == pytest.approx(1.0)
    assert region["active_tracks"] == ["Active source"]


def test_active_region_selector_fails_closed_without_content(tmp_path: Path, monkeypatch):
    daw = MasterMock()
    daw.connect()
    daw.create_audio_track("Empty source")
    session = daw.snapshot().model_copy(update={"project_path": str(tmp_path / "working.als")})
    Path(session.project_path).write_bytes(b"fixture")
    monkeypatch.setattr(
        "copilot.integration.mixing_mastering_v1.load_arrangement_clips",
        lambda _path: [],
    )

    with pytest.raises(ActiveRegionSelectionError, match="NO_ACTIVE_ARRANGEMENT_CLIPS"):
        select_capturable_active_region(session)
