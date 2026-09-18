from __future__ import annotations

from pathlib import Path

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.sample_library.schemas import (
    BpmEstimate,
    LibraryIndex,
    SampleAsset,
    SampleRole,
    SampleType,
)


def _session():
    daw = MockAbletonAdapter()
    daw.connect()
    daw.session_path = r"D:\sets\vibe_lab.als"
    daw.session_name = "vibe_lab"
    session = daw.snapshot()
    attach_tokens(session)
    return daw, session


def _synthetic_index():
    assets = {}
    specs = [
        ("kick", SampleRole.KICK, SampleType.ONE_SHOT, None),
        ("clap", SampleRole.CLAP, SampleType.ONE_SHOT, None),
        ("ch", SampleRole.CLOSED_HAT, SampleType.ONE_SHOT, None),
        ("oh", SampleRole.OPEN_HAT, SampleType.ONE_SHOT, None),
        ("bass", SampleRole.BASS, SampleType.LOOP, 124.0),
    ]
    for aid, role, stype, bpm in specs:
        assets[aid] = SampleAsset(
            id=aid,
            path=f"/samples/{aid}.wav",
            filename=f"{aid}.wav",
            library_root="/samples",
            relative_path=f"{aid}.wav",
            extension=".wav",
            size_bytes=1000,
            sha256=aid,
            sample_type=stype,
            semantic_role=role,
            bpm=BpmEstimate(value=bpm, confidence=0.9 if bpm else None),
        )
    return LibraryIndex(assets=assets)


def test_build_tech_house_plan():
    from copilot.musicplan.tech_house import build_tech_house_plan
    _, session = _session()
    plan = build_tech_house_plan(index=_synthetic_index(), session=session)
    # 5 roles x (CREATE_TRACK + SAMPLE_LOAD)
    assert len(plan.actions) == 10
    kinds = [a.action_type.value for a in plan.actions]
    assert kinds.count("CREATE_TRACK") == 5
    assert kinds.count("SAMPLE_LOAD") == 5
    names = [a.params.track_name for a in plan.actions if a.action_type.value == "CREATE_TRACK"]
    assert names == ["Kick", "Clap", "Closed Hat", "Open Hat", "Bass"]


def test_execute_tech_house_plan_full_loop(tmp_path):
    from copilot.musicplan.execute import (
        build_agent_tools,
        execute_track_build_plan,
    )
    from copilot.musicplan.tech_house import build_tech_house_plan

    daw, session = _session()
    plan = build_tech_house_plan(index=_synthetic_index(), session=session)
    tools = build_agent_tools(daw, journal_path=tmp_path / "journal.jsonl")
    report = execute_track_build_plan(
        tools, plan=plan, session=session, persist_dir=tmp_path
    )

    assert report["status"] == "CONTROLLED_WRITE_LOOP_COMPLETE", report
    assert report["EXECUTED"] is True
    assert report["MUSICAL_WRITE_COUNT"]["forward"] == 10
    assert report["after_track_count"] == 5
    assert report["after_clip_count"] == 5
    # rollback reversed the whole plan -> empty set restored
    assert report["restored_track_count"] == 0
    assert report["restored_clip_count"] == 0
    assert report["RESTORE_VERIFIED"] is True
    assert report["open_transaction"] is False
