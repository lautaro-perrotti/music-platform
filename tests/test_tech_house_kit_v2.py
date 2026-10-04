"""Focused deterministic musical constraints; real Live is a separate gate."""

from copilot.integration.tech_house_kit_direct_v2 import _validate_batch, make_batch
from copilot.producer import tech_house_kit_v1 as v1, tech_house_kit_v2 as v2


def _notes(plan: dict, role: str) -> list[dict]:
    return plan["roles"][role]["notes"]


def test_v2_plan_is_deterministic_and_uses_the_chosen_bass_preset() -> None:
    first = v2.groove_plan()
    assert first == v2.groove_plan()
    assert first["tempo_bpm"] == 126 and first["meter"] == [4, 4] and first["bars"] == 16
    assert len(first["roles"]) == 7
    assert next(s for s in v2.KIT if s.role == "Bass").browser_name == "House Bass.adv"
    batch = make_batch(first)
    _validate_batch(batch)
    assert batch.length_beats == 64 and batch.tracks[5].fallback_source_name is None
    assert all(0 <= n["start_time"] < 64 and n["start_time"] + n["duration"] <= 64
               for role in first["roles"] for n in _notes(first, role))


def test_bass_is_short_rhythmic_and_g1_dominated() -> None:
    plan = v2.groove_plan()
    bass = _notes(plan, "Bass")
    kicks = {n["start_time"] for n in _notes(plan, "Kick")}
    assert set(n["pitch"] for n in bass) <= {43, 46, 50, 41}
    assert sum(n["pitch"] == 43 for n in bass) / len(bass) > .9
    assert all(n["duration"] <= .28 and n["start_time"] not in kicks for n in bass)
    assert len({n["velocity"] for n in bass}) > 3


def test_only_hats_and_percussion_receive_deterministic_microtiming() -> None:
    plan = v2.groove_plan()
    assert all(n["start_time"].is_integer() for n in _notes(plan, "Kick"))
    assert all(n["start_time"] % 1 in (0, 0.75) for n in _notes(plan, "Clap"))
    closed = _notes(plan, "Closed Hat")
    open_ = _notes(plan, "Open Hat")
    perc = _notes(plan, "Perc")
    assert any(abs(n["start_time"] % .25) > .005 for n in closed)
    assert any(abs(n["start_time"] % .5) > .005 for n in open_)
    assert any(abs(n["start_time"] % .25) > .005 for n in perc)
    assert len({n["velocity"] for n in closed}) > 3
    assert len(closed) < len(_notes(v1.groove_plan(), "Closed Hat"))
    assert not any(56 <= n["start_time"] < 60 for n in open_)


def test_stab_punctuation_starts_at_bar_nine_and_differs_from_v1() -> None:
    current = v2.groove_plan()
    old = v1.groove_plan()
    stabs = _notes(current, "Stab")
    assert min(n["start_time"] for n in stabs) >= 32
    assert max(n["velocity"] for n in stabs) <= 88
    assert len(stabs) < len(_notes(old, "Stab"))
    for role in ("Bass", "Closed Hat", "Open Hat", "Perc", "Stab"):
        assert _notes(current, role) != _notes(old, role)
    for role in ("Kick", "Clap"):
        assert _notes(current, role) == _notes(old, role)
