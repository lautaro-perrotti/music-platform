from __future__ import annotations

from pathlib import Path

import pytest

from copilot.daw.mock import MockAbletonAdapter
from copilot.integration.mixing_mastering_v1 import execute_lucas_mix_master_iteration


class MasterMock(MockAbletonAdapter):
    def __init__(self) -> None:
        super().__init__()
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
