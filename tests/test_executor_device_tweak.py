from __future__ import annotations

import json

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.musicplan import build_device_tweak_action
from copilot.schemas.musicplan import (
    ActionType,
    DeviceTweakActionParams,
    PlanAction,
)


def _session():
    daw = MockAbletonAdapter()
    daw.connect()
    daw.session_path = r"D:\sets\executor_lab.als"
    daw.session_name = "executor_lab"
    daw.create_midi_track("Bass")
    session = daw.snapshot()
    attach_tokens(session)
    return daw, session


def test_build_device_tweak_action_well_formed():
    _, session = _session()
    track = session.tracks[0]
    action = build_device_tweak_action(
        track=track,
        project_identity=session.project_identity or "executor_lab",
        device_index=0,
        parameter_name="Frequency",
        expected_before=0.5,
        intended_after=0.6,
        reason="test",
        evidence_refs=["ev.1"],
        session_incarnation_id=session.session_incarnation_id or "",
    )
    assert action.action_type is ActionType.DEVICE_TWEAK
    assert isinstance(action.params, DeviceTweakActionParams)
    assert action.params.parameter_name == "Frequency"
    assert action.params.expected_before == 0.5
    assert action.params.intended_after == 0.6
    assert action.rollback is not None and action.rollback.prepared is True
    assert action.verification is not None and action.verification.execution is not None
    assert action.rollback.restore_value == 0.5


def test_device_tweak_params_polymorphic_roundtrip():
    action = PlanAction.model_validate_json(
        json.dumps({
            "action_id": "act_test",
            "action_type": "DEVICE_TWEAK",
            "target": {"ref": {}, "track_index_locator": 0},
            "params": {
                "kind": "device_tweak",
                "device_index": 2,
                "parameter_name": "Threshold",
                "expected_before": 0.3,
                "intended_after": 0.4,
            },
            "reason": "t",
            "evidence_refs": [],
            "expected_effect": {"affected_target": "x", "direction": "increase", "description": "d"},
        })
    )
    assert isinstance(action.params, DeviceTweakActionParams)
    assert action.params.device_index == 2
    assert action.params.parameter_name == "Threshold"
