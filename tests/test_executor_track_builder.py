from __future__ import annotations

from pathlib import Path

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens, target_token


def _session():
    daw = MockAbletonAdapter()
    daw.connect()
    daw.session_path = r"D:\sets\trackbuilder_lab.als"
    daw.session_name = "trackbuilder_lab"
    session = daw.snapshot()
    attach_tokens(session)
    return daw, session


def _create_track_plan(session, track_name="Kick"):
    from copilot.human_eval.store import now_iso
    from copilot.musicplan import build_create_track_action
    from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass

    action = build_create_track_action(
        project_identity=session.project_identity,
        track_name=track_name,
        track_kind="audio",
        reason="tech house kick track",
        evidence_refs=["ev.1"],
    )
    return MusicPlan(
        plan_id="plan_ct",
        diagnosis=DiagnosisBinding(
            diagnosis_id="d1", diagnosis_status="SUPPORTED",
            diagnosis_accepted=True, cause_status="CAUSE_SUPPORTED",
        ),
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        project_state_token=session.project_token or session.project_identity,
        audible_state_token=session.audible_token or "",
        target_state_tokens={},
        evidence_refs=["ev.1"],
        actions=[action],
        created_at=now_iso(),
    )


def _sample_load_plan(session, clip_index=0, sample_uri="samples/kick.wav"):
    from copilot.human_eval.store import now_iso
    from copilot.musicplan import build_sample_load_action
    from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass

    track = session.tracks[0]
    action = build_sample_load_action(
        track=track,
        project_identity=session.project_identity,
        clip_index=clip_index,
        sample_uri=sample_uri,
        reason="load kick",
        evidence_refs=["ev.1"],
    )
    return MusicPlan(
        plan_id="plan_sl",
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


# ---- CREATE_TRACK ----

def test_validate_create_track_ready():
    from copilot.musicplan import validate_create_track_plan
    from copilot.schemas.musicplan import PlanStatus
    _, session = _session()
    result = validate_create_track_plan(_create_track_plan(session), session=session)
    assert result.status is PlanStatus.READY_FOR_EXECUTION


def test_validate_create_track_rejects_duplicate_name():
    from copilot.musicplan import validate_create_track_plan
    from copilot.schemas.musicplan import PlanStatus
    daw, session = _session()
    daw.create_audio_track("Kick")  # now "Kick" exists
    session = daw.snapshot()
    attach_tokens(session)
    result = validate_create_track_plan(_create_track_plan(session), session=session)
    assert result.status is PlanStatus.REJECTED
    assert "TRACK_ALREADY_EXISTS" in (result.rejection_reason or "")


def test_execute_create_track_write_loop(tmp_path):
    from copilot.musicplan.execute import (
        build_agent_tools,
        execute_create_track_write_loop,
    )
    daw, session = _session()
    plan = _create_track_plan(session)
    tools = build_agent_tools(daw, journal_path=tmp_path / "journal.jsonl")
    report = execute_create_track_write_loop(
        tools, plan=plan, session=session, persist_dir=tmp_path
    )
    assert report["status"] == "CONTROLLED_WRITE_LOOP_COMPLETE", report
    assert report["EXECUTION_VERIFICATION"] == "PASS"
    assert report["track_present_after_write"] is True
    assert report["track_present_after_rollback"] is False
    assert report["MUSICAL_WRITE_COUNT"] == {"forward": 1, "rollback": 1}
    assert report["RESTORE_VERIFIED"] is True


# ---- SAMPLE_LOAD ----

def _session_with_audio_track():
    daw, session = _session()
    daw.create_audio_track("Kick")
    session = daw.snapshot()
    attach_tokens(session)
    return daw, session


def test_validate_sample_load_ready():
    from copilot.musicplan import validate_sample_load_plan
    from copilot.schemas.musicplan import PlanStatus
    _, session = _session_with_audio_track()
    result = validate_sample_load_plan(_sample_load_plan(session), session=session)
    assert result.status is PlanStatus.READY_FOR_EXECUTION


def test_validate_sample_load_rejects_occupied_slot():
    from copilot.musicplan import validate_sample_load_plan
    from copilot.schemas.musicplan import PlanStatus
    daw, session = _session_with_audio_track()
    daw.load_browser_item(0, "samples/old.wav", clip_index=0)  # occupy slot 0
    session = daw.snapshot()
    attach_tokens(session)
    result = validate_sample_load_plan(_sample_load_plan(session), session=session)
    assert result.status is PlanStatus.REJECTED
    assert "SLOT_OCCUPIED" in (result.rejection_reason or "")


def test_execute_sample_load_write_loop(tmp_path):
    from copilot.musicplan.execute import (
        build_agent_tools,
        execute_sample_load_write_loop,
    )
    daw, session = _session_with_audio_track()
    plan = _sample_load_plan(session)
    tools = build_agent_tools(daw, journal_path=tmp_path / "journal.jsonl")
    report = execute_sample_load_write_loop(
        tools, plan=plan, session=session, persist_dir=tmp_path
    )
    assert report["status"] == "CONTROLLED_WRITE_LOOP_COMPLETE", report
    assert report["EXECUTION_VERIFICATION"] == "PASS"
    assert report["after_sample_uri"] == "samples/kick.wav"
    assert report["clip_present_after_rollback"] is False
    assert report["MUSICAL_WRITE_COUNT"] == {"forward": 1, "rollback": 1}
    assert report["RESTORE_VERIFIED"] is True
