from __future__ import annotations

from pathlib import Path

from copilot.music_generation.ace_step import AceStepProvider, choose_acestep_profile
from copilot.music_generation.schemas import GenerationBrief, GeneratorHealth, GeneratorRequest
from copilot.music_generation.registry import MusicGeneratorRegistry


def test_four_gb_profile_is_dit_only_and_not_xl() -> None:
    profile = choose_acestep_profile(4.0)
    assert profile["dit"] == "acestep-v15-turbo"
    assert profile["lm"] is None
    assert profile["offload"] is True


def test_ace_provider_is_truthful_when_checkpoint_is_absent(tmp_path: Path) -> None:
    provider = AceStepProvider(model_path=tmp_path / "missing")
    assert provider.health() is GeneratorHealth.UNAVAILABLE
    brief = GenerationBrief(brief_id="test", user_intent="instrumental electronic music", target_duration_s=10)
    batch = provider.generate(GeneratorRequest(request_id="candidate-a", brief=brief, seed=1, output_dir=tmp_path))
    assert batch.status == "GENERATOR_UNAVAILABLE"
    assert batch.assets == []
    assert batch.failures[0]["code"] == "MODEL_MISSING"


def test_generator_registry_does_not_conflate_reasoning_provider() -> None:
    registry = MusicGeneratorRegistry()
    provider = AceStepProvider(model_path="missing")
    registry.register(provider)
    assert registry.descriptors()[0].provider_id == "ace-step"
    assert registry.descriptors()[0].health is GeneratorHealth.UNAVAILABLE

