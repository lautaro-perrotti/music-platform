from pathlib import Path

import numpy as np
import soundfile as sf
import pytest

from copilot.audio.advanced_perception_v1 import (
    build_multi_reference_bundle,
    run_advanced_perception,
)
from copilot.audio.music_analyzer import analyze_reference_music
from copilot.schemas.advanced_perception import (
    PerceptionObservation,
    ReferenceFeatureBinding,
    ReferenceRoleBinding,
)


def _pack(tmp_path: Path, token: str, frequency: float = 110.0):
    sample_rate = 16000
    time = np.arange(sample_rate * 4) / sample_rate
    path = tmp_path / f"{token.replace(':', '_')}.wav"
    sf.write(path, (0.3 * np.sin(2 * np.pi * frequency * time)).astype(np.float32), sample_rate)
    pack = analyze_reference_music(
        path,
        reference_state_token=token,
        target_state_token=f"target:{token}",
        tempo_bpm=120,
        use_cache=False,
    )
    return pack, path


def test_advanced_perception_is_provider_limited_without_semantic_providers(tmp_path: Path):
    pack, path = _pack(tmp_path, "reference:a")
    result = run_advanced_perception(pack, audio_path=path)

    assert result.status.value == "PROVIDER_LIMITED"
    assert result.no_write is True
    assert any(item.question == "section.non_template_boundary" for item in result.observations)
    assert any(item.name == "clap" and not item.available for item in result.providers)
    assert any(item.name == "music-flamingo" and not item.available for item in result.providers)
    assert result.metadata["graph_nodes"] == len(result.observations)


def test_fusion_preserves_contradiction_instead_of_selecting_a_fact(tmp_path: Path):
    pack, _ = _pack(tmp_path, "reference:contradiction")
    extra = PerceptionObservation(
        observation_id="perception.secondary.section-non-template-boundary",
        provider="secondary-local",
        provider_version="1",
        domain="structure",
        question="section.non_template_boundary",
        value=not any(section.start_beat > 0 for section in pack.sections),
        source_token=pack.tokens.reference_state_token,
        evidence_refs=["secondary.change_points"],
        confidence=0.5,
    )
    result = run_advanced_perception(pack, additional_observations=[extra])

    assert result.status.value == "PARTIAL"
    assert any(row["status"] == "CONTRADICT" for row in result.fusions)
    assert result.metadata["contradiction_count"] >= 1


def test_multi_reference_bundle_keeps_tokens_and_bindings_separate(tmp_path: Path):
    first, _ = _pack(tmp_path, "reference:bass", 55.0)
    second, _ = _pack(tmp_path, "reference:vocal", 220.0)
    bundle = build_multi_reference_bundle(
        [first, second],
        role_bindings=[ReferenceRoleBinding(
            role="bass",
            reference_state_token=first.tokens.reference_state_token,
            features=["lowend", "groove"],
            confidence=0.8,
        )],
        feature_bindings=[ReferenceFeatureBinding(
            feature="timbre",
            reference_state_token=second.tokens.reference_state_token,
            observation_ids=["music_analyzer.chroma"],
        )],
    )

    assert bundle.no_write is True
    assert bundle.references == ["reference:bass", "reference:vocal"]
    assert bundle.role_bindings[0].reference_state_token == "reference:bass"
    assert bundle.feature_bindings[0].reference_state_token == "reference:vocal"


def test_multi_reference_rejects_duplicate_identity(tmp_path: Path):
    first, _ = _pack(tmp_path, "reference:duplicate")
    with pytest.raises(ValueError, match="distinct reference tokens"):
        build_multi_reference_bundle([first, first])
