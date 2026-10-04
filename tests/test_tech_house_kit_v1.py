"""TECH_HOUSE_PRODUCTION_KIT_V1 contract. Technical only — not a taste judgment."""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot.producer import tech_house_kit_v1 as kit

ROLES = ("Kick", "Clap", "Closed Hat", "Open Hat", "Perc", "Bass", "Stab")


def test_kit_has_exactly_the_seven_roles_with_provenance() -> None:
    assert tuple(item.role for item in kit.KIT) == ROLES
    for item in kit.KIT:
        assert item.provenance.startswith("Ableton Live Core Library")
        assert item.relative_path and item.browser_name and item.ableton_device
        assert item.track_name == f"KIT V1 - {item.role}"


def test_first_candidates_are_the_approved_sounds() -> None:
    expected = {
        "Kick": "Kick 909 1.aif",
        "Clap": "Clap 909.aif",
        "Closed Hat": "Hihat Closed 909.aif",
        "Open Hat": "Hihat Open 909.aif",
        "Perc": "Shaker Short.aif",
        "Bass": "Basic FM House Bass.adg",
        "Stab": "House Stab.adg",
    }
    assert {item.role: item.browser_name for item in kit.KIT} == expected


def test_no_forbidden_or_placeholder_source() -> None:
    for item in kit.KIT:
        for text in (item.relative_path, item.browser_name, item.fallback_relative_path, item.fallback_browser_name):
            lowered = text.lower()
            assert not any(marker in lowered for marker in kit.FORBIDDEN_SOURCE_MARKERS), text


def test_missing_sound_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="KIT_SOUND_MISSING"):
        kit.kit_definition(tmp_path)


def test_groove_plan_is_16_bars_at_126_and_deterministic() -> None:
    first, second = kit.groove_plan(), kit.groove_plan()
    assert first == second
    assert first["tempo_bpm"] == 126.0 and first["meter"] == [4, 4] and first["bars"] == 16
    assert set(first["roles"]) == set(ROLES)
    for role, row in first["roles"].items():
        assert row["length_beats"] == 64.0
        assert row["note_count"] == len(row["notes"]) > 0
        for note in row["notes"]:
            assert 0 <= note["start_time"] and note["start_time"] + note["duration"] <= 64.0 + 1e-9, role


def test_one_shots_trigger_at_c3() -> None:
    plan = kit.groove_plan()
    for item in kit.KIT:
        if item.is_one_shot:
            assert {note["pitch"] for note in plan["roles"][item.role]["notes"]} == {60}, item.role


def test_kick_is_four_on_the_floor_and_bass_never_lands_on_it() -> None:
    plan = kit.groove_plan()
    kicks = {note["start_time"] for note in plan["roles"]["Kick"]["notes"]}
    assert kicks == {float(beat) for beat in range(64)}
    bass = [note["start_time"] for note in plan["roles"]["Bass"]["notes"]]
    assert bass and not any(onset in kicks for onset in bass)
    assert all(note["duration"] <= 0.25 for note in plan["roles"]["Bass"]["notes"])


def test_clap_on_two_and_four() -> None:
    claps = [note for note in kit.groove_plan()["roles"]["Clap"]["notes"] if note["velocity"] > 100]
    assert {note["start_time"] % 4 for note in claps} == {1.0, 3.0}
    assert len(claps) == 32


def test_stab_enters_at_bar_9_and_open_hat_drops_in_bar_15() -> None:
    plan = kit.groove_plan()
    assert min(note["start_time"] for note in plan["roles"]["Stab"]["notes"]) >= 32.0
    open_hat = [note["start_time"] for note in plan["roles"]["Open Hat"]["notes"]]
    assert not any(56.0 <= onset < 60.0 for onset in open_hat)


def test_percussion_varies_between_sections() -> None:
    perc = kit.groove_plan()["roles"]["Perc"]["notes"]
    per_bar = [sum(1 for note in perc if bar * 4 <= note["start_time"] < bar * 4 + 4) for bar in range(16)]
    assert per_bar[0] != per_bar[4], "bars 5-8 must vary the percussion"
    assert per_bar[15] > per_bar[0], "bar 16 carries the fill"


def test_sidechain_is_reported_blocked_not_emulated() -> None:
    plan = kit.groove_plan()
    assert plan["sidechain"] == "BLOCKED_BY_CONTROL_SURFACE"
