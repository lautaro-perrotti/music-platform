"""EVIDENCE_SYSTEM_V2 — graph, fusion, freshness, pack adapter."""

from __future__ import annotations

import json

import pytest

from copilot.audio.evidence_pack_v1 import build_evidence_pack, persist_evidence_pack
from copilot.runtime.evidence import (
    ConfidenceComponents,
    EvidenceGraph,
    EvidenceNode,
    HallucinatedEvidenceError,
    ProjectIsolationError,
    StaleEvidenceError,
    canonicalize_limitation,
    graph_from_pack,
    inventory_for_goals,
    requirements_for_goals,
)
from copilot.runtime.freshness import FreshnessClock, FreshnessDomain
from copilot.schemas.evidence import (
    EvidenceKind,
    EvidencePack,
    FusionStatus,
    LimitationCode,
    ValidityStatus,
)


def _node(**kwargs) -> EvidenceNode:
    kwargs.setdefault("kind", EvidenceKind.MEASUREMENT.value)
    return EvidenceNode(**kwargs)


def _clock(identity: str = "proj-a", token: str = "tok-a") -> FreshnessClock:
    clock = FreshnessClock()
    clock.bind_project(identity, token)
    return clock


def test_node_to_dict_includes_contract_fields() -> None:
    node = _node(
        identity="ev.main.capture",
        subject_identity="main",
        region="R1:0-32",
        source_artifact="main.wav",
        provider="fullmix-obs-1",
        version="1",
        provider_version="1",
        project_identity="proj-a",
        state_tokens={"project": "tok-a"},
        quality="LIMITED",
        confidence_components=ConfidenceComponents(measurement_quality="LIMITED"),
        limitations=[{"code": LimitationCode.ALIGNMENT_LIMITED.value}],
        freshness={"generations": {"ROUTING_STATE": 0}},
        validity=ValidityStatus.VALID.value,
        artifact_hash="abc",
        freshness_domains=["ROUTING_STATE"],
        provenance={"pack_id": "pack_1"},
        dependencies=["ev.region"],
    )
    dumped = node.to_dict()
    for key in (
        "identity",
        "evidence_id",
        "kind",
        "evidence_type",
        "subject_identity",
        "region",
        "time_scope",
        "source_artifact",
        "source_ref",
        "provider",
        "version",
        "provider_version",
        "project_identity",
        "state_tokens",
        "provenance",
        "dependencies",
        "quality",
        "confidence",
        "confidence_components",
        "limitations",
        "freshness",
        "validity",
    ):
        assert key in dumped
    assert dumped["evidence_id"] == "ev.main.capture"
    assert dumped["confidence_components"]["combined"] is None


def test_dependency_invalidation_cascades() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    graph.add(_node(identity="routing-1", kind="routing_fact", dependencies=["ROUTING_STATE"]))
    graph.add(
        _node(
            identity="derived-1",
            kind=EvidenceKind.RELATIONSHIP.value,
            dependencies=["routing-1"],
        )
    )
    graph.add(_node(identity="midi-1", kind="midi_static", dependencies=["ARRANGEMENT_STATE"]))
    dropped = graph.invalidate_for_domains(["ROUTING_STATE"])
    assert "routing-1" in dropped
    assert "derived-1" in dropped
    assert "midi-1" in graph.nodes
    assert "routing-1" not in graph.nodes


def test_upstream_stale_marks_derived_stale() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    graph.add(_node(identity="up", kind=EvidenceKind.FACT.value))
    graph.add(
        _node(
            identity="down",
            kind=EvidenceKind.RELATIONSHIP.value,
            dependencies=["up"],
        )
    )
    marked = graph.mark_stale(["up"])
    assert "up" in marked
    assert "down" in marked
    assert graph.nodes["down"].validity == ValidityStatus.STALE.value
    with pytest.raises(StaleEvidenceError):
        graph.require_valid("down")


