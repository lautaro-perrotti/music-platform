from __future__ import annotations

from pathlib import Path

from copilot.audio.advanced_perception_v1 import SemanticAudioProvider
from copilot.integration.musical_intelligence_recovery_v1 import (
    build_long_range_context,
    diagnose_bad_alpha,
    run_candidate_search,
)
from copilot.runtime.provider_registry_v1 import ProviderCapability, ProviderCapabilityRegistry
from copilot.schemas.music_analysis import MusicAnalysisPack, MusicAnalysisWindow, SectionEvidence
from copilot.schemas.reference_analysis import ReferenceStateTokens
from copilot.schemas.musical_intelligence import ProviderHealth


def _pack() -> MusicAnalysisPack:
    return MusicAnalysisPack(
        tokens=ReferenceStateTokens(reference_state_token="reference", target_state_token="target"),
        tempo_bpm=128,
        windows=[
            MusicAnalysisWindow(index=0, start_beat=0, end_beat=16, energy_db=-12),
            MusicAnalysisWindow(index=1, start_beat=16, end_beat=32, energy_db=-8),
        ],
        sections=[
            SectionEvidence(name="A", start_beat=0, end_beat=16, function="INTRO"),
            SectionEvidence(name="B", start_beat=16, end_beat=32, function="DROP"),
        ],
        evidence_refs=["fixture.energy"],
    )


def test_semantic_provider_is_typed_and_fails_closed() -> None:
    result = SemanticAudioProvider().observe(source_token="reference", evidence_refs=["fixture.audio"])
    assert result.status == "SEMANTIC_PROVIDER_UNAVAILABLE"
    assert result.observations == []
    assert result.no_write is True


def test_provider_registry_distinguishes_configuration_from_health() -> None:
    registry = ProviderCapabilityRegistry(cache_ttl_s=300)
    registry.register(ProviderCapability(provider_id="demo", capability="TEXT_REASONING", configured=True))
    before = registry.snapshot()[0]
    assert before.health is ProviderHealth.CONFIGURED
    after = registry.probe("demo", "TEXT_REASONING", lambda: True)
    assert after.health is ProviderHealth.HEALTHY
    assert after.availability is True


def test_long_range_context_preserves_unknown_motif_state() -> None:
    context = build_long_range_context(_pack())
    assert context.status == "PARTIAL"
    assert [row["energy_db"] for row in context.energy_arc] == [-12, -8]
    assert any("motif" in item.lower() for item in context.limitations)
    assert context.no_write is True


def test_candidate_search_does_not_invent_candidates() -> None:
    report = run_candidate_search({"reference": "fixture"})
    assert report.status == "CANDIDATE_GENERATION_PROVIDER_UNAVAILABLE"
    assert report.candidates == []
    assert report.no_write is True


def test_bad_alpha_records_rejection_without_inventing_reason() -> None:
    artifact = {
        "status": "PRODUCTION_PASS_VERIFIED / REVISION_PROVIDER_LIMITED",
        "execution": {"actions": [{"action_type": "SAMPLE_LOAD", "status": "VERIFIED"}], "failed": 0},
        "final": {"original_untouched": True, "safe_write_authorities": 1},
        "lucas": {"strategy_provenance": "REAL_LUCAS", "plan": {"plan_id": "p", "actions": []}},
        "lucas_feedback": {"status": "CRITIQUE_PROVIDER_UNAVAILABLE", "result": None},
    }
    report = diagnose_bad_alpha(artifact)
    assert report["user_judgment"] == "REJECTED"
    assert report["reason_provided"] is False
    assert "exact reason for user rejection" in report["insufficient_evidence"]

