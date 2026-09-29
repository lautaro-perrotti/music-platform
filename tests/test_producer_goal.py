import pytest

from copilot.producer.goal import ProducerGoal
from copilot.producer.track_spec import TrackSpec


@pytest.mark.parametrize(
    ("prompt", "bars"),
    [
        ("BPM: 120\nDuración: 2:00\nGroove oscuro", 60),
        ("BPM: 127\nDuración: 64 compases", 64),
        ("BPM: 120\nDuration: 120 seconds", 60),
        ("BPM: 120\nDuración: 2 minutos", 60),
        ("BPM: 120\nDuración: 2 minutos 32 segundos", 76),
    ],
)
def test_goal_parses_quantized_length(prompt, bars):
    goal = ProducerGoal.from_prompt(prompt)
    assert goal.duration_bars == bars
    assert goal.quantization_seconds == pytest.approx(120 / goal.bpm)


@pytest.mark.parametrize(
    "prompt",
    [
        "BPM: 127\nA dark track",
        "Duración: 64 compases\nA dark track",
        "BPM: 127\nBPM: 128\nDuración: 64 compases",
        "BPM: 127\nDuración: two minutes",
        "BPM: 127\nDuración: 0 compases",
    ],
)
def test_goal_rejects_missing_or_ambiguous_requirements(prompt):
    with pytest.raises(ValueError):
        ProducerGoal.from_prompt(prompt)


def test_goal_refuses_plan_with_wrong_tempo_or_length():
    goal = ProducerGoal.from_prompt("BPM: 127\nDuración: 64 compases")
    spec = TrackSpec(
        bpm=126, primary_hook="kick rhythm", hook_role="Kick", sections=[{"name": "Groove", "bars": 64, "energy": .6, "active_roles": ["Kick"]}]
    )
    with pytest.raises(ValueError, match="BPM_MISMATCH"):
        goal.validate_track_spec(spec)
    spec.bpm = 127
    spec = TrackSpec(
        bpm=127, primary_hook="kick rhythm", hook_role="Kick", sections=[{"name": "Groove", "bars": 32, "energy": .6, "active_roles": ["Kick"]}]
    )
    with pytest.raises(ValueError, match="DURATION_MISMATCH"):
        goal.validate_track_spec(spec)


def test_goal_requires_explicit_hook():
    goal = ProducerGoal.from_prompt("BPM: 127\nDuración: 8 compases")
    spec = TrackSpec(
        bpm=127, sections=[{"name": "Groove", "bars": 8, "energy": .6, "active_roles": ["Kick"]}]
    )
    with pytest.raises(ValueError, match="HOOK_REQUIRED"):
        goal.validate_track_spec(spec)