def test_cross_project_isolation_rejects_mixed_nodes() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    graph.add(_node(identity="a", project_identity="proj-a"))
    with pytest.raises(ProjectIsolationError):
        graph.add(_node(identity="b", project_identity="proj-b"))
    assert "b" not in graph.nodes


def test_project_switch_drops_prior_nodes() -> None:
    graph = EvidenceGraph(project_identity="proj-a", project_state="tok-a")
    graph.add(_node(identity="a", project_identity="proj-a"))
    dropped = graph.switch_project("proj-b", "tok-b")
    assert dropped == ["a"]
    assert graph.nodes == {}
    graph.add(_node(identity="b", project_identity="proj-b"))
    assert list(graph.nodes) == ["b"]


def test_contradiction_preservation() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    graph.add(
        _node(
            identity="src-a",
            question="kick_present",
            payload={"value": True},
            provider="analyzer-a",
        )
    )
    graph.add(
        _node(
            identity="src-b",
            question="kick_present",
            payload={"value": False},
            provider="analyzer-b",
        )
    )
    record = graph.fuse("kick_present", ["src-a", "src-b"])
    assert record.status == FusionStatus.CONTRADICT.value
    assert "src-a" in graph.nodes
    assert "src-b" in graph.nodes
    view = graph.view(question="kick_present")
    assert "src-a" in view.nodes and "src-b" in view.nodes
    assert set(view.counterevidence) == {"src-a", "src-b"}


def test_llm_interpretation_not_comparable_to_measurement() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    graph.add(_node(identity="meas", question="punch", payload={"value": 0.2}))
    graph.add(
        _node(
            identity="interp",
            kind=EvidenceKind.INTERPRETATION.value,
            question="punch",
            payload={"value": "kick is weak"},
            provider="astra",
        )
    )
    record = graph.fuse("punch", ["meas", "interp"])
    assert record.status == FusionStatus.NOT_COMPARABLE.value
    assert "meas" in graph.nodes


def test_limitation_propagation() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    graph.add(
        _node(
            identity="cap",
            limitations=[{"code": LimitationCode.ALIGNMENT_LIMITED.value, "detail": "±52 ms"}],
        )
    )
    graph.add(
        _node(
            identity="derived",
            kind=EvidenceKind.RELATIONSHIP.value,
            dependencies=["cap"],
            limitations=[{"code": LimitationCode.MIDI_UNAVAILABLE.value}],
        )
    )
    codes = graph.nodes["derived"].limitation_codes()
    assert LimitationCode.ALIGNMENT_LIMITED.value in codes
    assert LimitationCode.MIDI_UNAVAILABLE.value in codes
    view = graph.view()
    view_codes = {row["code"] for row in view.limitations}
    assert LimitationCode.ALIGNMENT_LIMITED.value in view_codes


def test_stale_state_rejection() -> None:
    clock = _clock()
    graph = EvidenceGraph(project_identity="proj-a", project_state="tok-a")
    graph.bind_clock(clock)
    graph.add(
        _node(
            identity="routing",
            kind="routing_fact",
            project_identity="proj-a",
            project_state="tok-a",
            state_tokens={"project": "tok-a"},
            freshness={"generations": {FreshnessDomain.ROUTING_STATE.value: 0}},
            freshness_domains=[FreshnessDomain.ROUTING_STATE.value],
        )
    )
    clock.apply_mutation("set_track_input_routing", {"track_index": 0})
    clock.project_token = "tok-b"
    changed = graph.apply_freshness(clock)
    assert "routing" in changed
    assert graph.nodes["routing"].validity == ValidityStatus.STALE.value
    view = graph.view()
    assert "routing" not in view.nodes
    with pytest.raises(StaleEvidenceError):
        graph.require_valid("routing")


