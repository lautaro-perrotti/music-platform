from __future__ import annotations

import json

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens, target_token
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


def _session_with_device():
    from copilot.schemas.session import (
        DeviceParameter,
        DeviceState,
        MixerState,
        SessionState,
        TrackState,
    )
    session = SessionState(
        project_identity="executor_lab",
        project_name="executor_lab",
        tracks=[
            TrackState(
                stable_id="trk_1", index=0, name="Bass", role="midi",
                mixer=MixerState(volume=0.8),
                devices=[
                    DeviceState(
                        stable_id="dev_1", index=0, name="EQ Eight", class_name="Eq8",
                        parameters=[DeviceParameter(index=1, name="Frequency", value=0.5)],
                    ),
                ],
            )
        ],
    )
    attach_tokens(session)
    return session


def _device_tweak_plan(session):
    from copilot.human_eval.store import now_iso
    from copilot.musicplan import build_device_tweak_action
    from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass

    track = session.tracks[0]
    action = build_device_tweak_action(
        track=track,
        project_identity=session.project_identity,
        device_index=0,
        parameter_name="Frequency",
        expected_before=0.5,
        intended_after=0.6,
        reason="test",
        evidence_refs=["ev.1"],
    )
    return MusicPlan(
        plan_id="plan_test",
        diagnosis=DiagnosisBinding(
            diagnosis_id="d1", diagnosis_status="SUPPORTED",
            diagnosis_accepted=True, cause_status="CAUSE_SUPPORTED",
        ),
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        project_state_token=session.project_token or session.project_identity,
        audible_state_token=session.audible_token or "",
        target_state_tokens={track.name: target_token(track)},
        evidence_refs=["ev.1"],
        actions=[action],
        created_at=now_iso(),
    )


def test_validate_device_tweak_ready():
    from copilot.musicplan import validate_device_tweak_plan
    from copilot.schemas.musicplan import PlanStatus
    session = _session_with_device()
    plan = _device_tweak_plan(session)
    result = validate_device_tweak_plan(plan, session=session)
    assert result.status is PlanStatus.READY_FOR_EXECUTION
    assert result.actions[0].rollback.restore_value == 0.5


def test_validate_device_tweak_param_mismatch():
    from copilot.musicplan import validate_device_tweak_plan
    from copilot.schemas.musicplan import PlanStatus
    session = _session_with_device()
    plan = _device_tweak_plan(session)
    plan.actions[0].params.expected_before = 0.9  # live is 0.5
    result = validate_device_tweak_plan(plan, session=session)
    assert result.status is PlanStatus.REJECTED
    assert "EXPECTED_PARAM_MISMATCH" in (result.rejection_reason or "")


def test_validate_device_tweak_param_not_found():
    from copilot.musicplan import validate_device_tweak_plan
    from copilot.schemas.musicplan import PlanStatus
    session = _session_with_device()
    plan = _device_tweak_plan(session)
    plan.actions[0].params.parameter_name = "Q"
    result = validate_device_tweak_plan(plan, session=session)
    assert result.status is PlanStatus.REJECTED
    assert result.rejection_reason == "PARAMETER_NOT_FOUND"


def test_execute_device_tweak_write_loop(tmp_path):
    from copilot.human_eval.store import now_iso
    from copilot.musicplan import build_device_tweak_action
    from copilot.musicplan.execute import (
        build_agent_tools,
        execute_device_tweak_write_loop,
    )
    from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass

    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track("Pad")  # EQ Eight device, "1 Gain A" = 0.5
    session = daw.snapshot()
    attach_tokens(session)
    track = session.tracks[0]

    action = build_device_tweak_action(
        track=track,
        project_identity=session.project_identity,
        device_index=0,
        parameter_name="1 Gain A",
        expected_before=0.5,
        intended_after=0.7,
        reason="test",
        evidence_refs=["ev.1"],
    )
    plan = MusicPlan(
        plan_id="plan_tweak",
        diagnosis=DiagnosisBinding(
            diagnosis_id="d1", diagnosis_status="SUPPORTED",
            diagnosis_accepted=True, cause_status="CAUSE_SUPPORTED",
        ),
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        project_state_token=session.project_token or session.project_identity,
        audible_state_token=session.audible_token or "",
        target_state_tokens={track.name: target_token(track)},
        evidence_refs=["ev.1"],
        actions=[action],
        created_at=now_iso(),
    )

    tools = build_agent_tools(daw, journal_path=tmp_path / "journal.jsonl")
    report = execute_device_tweak_write_loop(
        tools, plan=plan, session=session, persist_dir=tmp_path
    )

    assert report["status"] == "CONTROLLED_WRITE_LOOP_COMPLETE", report
    assert report["EXECUTION_VERIFICATION"] == "PASS"
    assert report["after_readback"] == 0.7
    assert report["rollback_readback"] == 0.5
    assert report["RESTORE_VERIFIED"] is True
    assert report["MUSICAL_WRITE_COUNT"] == {"forward": 1, "rollback": 1}
    assert report["open_transaction"] is False


def test_execute_device_tweak_rejects_bad_expected_before(tmp_path):
    from copilot.human_eval.store import now_iso
    from copilot.musicplan import build_device_tweak_action
    from copilot.musicplan.execute import (
        build_agent_tools,
        execute_device_tweak_write_loop,
    )
    from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass

    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track("Pad")
    session = daw.snapshot()
    attach_tokens(session)
    track = session.tracks[0]

    action = build_device_tweak_action(
        track=track, project_identity=session.project_identity,
        device_index=0, parameter_name="1 Gain A",
        expected_before=0.9,  # live is 0.5 -> must reject
        intended_after=0.7, reason="test", evidence_refs=["ev.1"],
    )
    plan = MusicPlan(
        plan_id="plan_bad",
        diagnosis=DiagnosisBinding(
            diagnosis_id="d1", diagnosis_status="SUPPORTED",
            diagnosis_accepted=True, cause_status="CAUSE_SUPPORTED",
        ),
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        project_state_token=session.project_token or session.project_identity,
        audible_state_token=session.audible_token or "",
        target_state_tokens={track.name: target_token(track)},
        evidence_refs=["ev.1"], actions=[action], created_at=now_iso(),
    )
    tools = build_agent_tools(daw, journal_path=tmp_path / "journal.jsonl")
    report = execute_device_tweak_write_loop(
        tools, plan=plan, session=session, persist_dir=tmp_path
    )
    assert report["status"] == "NOT_EXECUTABLE"
    assert "EXPECTED_PARAM_MISMATCH" in (report.get("error") or "")
