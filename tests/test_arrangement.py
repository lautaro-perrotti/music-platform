from __future__ import annotations

from pathlib import Path

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens

TRACKS = ["Kick", "Clap", "Closed Hat", "Shaker", "Conga", "Clave",
          "Perc Loop", "Bass", "Vocal", "Stab", "FX"]


def test_arrangement_structure():
    from copilot.musicplan.arrangement import ALL_TRACKS, TECH_HOUSE_ARRANGEMENT
    assert [s.name for s in TECH_HOUSE_ARRANGEMENT] == [
        "INTRO", "GROOVE", "BASS", "DROP", "BREAK", "DROP2", "OUTRO"
    ]
    assert [s.bars for s in TECH_HOUSE_ARRANGEMENT] == [8, 16, 8, 32, 8, 16, 8]
    intro = TECH_HOUSE_ARRANGEMENT[0]
    assert intro.active == ["Conga", "Clave", "Shaker"]  # percussion only
    drop = TECH_HOUSE_ARRANGEMENT[3]
    assert set(drop.active) == set(ALL_TRACKS)  # full groove
    brk = TECH_HOUSE_ARRANGEMENT[4]
    assert "Kick" not in brk.active and "Bass" not in brk.active  # subtract kick+bass


def test_arrangement_mute_actions():
    from copilot.musicplan.arrangement import build_arrangement_mute_actions
    actions = build_arrangement_mute_actions(project_identity="x")
    assert len(actions) == 77  # 7 sections x 11 tracks
    intro = actions[:10]
    by_name = {a.target.ref["name"]: a.params.mute for a in intro}
    assert by_name["Kick"] is True
    assert by_name["Conga"] is False
    assert by_name["Shaker"] is False


def test_execute_arrangement_mute_plan(tmp_path):
    from copilot.human_eval.store import now_iso
    from copilot.musicplan.arrangement import build_arrangement_mute_actions
    from copilot.musicplan.execute import build_agent_tools, execute_track_build_plan
    from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass

    daw = MockAbletonAdapter()
    daw.connect()
    daw.session_path = r"D:\sets\arr_lab.als"
    daw.session_name = "arr_lab"
    for name in TRACKS:
        daw.create_audio_track(name)
    session = daw.snapshot()
    attach_tokens(session)

    actions = build_arrangement_mute_actions(project_identity=session.project_identity)
    plan = MusicPlan(
        plan_id="arr",
        diagnosis=DiagnosisBinding(
            diagnosis_id="d1", diagnosis_status="SUPPORTED",
            diagnosis_accepted=True, cause_status="CAUSE_SUPPORTED",
        ),
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        project_state_token=session.project_token or session.project_identity,
        audible_state_token=session.audible_token or "",
        target_state_tokens={},
        evidence_refs=[],
        actions=actions,
        created_at=now_iso(),
    )
    tools = build_agent_tools(daw, journal_path=tmp_path / "journal.jsonl")
    report = execute_track_build_plan(
        tools, plan=plan, session=session, persist_dir=tmp_path
    )
    assert report["status"] == "CONTROLLED_WRITE_LOOP_COMPLETE", report
    assert report["MUSICAL_WRITE_COUNT"]["forward"] == 77
    assert report["MUSICAL_WRITE_COUNT"]["rollback"] == 1
    assert report["RESTORE_VERIFIED"] is True
