"""No provider or Live calls in these focused contract tests."""

import json

import pytest

from copilot.integration import lucas_producer_plan_v1 as planning
from copilot.integration.lucas_producer_plan_live_v1 import make_batch
from copilot.producer.producer_plan_v1 import ProducerPlanV1, render_producer_plan_v1
from copilot.producer.tech_house_kit_v2 import groove_plan as v2_plan
from copilot.schemas.session import MidiNote
from tests.test_producer_plan_v1 import plan_data


def test_renderer_maps_lucas_plan_to_fixed_sonic_palette() -> None:
    plan = ProducerPlanV1.model_validate(plan_data())
    rendered = render_producer_plan_v1(plan)
    batch = make_batch(rendered)
    assert batch.tempo == 126 and batch.length_beats == 64
    assert [track.name for track in batch.tracks] == [
        f"KIT LUCAS V1 - {role}" for role in
        ("Kick", "Clap", "Closed Hat", "Open Hat", "Perc", "Bass", "Stab")
    ]
    assert batch.tracks[5].source_name == "House Bass.adv"
    assert all(track.fallback_source_name is None for track in batch.tracks)


def test_novelty_compares_onsets_not_fixtures_as_certification(tmp_path, monkeypatch) -> None:
    candidate = tmp_path / "audio" / "b.wav"
    (tmp_path / "plan").mkdir()
    (tmp_path / "plan" / "groove_plan.json").write_text(json.dumps(v2_plan()), encoding="utf-8")
    monkeypatch.setattr(planning, "B_WAV", candidate)
    original = v2_plan()["roles"]
    same = {new: [MidiNote(**note) for note in original[old]["notes"]]
            for new, old in (("bass", "Bass"), ("closed_hat", "Closed Hat"), ("stab", "Stab"))}
    with pytest.raises(RuntimeError, match="NOT_NOVEL"):
        planning.novelty_report(same)
    changed = {key: list(value) for key, value in same.items()}
    changed["bass"] = [MidiNote(**{**same["bass"][0].model_dump(), "start_time": .61})] + same["bass"][1:]
    result = planning.novelty_report(changed)
    assert result["sequence_different"] == {"bass": True, "closed_hat": False, "stab": False}
