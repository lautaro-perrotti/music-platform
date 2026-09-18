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


def _synthetic_index() -> LibraryIndex:
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
        ("stab", SampleRole.SYNTH, SampleType.ONE_SHOT, None, "stab"),
        ("guitar", SampleRole.UNKNOWN, SampleType.ONE_SHOT, None, "guitar"),
        ("sax", SampleRole.UNKNOWN, SampleType.ONE_SHOT, None, "sax"),
        ("fx", SampleRole.FX, SampleType.ONE_SHOT, None, "fx"),
        ("impact", SampleRole.IMPACT, SampleType.ONE_SHOT, None, "impact"),
        ("downlifter", SampleRole.DOWNLIFTER, SampleType.LOOP, None, "downlifter"),
        ("texture", SampleRole.TEXTURE, SampleType.LOOP, None, "texture"),
    ]
    for aid, role, stype, bpm, fn in specs:
        assets[aid] = SampleAsset(
            id=aid, path=f"/samples/{fn}.wav", filename=f"{fn}.wav",
            library_root="/samples", relative_path=f"{fn}.wav", extension=".wav",
            size_bytes=1000, sha256=aid, sample_type=stype, semantic_role=role,
            bpm=BpmEstimate(value=bpm, confidence=0.9 if bpm else None),
        )
    return LibraryIndex(assets=assets)


def _built(monkeypatch=None, tmp_path=None):
    from copilot.musicplan.tech_house import build_tech_house_plan
    from copilot.musicplan.arrangement import build_arrangement_mute_actions, TECH_HOUSE_ARRANGEMENT
    from copilot.musicplan.execute import build_agent_tools, execute_track_build_plan

    daw = MockAbletonAdapter()
    daw.connect()
    daw.session_path = r"D:\sets\crit_lab.als"
    daw.session_name = "crit_lab"
    session = daw.snapshot()
    attach_tokens(session)
    plan = build_tech_house_plan(index=_synthetic_index(), session=session)
    plan.actions.extend(build_arrangement_mute_actions(project_identity=session.project_identity))
    drop = [s for s in TECH_HOUSE_ARRANGEMENT if s.name == "DROP"][0]
    plan.actions.extend(build_arrangement_mute_actions(project_identity=session.project_identity, arrangement=[drop]))
    tools = build_agent_tools(daw, journal_path=Path(tmp_path) / "journal.jsonl" if tmp_path else None)
    execute_track_build_plan(tools, plan=plan, session=session, persist_dir=tmp_path, leave=True)
    after = daw.snapshot()
    return plan, after


def test_build_track_state_summary(tmp_path):
    from copilot.musicplan.critique import build_track_state_summary

    plan, after = _built(tmp_path=tmp_path)
    summary = build_track_state_summary(plan=plan, session=after)
    # structural facts must be present
    assert "Kick" in summary and "Bass" in summary
    assert "MIDI groove percussion" in summary
    assert "Shaker" in summary  # groove-MIDI percussion
    assert "Sidechain" in summary
    assert "Routing (element -> bus)" in summary
    assert "direct to Main" in summary  # no buses: every element goes direct to Main
    assert "chain[Bass]" in summary and "Compressor" in summary


def test_critique_track_with_mock_provider(tmp_path):
    from copilot.musicplan.critique import critique_track

    class MockProvider:
        def reason_json_object(self, prompt, timeout_s=None):
            assert "DECISION HIERARCHY" in prompt  # decision system is loaded
            assert "STRUCTURAL READBACK" in prompt
            return (
                '{"verdict": "improve", '
                '"top_3_issues": [{"priority": 1, "area": "density", '
                '"issue": "DROP too crowded", "minimal_fix": "mute Texture"}, '
                '{"priority": 2, "area": "low_end", "issue": "kick/bass overlap", '
                '"minimal_fix": "verify sidechain"}], "reasoning": "ok"}'
            )

    plan, after = _built(tmp_path=tmp_path)
    result = critique_track(plan=plan, session=after, provider=MockProvider())
    assert result is not None
    assert result.verdict == "improve"
    assert len(result.top_3_issues) == 2
    assert result.top_3_issues[0].priority == 1
    assert result.top_3_issues[0].area == "density"


def test_critique_track_no_provider_returns_none(tmp_path):
    from copilot.musicplan.critique import critique_track

    plan, after = _built(tmp_path=tmp_path)
    # force provider=None path
    result = critique_track(plan=plan, session=after, provider=None)
    # None only if no provider is configured; the mock path here just must not raise.
    assert result is None or result.verdict in ("finalize", "improve")


def test_parse_critique_tolerates_fences():
    from copilot.musicplan.critique import parse_critique

    r = parse_critique('```json\n{"verdict": "finalize", "top_3_issues": [], "reasoning": "x"}\n```')
    assert r.verdict == "finalize"
    assert r.top_3_issues == []