def test_immutable_artifact_reuse_by_hash() -> None:
    clock = _clock()
    graph = EvidenceGraph(project_identity="proj-a", project_state="tok-a")
    graph.bind_clock(clock)
    graph.add(
        _node(
            identity="ev.fullmix",
            project_identity="proj-a",
            artifact_hash="wav-deadbeef",
            immutable_artifact=True,
            freshness={"generations": {FreshnessDomain.ROUTING_STATE.value: 0}},
            freshness_domains=[FreshnessDomain.ROUTING_STATE.value],
            payload={"event_count": 3},
        )
    )
    graph.add(
        _node(
            identity="routing",
            kind="routing_fact",
            project_identity="proj-a",
            dependencies=["ROUTING_STATE"],
            freshness_domains=[FreshnessDomain.ROUTING_STATE.value],
        )
    )
    clock.apply_mutation("set_track_input_routing", {"track_index": 0})
    graph.apply_freshness(clock)
    dropped = graph.invalidate_for_domains(["ROUTING_STATE"])
    assert "routing" in dropped
    assert "ev.fullmix" in graph.nodes
    assert graph.nodes["ev.fullmix"].is_valid()
    assert graph.nodes["ev.fullmix"].artifact_hash == "wav-deadbeef"


def test_evidence_view_scoping() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    graph.add(
        _node(
            identity="ev.fullmix",
            region="R1:0-32",
            subject_identity="main",
            question="fullmix_observation",
            payload={"name": "fullmix_observation", "event_count": 2},
        )
    )
    graph.add(
        _node(
            identity="ev.midi",
            kind=EvidenceKind.FACT.value,
            region="R2:32-64",
            subject_identity="clip",
            question="midi_evidence",
            payload={"name": "midi_evidence"},
        )
    )
    view = graph.view(goals=("ENERGY_STRUCTURE",), region="R1:0-32")
    assert "ev.fullmix" in view.nodes
    assert "ev.midi" not in view.nodes
    assert view.summary["node_count"] == 1
    assert view.provenance[0]["evidence_id"] == "ev.fullmix"


def test_missing_evidence_planning_has_no_sequence() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    graph.add(
        _node(
            identity="ev.main.capture",
            question="main_capture",
            payload={"name": "main_capture"},
        )
    )
    needed = requirements_for_goals(("ENERGY_STRUCTURE", "HARMONIC_CONTEXT"), graph)
    inventory = inventory_for_goals(("ENERGY_STRUCTURE", "HARMONIC_CONTEXT"), graph)
    assert "CAPTURE_MAIN" not in needed
    assert "FULLMIX_ANALYSIS" in needed
    assert "READ_MIDI" in needed
    assert inventory["missing"] == inventory["acquisition"]["capabilities"]
    assert inventory["acquisition"]["sequencing"] is None
    assert "acquisition_order" not in inventory
    bare = requirements_for_goals(("ENERGY_STRUCTURE",))
    assert bare == ["CAPTURE_MAIN", "FULLMIX_ANALYSIS"]


def test_provider_failure_records_limitation_not_measurement() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    node = graph.record_provider_failure(
        provider="astra",
        code=LimitationCode.MODEL_UNAVAILABLE.value,
        detail="not configured",
    )
    assert node.kind == EvidenceKind.LIMITATION.value
    assert LimitationCode.MODEL_UNAVAILABLE.value in node.limitation_codes()
    assert not any(item.kind == EvidenceKind.MEASUREMENT.value for item in graph.nodes.values())


def test_no_hallucinated_measurement_from_llm() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    with pytest.raises(HallucinatedEvidenceError):
        graph.add(
            _node(
                identity="fake-rms",
                kind=EvidenceKind.MEASUREMENT.value,
                provider="astra",
                payload={"rms": 0.9},
            )
        )
    assert graph.nodes == {}
    graph.add(
        _node(
            identity="ok-interp",
            kind=EvidenceKind.INTERPRETATION.value,
            provider="astra",
            payload={"text": "maybe muddy"},
        )
    )
    view = graph.view(kinds=(EvidenceKind.MEASUREMENT.value,))
    assert view.nodes == {}


