import json
from types import SimpleNamespace

import pytest

from copilot.integration.autonomous_producer_alpha_v1 import (
    LucasPlanningProviderAdapter,
    LucasReasoningOutputAdapter,
    RealLucasRequired,
    run_alpha,
    _uncertified_phrase_variations,
)
from copilot.daw.mock import MockAbletonAdapter
from copilot.producer.goal import ProducerGoal
from copilot.producer.track_spec import TrackSpec
from copilot.schemas.musicplan import ProductionActionKind


def test_reasoning_candidate_strategies_are_unwrapped_without_core_choices():
    raw = json.dumps({
        "candidate_strategies": [
            {"strategy": "selections", "reason": '{"Kick": 2}'},
            {"strategy": "arrangement", "reason": '[{"name":"INTRO","bars":8,"active":["Kick"]}]'},
            {"strategy": "reasoning", "reason": "kick first"},
        ]
    })
    output = json.loads(LucasReasoningOutputAdapter.unwrap(raw))
    assert output == {
        "selections": {"Kick": 2},
        "arrangement": [{"name": "INTRO", "bars": 8, "active": ["Kick"]}],
        "reasoning": "kick first",
    }


def test_producer_provider_schema_exposes_typed_goal_and_mix_decisions():
    schema = LucasPlanningProviderAdapter._schema()
    assert {"track_spec", "patterns", "mix_decisions", "selection_reasons", "producer_criteria"} <= set(
        schema["properties"]
    )
    for decision in schema["properties"]["mix_decisions"]["properties"].values():
        assert "producer_criteria" not in decision["properties"]["action"]["properties"]
    note = schema["properties"]["patterns"]["additionalProperties"]["properties"]["notes"]["items"]
    assert "mix_decisions" not in note["properties"]


def test_goal_capture_rejects_identity_failure_before_capture(tmp_path, monkeypatch):
    from copilot.audio import session_diagnose
    from copilot.audio import source_capture_pool_v1
    from copilot.integration.autonomous_producer_alpha_v1 import _capture_goal_sources

    capture_calls = []

    class Session:
        def track_by_name(self, _name):
            raise AssertionError("track lookup must not follow failed preflight")

    monkeypatch.setattr(
        session_diagnose,
        "preflight_session",
        lambda _daw: {"pass": False, "missing": ["PROJECT_IDENTITY_MISSING"]},
    )
    monkeypatch.setattr(
        source_capture_pool_v1,
        "capture_source_post_mixer_ref",
        lambda *args, **kwargs: capture_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="GENERIC_CAPTURE_PREFLIGHT_FAILED.*PROJECT_IDENTITY_MISSING"):
        _capture_goal_sources(
            daw=object(), session=Session(),
            spec=SimpleNamespace(hook_role="Stab", sections=[]),
            evidence=tmp_path,
        )

    assert capture_calls == []


def test_direct_lucas_contract_is_preserved():
    raw = '{"selections":{"Kick":1},"arrangement":[]}'
    assert LucasReasoningOutputAdapter.unwrap(raw) == raw


def test_missing_producer_strategy_fails_closed():
    with pytest.raises(RealLucasRequired):
        LucasReasoningOutputAdapter.unwrap('{"status":"INSUFFICIENT_EVIDENCE"}')


def test_natural_language_candidate_strategies_are_mechanically_unwrapped():
    raw = json.dumps({
        "candidate_strategies": [
            {
                "strategy": "Select Clap candidate 1, Stab candidate 1, and FX candidate 2.",
                "reason": "one hook",
            },
            {
                "strategy": "INTRO: 8 bars, Kick + Clap. DROP: 8 bars, Kick + Clap + Stab.",
                "reason": "subtraction",
            },
        ]
    })
    output = json.loads(LucasReasoningOutputAdapter.unwrap(raw))
    assert output["selections"] == {"Clap": 1, "Stab": 1, "FX": 2}
    assert output["arrangement"] == [
        {"name": "INTRO", "bars": 8, "active": ["Kick", "Clap"]},
        {"name": "DROP", "bars": 8, "active": ["Kick", "Clap", "Stab"]},
    ]


def test_section_strategy_sentences_are_unwrapped():
    raw = json.dumps({
        "candidate_strategies": [
            {
                "strategy": "Section 1 - Percussive intro: 8 bars, active Kick and Clap.",
                "reason": "foundation",
            },
            {
                "strategy": "Section 2 - Hook tease: 4 bars, active Kick, Clap and Stab.",
                "reason": "contrast",
            },
        ]
    })
    output = json.loads(LucasReasoningOutputAdapter.unwrap(raw))
    assert output["arrangement"][0]["active"] == ["Kick", "Clap"]
    assert output["arrangement"][1]["active"] == ["Kick", "Clap", "Stab"]


def test_goal_blocks_wrong_template_tempo_before_musical_write(tmp_path, monkeypatch):
    from copilot.integration import autonomous_producer_alpha_v1 as alpha

    class SnapshotAdapter(MockAbletonAdapter):
        def snapshot(self, **kwargs):
            return super().snapshot()

    daw = SnapshotAdapter()
    daw.session_path = str(tmp_path / "copy.als")
    daw.connect()
    from copilot.daw.state_tokens import attach_tokens
    identity = attach_tokens(daw.snapshot()).project_identity
    daw.disconnect()
    opened = {
        "status": "OPENED_EMPTY", "project_identity": identity,
        "working_als": daw.session_path,
        "readiness": {"launch": {"launch": "started", "process_lifecycle": {"owned": True}}},
    }
    monkeypatch.setattr(alpha, "AbletonTcpAdapter", lambda: daw)
    monkeypatch.setattr(alpha, "is_copilot_working_copy", lambda path: True)
    goal = ProducerGoal.from_prompt("BPM: 127\nDuración: 8 compases")
    report = run_alpha(
        evidence=tmp_path / "logs", goal=goal,
        expected_project_path=tmp_path / "copy.als",
        opened_project=opened,
    )
    assert report["status"] == "BLOCKED"
    assert report["MUSICAL_WRITES"] == 0
    assert "TEMPLATE_TEMPO_MISMATCH" in report["blockers"][0]


def test_promised_midi_phrase_variation_is_deferred_not_counted_as_verified():
    spec = TrackSpec(
        bpm=127, primary_hook="shaker rhythm", hook_role="Shaker",
        sections=[
            {"name": "Intro", "bars": 4, "energy": .4,
             "active_roles": ["Kick", "Shaker"]},
            {"name": "Drop", "bars": 4, "energy": .8,
             "active_roles": ["Kick", "Shaker"],
             "variation": "Alter shaker accents on the last bar"},
        ],
    )
    plan = SimpleNamespace(actions=[
        SimpleNamespace(
            action_type=ProductionActionKind.CREATE_PATTERN,
            target=SimpleNamespace(ref={"name": "Shaker"}),
        ),
    ])
    rows = _uncertified_phrase_variations(spec, plan)
    assert len(rows) == 1
    assert rows[0]["section"] == "Drop"
    assert rows[0]["role"] == "Shaker"
    assert rows[0]["status"] == "EXECUTION_DEFERRED"
    assert rows[0]["reason"] == "PHRASE_MIDI_VARIATION_NOT_CERTIFIED"
    spec.sections[1].variation = ""
    assert _uncertified_phrase_variations(spec, plan) == []
