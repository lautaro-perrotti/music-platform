from __future__ import annotations

from copilot.audio.deep_causal_v2 import (
    build_deep_causal_context,
    build_signal_graph,
    causal_summary,
    context_from_evidence,
    evaluate_causal_context,
    extract_observed_effects,
    generate_causal_candidates,
)
from copilot.schemas.deep_causal import (
    CausalCandidate,
    CausalContext,
    CausalGrade,
    CausalNode,
    CausalNodeKind,
    CausalPath,
    CausalPathKind,
    WindowMeasurement,
)
from copilot.schemas.evidence import EvidenceItem, EvidenceKind, EvidencePack
from copilot.schemas.music_analysis import MusicAnalysisPack, SectionEvidence, TransitionEvidence
from copilot.schemas.reference_analysis import ReferenceStateTokens
from copilot.schemas.session import MixerState, RoutingState, SendState, SessionState, TrackState


def _measurement(node: str, phase: str, level: float, ref: str, *, generation: int = 1) -> WindowMeasurement:
    return WindowMeasurement(
        node_id=node,
        phase=phase,  # type: ignore[arg-type]
        level_db=level,
        active=True,
        start_s={"before": 0.0, "during": 1.0, "after": 2.0}[phase],
        end_s={"before": 1.0, "during": 2.0, "after": 3.0}[phase],
        evidence_refs=[ref],
        project_identity="fixture-project",
        project_token="project-token",
        audible_token="audible-token",
        generation=generation,
    )


def _context(*, ambiguous: bool = False, stale: bool = False) -> CausalContext:
    path = CausalPath(
        path_id="audio.source-device-main",
        kind=CausalPathKind.AUDIO,
        node_ids=["source", "device", "main"],
        parallel_path_ids=["parallel"] if ambiguous else [],
        evidence_refs=["routing-1"],
    )
    measurements = [
        _measurement("source", "before", -12.0, "source-before", generation=2 if stale else 1),
        _measurement("source", "during", -6.0, "source-during", generation=2 if stale else 1),
        _measurement("source", "after", -12.0, "source-after", generation=2 if stale else 1),
        _measurement("device", "before", -12.0, "device-before", generation=2 if stale else 1),
        _measurement("device", "during", -6.0, "device-during", generation=2 if stale else 1),
        _measurement("device", "after", -12.0, "device-after", generation=2 if stale else 1),
        _measurement("main", "before", -12.0, "main-before", generation=2 if stale else 1),
        _measurement("main", "during", -6.0, "main-during", generation=2 if stale else 1),
        _measurement("main", "after", -12.0, "main-after", generation=2 if stale else 1),
    ]
    return CausalContext(
        project_identity="fixture-project",
        project_token="project-token",
        audible_token="audible-token",
        evidence_generation=1,
        nodes=[
            CausalNode(node_id="source", kind=CausalNodeKind.SOURCE),
            CausalNode(node_id="device", kind=CausalNodeKind.DEVICE),
            CausalNode(node_id="main", kind=CausalNodeKind.MAIN),
        ],
        paths=[path],
        candidates=[
            CausalCandidate(
                candidate_id="known-cause",
                cause_node_id="source",
                effect_node_id="main",
                path_id=path.path_id,
                cause_event_start_s=1.0,
                effect_event_start_s=1.05,
                measurements=measurements,
                counterevidence=["ambiguous alternate path"] if ambiguous else [],
            )
        ],
        provenance=["fixture:deep-causal-v2"],
    )


def test_known_causal_fixture_recovers_strong_support() -> None:
    result = evaluate_causal_context(_context())
    evidence = result.results[0]
    assert evidence.grade == CausalGrade.STRONG_CAUSAL_SUPPORT
    assert evidence.path_valid is True
    assert evidence.temporal_precedence is True
    assert evidence.propagation_supported is True
    assert result.musical_writes == 0


def test_ambiguous_fixture_abstains_and_preserves_alternative() -> None:
    result = evaluate_causal_context(_context(ambiguous=True))
    evidence = result.results[0]
    assert evidence.grade == CausalGrade.CAUSALITY_UNRESOLVED
    assert evidence.counterevidence
    assert causal_summary(result)["MUSICAL_WRITES"] == 0


def test_stale_generation_fails_closed() -> None:
    evidence = evaluate_causal_context(_context(stale=True)).results[0]
    assert evidence.grade == CausalGrade.CAUSALITY_UNRESOLVED
    assert any("stale" in reason for reason in evidence.reasons)


def test_missing_path_is_not_attributed() -> None:
    context = _context()
    context.paths = []
    evidence = evaluate_causal_context(context).results[0]
    assert evidence.grade == CausalGrade.COINCIDENT
    assert evidence.path_valid is False


def test_compatible_with_cause_is_weaker_than_causal_support() -> None:
    context = _context()
    candidate = context.candidates[0]
    candidate.measurements = [
        item.model_copy(update={"active": False}) if item.phase == "during" and item.node_id == "main" else item
        for item in candidate.measurements
    ]
    evidence = evaluate_causal_context(context).results[0]
    assert evidence.grade == CausalGrade.COMPATIBLE_WITH_CAUSE


def test_control_path_is_not_collapsed_into_audio_path() -> None:
    context = _context()
    context.paths = [
        CausalPath(
            path_id="sidechain-control",
            kind=CausalPathKind.CONTROL,
            node_ids=["source", "main"],
            evidence_refs=["sidechain-1"],
        )
    ]
    context.candidates[0] = context.candidates[0].model_copy(update={"path_id": "sidechain-control"})
    evidence = evaluate_causal_context(context).results[0]
    assert evidence.path_valid is True
    assert evidence.path_kind == CausalPathKind.CONTROL
    assert "sidechain-1" in evidence.evidence_refs