def test_pack_adapter_reads_existing_pack_without_rewrite(tmp_path) -> None:
    built = build_evidence_pack(
        project_token="pt",
        audible_token="at",
        project_identity="proj-a",
        region_id="R1",
        region={"start_qn": 0.0, "end_qn": 32.0},
        main_capture={
            "ok": True,
            "signal_class": "HAS_SIGNAL",
            "rms": 0.1,
            "audio_sha256": "hash-main",
            "quality": "LIMITED",
        },
        source_captures=[
            {
                "ok": True,
                "signal_class": "HAS_SIGNAL",
                "audio_sha256": "hash-src",
                "ref": {"content_fingerprint": "fp1", "role": "kick"},
            }
        ],
        fullmix={"ok": True, "analyzer_id": "fullmix-obs-1", "events": [1, 2]},
        lowend={"ok": True, "analyzer_id": "lowend-obs-1", "temporal": {"kick_events": 2}},
        arrangement={"active_count": 1, "eligible_count": 1},
        routing={"main_final": True},
    )
    path = persist_evidence_pack(built, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["milestone"] == "EVIDENCE_PACK_V1"
    pack = EvidencePack.model_validate(raw["pack"])
    graph = graph_from_pack(pack, project_identity="proj-a", provider="producer_analyze_v1")
    assert "ev.main.capture" in graph.nodes
    assert "ev.fullmix" in graph.nodes
    assert "ev.lowend" in graph.nodes
    assert graph.nodes["ev.fullmix"].dependencies == ["ev.main.capture"]
    assert "ev.main.capture" in graph.nodes["ev.lowend"].dependencies
    assert "ev.source.0.capture" in graph.nodes["ev.lowend"].dependencies
    codes = {row["code"] for row in graph.view().limitations}
    assert LimitationCode.ALIGNMENT_LIMITED.value in codes
    assert LimitationCode.MIDI_UNAVAILABLE.value in codes
    assert graph.nodes["ev.main.capture"].artifact_hash == "hash-main"
    assert graph.nodes["ev.main.capture"].immutable_artifact is True
    assert raw["pack"]["items"][0]["kind"] in {item.value for item in EvidenceKind}


def test_pack_adapter_rejects_foreign_project() -> None:
    built = build_evidence_pack(
        project_token="pt",
        audible_token="at",
        project_identity="proj-a",
        region_id="R1",
        region={"start_qn": 0.0, "end_qn": 8.0},
    )
    graph = graph_from_pack(built, project_identity="proj-a")
    foreign = build_evidence_pack(
        project_token="other",
        audible_token="at2",
        project_identity="proj-b",
        region_id="R1",
        region={"start_qn": 0.0, "end_qn": 8.0},
        main_capture={"ok": True, "signal_class": "HAS_SIGNAL", "audio_sha256": "x"},
    )
    other = graph_from_pack(foreign, project_identity="proj-b")
    with pytest.raises(ProjectIsolationError):
        graph.add(other.nodes["ev.main.capture"])


def test_clock_mismatch_rejects_graph() -> None:
    graph = EvidenceGraph(project_identity="proj-a")
    graph.add(_node(identity="a", project_identity="proj-a"))
    clock = _clock("proj-b", "tok-b")
    with pytest.raises(ProjectIsolationError):
        graph.bind_clock(clock)


def test_confidence_components_have_no_invented_combined() -> None:
    components = ConfidenceComponents(
        measurement_quality="LIMITED",
        provider_confidence=0.4,
        source_reliability="main",
        cross_source_agreement=FusionStatus.CONTRADICT.value,
        reasoning_confidence="LOW",
    )
    dumped = components.to_dict()
    assert dumped["combined"] is None
    assert dumped["measurement_quality"] == "LIMITED"
    assert dumped["cross_source_agreement"] == FusionStatus.CONTRADICT.value


def test_limitation_aliases_keep_pack_codes_readable() -> None:
    assert canonicalize_limitation("MIDI_UNREAD") == LimitationCode.MIDI_UNAVAILABLE.value
    assert canonicalize_limitation("ALIGNMENT_LIMITED") == LimitationCode.ALIGNMENT_LIMITED.value
