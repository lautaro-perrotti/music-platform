from __future__ import annotations

import pytest

from copilot.musicplan.arrangement_engine import (
    EnergyTrend,
    build_arrangement_engine_plan,
)
from copilot.producer.track_spec import SectionSpec


def test_arrangement_engine_derives_timeline_contrast_and_groove_focus():
    plan = build_arrangement_engine_plan(
        [
            SectionSpec(name="OPEN", bars=8, energy=0.2, active_roles=["Shaker"]),
            SectionSpec(
                name="RISE",
                bars=8,
                energy=0.55,
                active_roles=["Kick", "Closed Hat", "Bass"],
            ),
            SectionSpec(
                name="DROP",
                bars=16,
                energy=0.9,
                active_roles=["Kick", "Closed Hat", "Bass", "Clap"],
            ),
            SectionSpec(name="EXIT", bars=8, energy=0.2, active_roles=["Shaker"]),
        ],
        known_roles={"Kick", "Closed Hat", "Bass", "Clap", "Shaker"},
    )

    assert plan.total_bars == 40
    assert [(row.start_bar, row.end_bar) for row in plan.decisions] == [
        (0, 8),
        (8, 16),
        (16, 32),
        (32, 40),
    ]
    assert plan.decisions[0].trend == EnergyTrend.OPEN
    assert plan.decisions[1].trend == EnergyTrend.RISING
    assert plan.decisions[2].trend == EnergyTrend.PEAK
    assert plan.decisions[3].trend == EnergyTrend.RESET
    assert plan.decisions[1].added_roles == ["Bass", "Closed Hat", "Kick"]
    assert plan.decisions[3].removed_roles == ["Bass", "Clap", "Closed Hat", "Kick"]
    assert plan.decisions[2].groove_focus == ["Kick", "Closed Hat", "Bass", "Clap"]
    assert plan.rules_applied[-1] == "no-daw-writes"


def test_arrangement_engine_rejects_unknown_roles_and_duplicate_sections():
    with pytest.raises(ValueError, match="UNKNOWN_ROLES"):
        build_arrangement_engine_plan(
            [{"name": "A", "bars": 4, "energy": 0.2, "active": ["Imaginary"]}],
            known_roles={"Kick"},
        )

    with pytest.raises(ValueError, match="DUPLICATE"):
        build_arrangement_engine_plan(
            [
                {"name": "A", "bars": 4, "energy": 0.2, "active": ["Kick"]},
                {"name": "a", "bars": 4, "energy": 0.4, "active": ["Kick"]},
            ]
        )


def test_arrangement_engine_accepts_legacy_sections_without_inventing_audio_facts():
    plan = build_arrangement_engine_plan(
        [{"name": "LEGACY", "bars": 4, "active": ["Kick"]}],
        known_roles={"Kick"},
    )
    assert plan.decisions[0].energy == 0.5
    assert plan.decisions[0].energy_delta == 0.0
    assert plan.decisions[0].trend == EnergyTrend.OPEN