def test_missing_intermediate_propagation_is_weak_support() -> None:
    context = _context()
    context.candidates[0].measurements = [
        item for item in context.candidates[0].measurements if item.node_id != "device"
    ]
    evidence = evaluate_causal_context(context).results[0]
    assert evidence.grade == CausalGrade.WEAK_CAUSAL_SUPPORT


def test_negative_direction_is_coincident_not_causal() -> None:
    context = _context()
    context.candidates[0].measurements = [
        item.model_copy(update={"level_db": -18.0})
        if item.node_id == "main" and item.phase == "during"
        else item
        for item in context.candidates[0].measurements
    ]
    evidence = evaluate_causal_context(context).results[0]
    assert evidence.grade == CausalGrade.COINCIDENT
    assert evidence.direction_compatible is False


def test_intentional_source_silence_is_not_reported_as_error() -> None:
    context = _context()
    context.candidates[0].measurements = [
        item.model_copy(update={"active": False})
        if item.node_id == "source" and item.phase == "during"
        else item
        for item in context.candidates[0].measurements
    ]
    evidence = evaluate_causal_context(context).results[0]
    assert evidence.grade == CausalGrade.COMPATIBLE_WITH_CAUSE
    assert "error" not in " ".join(evidence.reasons).lower()


def test_parallel_direct_and_return_paths_are_explicit() -> None:
    return_track = TrackState(
        stable_id="return-a",
        index=1,
        name="A-Reverb",
        role="return",
        mixer=MixerState(),
        routing=RoutingState(output_type="Main"),
    )
    source = TrackState(
        stable_id="source-a",
        index=0,
        name="Source",
        role="audio",
        mixer=MixerState(),
        routing=RoutingState(output_type="Main"),
        sends=[SendState(index=0, name="A-Reverb", value=0.5)],
    )
    nodes, paths = build_signal_graph(SessionState(project_identity="p", tracks=[source, return_track]))
    direct = next(path for path in paths if path.path_id == "audio:source-a:main")
    parallel = next(path for path in paths if path.path_id == "audio:source-a:return:return-a")
    assert parallel.kind == CausalPathKind.AUDIO
    assert parallel.path_id in direct.parallel_path_ids


def test_mixed_identity_is_unresolved() -> None:
    context = _context()
    context.candidates[0].measurements[0] = context.candidates[0].measurements[0].model_copy(
        update={"project_identity": "other-project"}
    )
    evidence = evaluate_causal_context(context).results[0]
    assert evidence.grade == CausalGrade.CAUSALITY_UNRESOLVED


def _pack_with_identity() -> EvidencePack:
    return EvidencePack(
        pack_id="real-fact-pack",
        analysis_version="fixture",
        prompt_schema_version="fixture",
        region="r1",
        project_token="project-token",
        audible_token="audible-token",
        items=[
            EvidenceItem(
                evidence_id="identity",
                kind=EvidenceKind.STATE_TOKEN,
                source_ref="session",
                region="r1",
                analysis_version="fixture",
                name="project_identity",
                value="fixture-project",
                project_token="project-token",
                audible_token="audible-token",
            )
        ],
    )


def test_facts_generate_events_and_candidates_without_causal_annotations() -> None:
    session = SessionState(
        project_identity="fixture-project",
        project_token="project-token",
        audible_token="audible-token",
        tracks=[
            TrackState(
                stable_id="bass-track",
                index=0,
                name="Bass",
                role="midi",
                mixer=MixerState(),
                routing=RoutingState(output_type="Main"),
            )
        ],
    )
    analysis = MusicAnalysisPack(
        tokens=ReferenceStateTokens(reference_state_token="reference", target_state_token="target"),
        tempo_bpm=120.0,
        transitions=[TransitionEvidence(start_beat=8.0, end_beat=10.0, kind="ENERGY_DIP", energy_delta_db=-4.0, event_locations=[9.0])],
    )
    pack = _pack_with_identity()
    events = extract_observed_effects(pack, analysis)
    nodes, paths = build_signal_graph(session, sidechain_sources={"Bass"})
    candidates = generate_causal_candidates(events, nodes=nodes, paths=paths)
    assert events and events[0].effect_node_id == "main"
    assert candidates and candidates[0].cause_node_id == "bass-track"
    assert candidates[0].event_id == events[0].event_id
    context = build_deep_causal_context(pack, analysis_pack=analysis, session=session)
    result = evaluate_causal_context(context)
    assert result.results
    assert result.results[0].grade == CausalGrade.CAUSALITY_UNRESOLVED
    assert result.musical_writes == 0


def test_evidence_pack_and_music_analysis_adapter_is_read_only() -> None:
    pack = EvidencePack(
        pack_id="pack-1",
        analysis_version="fixture",
        prompt_schema_version="fixture",
        region="r1",
        project_token="project-token",
        audible_token="audible-token",
        items=[
            EvidenceItem(
                evidence_id="identity",
                kind=EvidenceKind.STATE_TOKEN,
                source_ref="session",
                region="r1",
                analysis_version="fixture",
                name="project_identity",
                value="fixture-project",
                project_token="project-token",
                audible_token="audible-token",
            )
        ],
    )
    context = context_from_evidence(pack, candidates=_context().candidates, paths=_context().paths)
    result = evaluate_causal_context(context)
    assert result.results[0].grade == CausalGrade.STRONG_CAUSAL_SUPPORT
    assert result.musical_writes == 0
