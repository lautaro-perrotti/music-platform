"""A multi-group compound must never certify an action it does not execute.

Before the fix, `_compile_multi_midi_variation` took only the first device load,
pattern and placement of each group but listed every plan action in
`certified_action_ids`, so readback could "verify" writes that never happened.
"""

from __future__ import annotations

from datetime import datetime, timezone

from copilot.musicplan import (
    build_create_track_action,
    build_device_load_action,
    build_duplicate_clip_to_arrangement_action,
    build_pattern_action,
)
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass, PlanStatus
from copilot.schemas.session import MidiNote, SessionState, TrackState

PID = "pid_test"


def _session() -> SessionState:
    return SessionState(project_identity=PID, project_token="ptok", audible_token="atok",
                        session_incarnation_id="sess_test")


def _group(name: str, devices: tuple[str, ...] = (), patterns: int = 1, placements: int = 1) -> list:
    virtual = TrackState(stable_id="", index=-1, name=name, role="midi")
    actions = [build_create_track_action(project_identity=PID, track_name=name, track_kind="midi",
                                         reason="test", evidence_refs=["test"])]
    for device in devices:
        actions.append(build_device_load_action(track=virtual, project_identity=PID, device_name=device,
                                                device_uri=f"query:{device}", reason="test",
                                                evidence_refs=["test"]))
    for _ in range(patterns):
        actions.append(build_pattern_action(track=virtual, project_identity=PID, clip_index=0, length_beats=4.0,
                                            notes=[MidiNote(pitch=60, start_time=0.0, duration=0.25)],
                                            reason="test", evidence_refs=["test"]))
    for _ in range(placements):
        actions.append(build_duplicate_clip_to_arrangement_action(track=virtual, project_identity=PID, clip_index=0,
                                                                   destination_time=0.0, length=None,
                                                                   reason="test", evidence_refs=["test"]))
    return actions


def _plan(actions: list) -> MusicPlan:
    return MusicPlan(
        plan_id="plan_test", status=PlanStatus.READY_FOR_EXECUTION,
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        diagnosis=DiagnosisBinding(diagnosis_id="test", diagnosis_status="TEST", diagnosis_accepted=True),
        project_state_token="ptok", audible_state_token="atok", evidence_refs=["test"], actions=actions,
        notes=["test"], created_at=datetime.now(timezone.utc).isoformat(),
    )


def _compile(actions: list):
    return ProductionCompiler().compile(_plan(actions), session=_session())


def test_single_device_per_group_compiles_and_certifies_every_action() -> None:
    actions = _group("A", devices=("Operator",)) + _group("B")
    result = _compile(actions)
    assert result.status == "COMPILED", result.reasons
    executed = {execution.action_id for execution in result.intent.executions}
    assert set(result.certified_action_ids) == executed == {action.action_id for action in actions}


def test_second_device_load_in_a_group_is_rejected_not_silently_dropped() -> None:
    actions = _group("A", devices=("Operator", "Saturator")) + _group("B")
    result = _compile(actions)
    assert result.status == "PLAN_REJECTED"
    assert "MULTI_MIDI_GROUP_DUPLICATE_ACTION" in result.reasons
    assert result.intent is None
    assert not result.certified_action_ids


def test_second_pattern_or_placement_in_a_group_is_rejected() -> None:
    for kwargs in ({"patterns": 2}, {"placements": 2}):
        result = _compile(_group("A", **kwargs) + _group("B"))
        assert result.status == "PLAN_REJECTED", kwargs
        assert "MULTI_MIDI_GROUP_DUPLICATE_ACTION" in result.reasons
