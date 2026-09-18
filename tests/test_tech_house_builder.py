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
        ("kick", SampleRole.KICK, SampleType.ONE_SHOT, None, "kick"),
        ("clap", SampleRole.CLAP, SampleType.ONE_SHOT, None, "clap"),
        ("ch", SampleRole.CLOSED_HAT, SampleType.ONE_SHOT, None, "ch"),
        ("shaker", SampleRole.SHAKER, SampleType.ONE_SHOT, None, "shaker"),
        ("conga", SampleRole.PERCUSSION, SampleType.ONE_SHOT, None, "conga"),
        ("clave", SampleRole.PERCUSSION, SampleType.ONE_SHOT, None, "clave"),
        ("perc_loop", SampleRole.TOP_LOOP, SampleType.LOOP, 127.0, "perc"),
        ("bass", SampleRole.BASS, SampleType.LOOP, 127.0, "bass"),
        ("vocal", SampleRole.VOCAL, SampleType.ONE_SHOT, None, "vocal"),
        ("stab", SampleRole.SYNTH, SampleType.ONE_SHOT, None, "stab"),
        ("fx", SampleRole.FX, SampleType.ONE_SHOT, None, "fx"),
    ]
    for aid, role, stype, bpm, fn in specs:
        assets[aid] = SampleAsset(
            id=aid,
            path=f"/samples/{fn}.wav",
            filename=f"{fn}.wav",
            library_root="/samples",
            relative_path=f"{fn}.wav",
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
    # 11 roles x (CREATE_TRACK + SAMPLE_LOAD) + mixing + groups (routing + bus)
    assert len(plan.actions) == 73
    kinds = [a.action_type.value for a in plan.actions]
    assert kinds.count("CREATE_TRACK") == 16
    assert kinds.count("SAMPLE_LOAD") == 11
    assert kinds.count("DEVICE_LOAD") == 35
    assert kinds.count("SET_TRACK_ROUTING") == 11
    names = [a.params.track_name for a in plan.actions if a.action_type.value == "CREATE_TRACK"]
    assert names == ["Kick", "Clap", "Closed Hat", "Shaker", "Conga", "Clave",
                     "Perc Loop", "Bass", "Vocal", "Stab", "FX",
                     "DRUMS", "BASS BUS", "SYNTHS", "FX BUS", "VOCALS"]
    # Bass gets the sidechain/saturation chain: EQ Eight + Compressor + Saturator
    bass_devices = [a.params.device_name for a in plan.actions
                    if a.action_type.value == "DEVICE_LOAD" and a.target.ref.get("name") == "Bass"]
    assert bass_devices == ["EQ Eight", "Compressor", "Saturator"]


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
    assert report["MUSICAL_WRITE_COUNT"]["forward"] == 73
    assert report["after_track_count"] == 16
    assert report["after_clip_count"] == 11
    # rollback reversed the whole plan -> empty set restored
    assert report["restored_track_count"] == 0
    assert report["restored_clip_count"] == 0
    assert report["RESTORE_VERIFIED"] is True
    assert report["open_transaction"] is False


def test_groove_patterns():
    from copilot.musicplan.groove import (
        DRUM_MAP,
        bass_pattern,
        clap_pattern,
        clave_pattern,
        closed_hat_pattern,
        conga_pattern,
        full_groove,
        kick_pattern,
        shaker_pattern,
    )
    kick = kick_pattern()
    assert [n.start_time for n in kick] == [0.0, 1.0, 2.0, 3.0]
    assert [n.velocity for n in kick] == [100, 98, 102, 96]  # velocity variation = groove
    clap = clap_pattern()
    assert [n.start_time for n in clap if n.velocity > 60] == [1.0, 3.0]
    assert len(closed_hat_pattern()) == 6  # 4 offbeats + 2 ghosts
    assert len(shaker_pattern()) == 16  # 16ths
    assert len(conca_pattern := conga_pattern()) == 6  # syncopated latin
    assert [n.start_time for n in clave_pattern()] == [0.0, 0.75, 1.5, 2.5, 3.0]  # son 3-2
    assert len(bass_pattern()) == 5  # funky syncopated
    full = full_groove()
    assert len(full) == 45  # kick4 + clap3 + hat6 + shaker16 + conga6 + clave5 + bass5
    # every note has a mapped pitch
    assert all(n.pitch in DRUM_MAP.values() for n in full)
    # sorted by time
    times = [n.start_time for n in full]
    assert times == sorted(times)
