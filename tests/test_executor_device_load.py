from __future__ import annotations

from pathlib import Path

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens, target_token


def _session():
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track("Pad")  # EQ Eight device
    session = daw.snapshot()
    attach_tokens(session)
    return daw, session


def _device_load_plan(session):
    from copilot.human_eval.store import now_iso
    from copilot.musicplan import build_device_load_action
    from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass

    track = session.tracks[0]
    action = build_device_load_action(
        track=track,
        project_identity=session.project_identity,
        device_name="Compressor",
        device_uri="devices/native/Compressor",
        reason="tame dynamics",
        evidence_refs=["ev.1"],
    )
    return MusicPlan(
        plan_id="plan_load",
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


def test_validate_device_load_ready():
    from copilot.musicplan import validate_device_load_plan
    from copilot.schemas.musicplan import PlanStatus
    _, session = _session()
    result = validate_device_load_plan(_device_load_plan(session), session=session)
    assert result.status is PlanStatus.READY_FOR_EXECUTION


def test_validate_device_load_rejects_duplicate():
    from copilot.musicplan import validate_device_load_plan
    from copilot.schemas.musicplan import PlanStatus
    _, session = _session()
    plan = _device_load_plan(session)
    plan.actions[0].params.device_name = "EQ Eight"  # already present
    result = validate_device_load_plan(plan, session=session)
    assert result.status is PlanStatus.REJECTED
    assert "DEVICE_ALREADY_PRESENT" in (result.rejection_reason or "")


def test_execute_device_load_write_loop(tmp_path):
    from copilot.musicplan.execute import (
        build_agent_tools,
        execute_device_load_write_loop,
    )
    daw, session = _session()
    plan = _device_load_plan(session)
    tools = build_agent_tools(daw, journal_path=tmp_path / "journal.jsonl")
    report = execute_device_load_write_loop(
        tools, plan=plan, session=session, persist_dir=tmp_path
    )
    assert report["status"] == "CONTROLLED_WRITE_LOOP_COMPLETE", report
    assert report["EXECUTION_VERIFICATION"] == "PASS"
    assert report["device_present_after_write"] is True
    assert report["device_present_after_rollback"] is False
    assert report["RESTORE_VERIFIED"] is True
    assert report["MUSICAL_WRITE_COUNT"] == {"forward": 1, "rollback": 1}
    assert report["open_transaction"] is False
