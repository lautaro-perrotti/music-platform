import json

import pytest

from copilot.integration.autonomous_producer_alpha_v1 import (
    LucasReasoningOutputAdapter,
    RealLucasRequired,
)


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
