from __future__ import annotations

from pathlib import Path

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens, target_token


def _session_with_audio_clip():
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_audio_track("Pad")
    # pre-populate an audio clip at slot 0 with an "old" sample
    daw.load_browser_item(0, "samples/hihat_old.wav", clip_index=0)
    session = daw.snapshot()
    attach_tokens(session)
    return daw, session


def _sample_swap_plan(session):
    from copilot.human_eval.store import now_iso
    from copilot.musicplan import build_sample_swap_action
    from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass

    track = session.tracks[0]
    action = build_sample_swap_action(
        track=track,
        project_identity=session.project_identity,
        clip_index=0,
        sample_uri="samples/hihat_new.wav",
        reason="change hihat",
        evidence_refs=["ev.1"],
    )
    return MusicPlan(
        plan_id="plan_swap",
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


def test_validate_sample_swap_ready():
    from copilot.musicplan import validate_sample_swap_plan
    from copilot.schemas.musicplan import PlanStatus
    _, session = _session_with_audio_clip()
    result = validate_sample_swap_plan(_sample_swap_plan(session), session=session)
    assert result.status is PlanStatus.READY_FOR_EXECUTION
    # previous sample captured from live clip
    assert result.actions[0].params.previous_sample_uri == "samples/hihat_old.wav"


def test_validate_sample_swap_clip_not_found():
    from copilot.musicplan import validate_sample_swap_plan
    from copilot.schemas.musicplan import PlanStatus
    _, session = _session_with_audio_clip()
    plan = _sample_swap_plan(session)
    plan.actions[0].params.clip_index = 99
    result = validate_sample_swap_plan(plan, session=session)
    assert result.status is PlanStatus.REJECTED
    assert "CLIP_NOT_FOUND" in (result.rejection_reason or "")


def test_validate_sample_swap_rejects_midi_clip():
    from copilot.musicplan import validate_sample_swap_plan
    from copilot.schemas.musicplan import PlanStatus
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track("Pad")
    daw.create_midi_clip(0, 0, 4.0)  # MIDI clip (no sample)
    session = daw.snapshot()
    attach_tokens(session)
    plan = _sample_swap_plan(session)
    result = validate_sample_swap_plan(plan, session=session)
    assert result.status is PlanStatus.REJECTED
    assert "CLIP_NOT_AUDIO" in (result.rejection_reason or "")


def test_execute_sample_swap_write_loop(tmp_path):
    from copilot.musicplan.execute import (
        build_agent_tools,
        execute_sample_swap_write_loop,
    )
    daw, session = _session_with_audio_clip()
    plan = _sample_swap_plan(session)
    tools = build_agent_tools(daw, journal_path=tmp_path / "journal.jsonl")
    report = execute_sample_swap_write_loop(
        tools, plan=plan, session=session, persist_dir=tmp_path
    )
    assert report["status"] == "CONTROLLED_WRITE_LOOP_COMPLETE", report
    assert report["EXECUTION_VERIFICATION"] == "PASS"
    assert report["after_sample_uri"] == "samples/hihat_new.wav"
    assert report["restored_sample_uri"] == "samples/hihat_old.wav"
    assert report["MUSICAL_WRITE_COUNT"] == {"forward": 1, "rollback": 1}
    assert report["RESTORE_VERIFIED"] is True
    assert report["open_transaction"] is False
