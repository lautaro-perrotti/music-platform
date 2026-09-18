"""FOUNDATION_INTEGRATION_CHECKPOINT_V1 — DSP + Evidence + Safe Write, no new features."""

from __future__ import annotations

from copilot.audio.m4l_control_contract_v1 import APPLY_VOCABULARY
from copilot.audio.physical_dsp_v2.audio import buffer_from_samples
from copilot.audio.physical_dsp_v2.fixtures import sine
from copilot.audio.physical_dsp_v2.pipeline import analyze_buffer, analyze_pair
from copilot.runtime.capabilities import build_registry
from copilot.runtime.contracts import AnalyzeProjectRequest
from copilot.runtime.evidence import graph_from_pack
from copilot.runtime.producer import compile_analyze_project
from copilot.schemas.dsp import AnalyzerFamily, DspLimitation, DspObservation, DspQuality
from copilot.schemas.evidence import EvidenceKind, EvidencePack
from copilot.schemas.safe_write import CERTIFIED_PRODUCTION_ACTIONS


def _pack_from_items(items, *, pack_id: str = "ckpt.dsp") -> EvidencePack:
    return EvidencePack(
        pack_id=pack_id,
        analysis_version="checkpoint-v1",
        prompt_schema_version="music-diagnosis-reason-1",
        region="AUTO_36_68",
        project_token="tok-a",
        audible_token="aud-a",
        items=items,
    )


def test_dsp_observations_ingest_into_evidence_graph() -> None:
    buf = buffer_from_samples(sine(44100, 0.4, 220.0, 0.2), 44100)
    bundle = analyze_buffer(
        buf,
        analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,),
        use_cache=False,
    )
    obs = bundle.observations[0]
    assert isinstance(obs, DspObservation)
    items = obs.to_evidence_items(project_token="tok-a", audible_token="aud-a")
    assert items
    assert all(item.kind is EvidenceKind.MEASUREMENT or item.kind is EvidenceKind.LIMITATION for item in items)
    graph = graph_from_pack(_pack_from_items(items), project_identity="proj-a")
    measured = [node for node in graph.nodes.values() if node.kind == EvidenceKind.MEASUREMENT.value]
    assert measured
    assert all(node.artifact_hash == obs.source_artifact_hash for node in measured)
    assert all(node.immutable_artifact for node in measured)
    codes = {row["code"] for node in graph.nodes.values() for row in node.limitations}
    for code in obs.limitation_codes():
        assert code in codes


def test_dsp_limitations_not_dropped_when_not_canonical() -> None:
    buf = buffer_from_samples(sine(44100, 0.4, 220.0, 0.2), 44100)
    bundle = analyze_buffer(
        buf,
        analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,),
        use_cache=False,
    )
    obs = bundle.observations[0]
    obs.limitations.append(DspLimitation("ITU_LRA_NOT_IMPLEMENTED", "not an ITU meter"))
    obs.quality = DspQuality.LIMITED
    items = obs.to_evidence_items()
    graph = graph_from_pack(_pack_from_items(items), project_identity="proj-a")
    codes = {row["code"] for node in graph.nodes.values() for row in node.limitations}
    assert "ITU_LRA_NOT_IMPLEMENTED" in codes


def test_relational_dsp_is_relationship_kind() -> None:
    a = buffer_from_samples(sine(44100, 0.3, 80.0, 0.3), 44100)
    b = buffer_from_samples(sine(44100, 0.3, 90.0, 0.3), 44100)
    pair = analyze_pair(a, b, use_cache=False)
    obs = pair.observations[0]
    item = obs.to_evidence_item(obs.values[0])
    assert item.kind is EvidenceKind.RELATIONSHIP
    graph = graph_from_pack(_pack_from_items(obs.to_evidence_items()), project_identity="proj-a")
    rel = [node for node in graph.nodes.values() if node.kind == EvidenceKind.RELATIONSHIP.value]
    assert rel
    assert all(node.artifact_hash for node in rel)


def test_analyze_project_stays_read_only_and_does_not_sequence_dsp_or_writes() -> None:
    graph = compile_analyze_project(AnalyzeProjectRequest(project=None))
    caps = [node.capability_id for node in graph.nodes]
    assert "PHYSICAL_DSP_V2" not in caps
    assert "SAFE_WRITE" not in caps
    assert not any("WRITE" in cap or cap.startswith("SET_") for cap in caps)
    registry = build_registry()
    assert registry.get("PHYSICAL_DSP_V2") is not None
    assert APPLY_VOCABULARY == ("SET_TRACK_VOLUME",)
    assert CERTIFIED_PRODUCTION_ACTIONS == {"SET_TRACK_VOLUME"}
