"""Contract tests only; the final candidate must come from a real Lucas call."""

import copy

import pytest
from pydantic import ValidationError

from copilot.producer.producer_plan_v1 import (
    INTENT, ProducerPlanV1, degree_to_midi, render_producer_plan_v1,
)
from copilot.producer.tech_house_kit_v2 import groove_plan as v2_plan


def _hit(offset: float, degree: str | None = None) -> dict:
    row = {"offset": offset, "duration": .2, "dynamic": "normal", "timing": "on_grid"}
    if degree:
        row["degree"] = degree
    return row


def plan_data() -> dict:
    return {
        "intent": INTENT, "tempo": 126, "bars": 16, "meter": "4/4",
        "tonality": {"root": "G", "mode": "minor", "bass_root_midi": 43, "reason": "Low root."},
        "character": {"darkness": 4, "groove": 4, "density": 2, "repetition": 4, "tension": 3},
        "swing": {"amount_beats": .02, "roles": ["closed_hat", "perc"]},
        "sections": [
            {"start_bar": 1, "end_bar": 8, "energy": .5,
             "active_roles": ["kick", "clap", "closed_hat", "open_hat", "perc", "bass"],
             "variation_notes": "Hold space."},
            {"start_bar": 9, "end_bar": 16, "energy": .7,
             "active_roles": ["kick", "clap", "closed_hat", "open_hat", "perc", "bass", "stab"],
             "variation_notes": "Add punctuation."},
        ],
        "roles": {
            "closed_hat": {"motif": [_hit(.25), _hit(.75)]},
            "open_hat": {"motif": [_hit(.5)]},
            "perc": {"motif": [_hit(2.25)]},
            "bass": {"motif": [_hit(.5, "1"), _hit(1.5, "5")],
                     "additions": [{"bar": 8, "event": _hit(3.25, "b3")}],
                     "concept": "Sparse root and fifth."},
            "stab": {"motif": [{**_hit(1.5), "degrees": ["b3", "5"], "register_octave": 1}],
                     "concept": "Short punctuation."},
        },
        "rationale": {"bass": "Sparse pulse.", "groove": "Delay hats.",
                      "sections": "Add stabs later.", "stab": "Leave gaps."},
    }


def test_schema_and_section_coverage() -> None:
    plan = ProducerPlanV1.model_validate(plan_data())
    assert plan.sections[-1].end_bar == 16
    broken = plan_data()
    broken["sections"][1]["start_bar"] = 10
    with pytest.raises(ValidationError, match="contiguously"):
        ProducerPlanV1.model_validate(broken)
    broken = plan_data()
    broken["roles"]["bass"]["motif"] = [_hit(.5, "1")]
    with pytest.raises(ValidationError):
        ProducerPlanV1.model_validate(broken)


def test_degree_to_midi_and_deterministic_render() -> None:
    assert degree_to_midi(43, "b3") == 46
    assert degree_to_midi(43, "5", 1) == 62
    assert degree_to_midi(31, "1") == 31  # Valid G sub register.
    plan = ProducerPlanV1.model_validate(plan_data())
    first = render_producer_plan_v1(plan)
    assert first == render_producer_plan_v1(plan)
    assert len(first["kick"]) == 64 and len(first["clap"]) == 32
    assert all(n.start_time % 1 == 0 for n in first["kick"])
    assert min(n.start_time for n in first["stab"]) >= 32
    assert all(0 <= n.start_time and n.start_time + n.duration <= 64 for role in first.values() for n in role)
    assert [n.start_time for n in first["bass"]] != [n["start_time"] for n in v2_plan()["roles"]["Bass"]["notes"]]


def test_lucas_can_choose_a_bounded_lower_root_octave() -> None:
    data = plan_data()
    data["tonality"]["bass_root_midi"] = 31
    assert ProducerPlanV1.model_validate(data).tonality.bass_root_midi == 31
    data["tonality"]["bass_root_midi"] = 32
    with pytest.raises(ValidationError, match="must match chosen root"):
        ProducerPlanV1.model_validate(data)


def test_microtiming_velocity_and_density_bounds() -> None:
    data = plan_data()
    data["roles"]["closed_hat"]["motif"][0]["timing"] = "late"
    plan = ProducerPlanV1.model_validate(data)
    rendered = render_producer_plan_v1(plan)
    assert rendered["closed_hat"][0].start_time == .282  # .25 + .02 swing + .012 late
    assert all(1 <= n.velocity <= 127 for notes in rendered.values() for n in notes)
    invalid = copy.deepcopy(data)
    invalid["roles"]["bass"]["additions"] = [
        {"bar": 8, "event": _hit(offset, "1")} for offset in (.1, .2, .3, .4, .6)
    ]
    with pytest.raises(ValueError, match="bass density"):
        render_producer_plan_v1(ProducerPlanV1.model_validate(invalid))
    invalid = copy.deepcopy(data)
    invalid["swing"]["amount_beats"] = .045
    with pytest.raises(ValueError, match="microtiming out of bounds"):
        render_producer_plan_v1(ProducerPlanV1.model_validate(invalid))


def test_stab_density_and_forbid_live_commands() -> None:
    invalid = plan_data()
    invalid["roles"]["stab"]["additions"] = [
        {"bar": 9, "event": {**_hit(2.0), "degrees": ["1"]}},
        {"bar": 9, "event": {**_hit(3.0), "degrees": ["5"]}},
    ]
    with pytest.raises(ValueError, match="stab density"):
        render_producer_plan_v1(ProducerPlanV1.model_validate(invalid))
    invalid = plan_data()
    invalid["create_track"] = "unsafe"
    with pytest.raises(ValidationError):
        ProducerPlanV1.model_validate(invalid)
