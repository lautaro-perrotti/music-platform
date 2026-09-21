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
        ("impact", SampleRole.IMPACT, SampleType.ONE_SHOT, None, "impact"),
        ("downlifter", SampleRole.DOWNLIFTER, SampleType.LOOP, None, "downlifter"),
        ("texture", SampleRole.TEXTURE, SampleType.LOOP, None, "texture"),
    ]
    for aid, role, stype, bpm, fn in specs:
        assets[aid] = SampleAsset(
            id=f"asset_{aid}", path=f"/samples/{fn}.wav", filename=f"{fn}.wav",
            library_root="/samples", relative_path=f"{fn}.wav", extension=".wav",
            size_bytes=1000, sha256=aid, sample_type=stype, semantic_role=role,
            bpm=BpmEstimate(value=bpm, confidence=0.9 if bpm else None),
        )
    return LibraryIndex(assets=assets)


def _session():
    daw = MockAbletonAdapter()
    daw.connect()
    daw.session_path = r"D:\sets\astra_lab.als"
    daw.session_name = "astra_lab"
    session = daw.snapshot()
    attach_tokens(session)
    return session


class MockProvider:
    def __init__(self, response):
        self.response = response

    def reason(self, prompt, timeout_s=30.0):
        self.prompt = prompt
        return self.response


def test_parse_astra_selection_tolerant():
    from copilot.musicplan.astra_plan import parse_astra_selection
    assert parse_astra_selection('{"selections": {"Kick": 1}}')["selections"]["Kick"] == 1
    # markdown fence
    assert parse_astra_selection('```json\n{"selections": {"Kick": 2}}\n```')["selections"]["Kick"] == 2


def test_build_plan_from_prompt_uses_selections():
    from copilot.musicplan.astra_plan import build_plan_from_prompt
    session = _session()
    provider = MockProvider('{"selections": {"Kick": 1, "Bass": 1, "Conga": 1}, "reasoning": "dark groove"}')
    plan, meta = build_plan_from_prompt(
        index=_synthetic_index(), session=session, intent="dark percussive", provider=provider
    )
    assert meta["astra_used"] is True
    assert meta["sample_map"].get("Kick") == "kick"
    assert meta["sample_map"].get("Bass") == "bass"
    assert len(plan.actions) > 0


def test_build_plan_from_prompt_fallback_on_error():
    from copilot.musicplan.astra_plan import build_plan_from_prompt
    session = _session()
    provider = MockProvider("not json")
    # "not json" -> parse fails -> deterministic fallback
    plan, meta = build_plan_from_prompt(
        index=_synthetic_index(), session=session, intent="dark", provider=provider
    )
    assert meta["astra_used"] is False
    assert len(plan.actions) > 0  # deterministic recipe still produces a plan
