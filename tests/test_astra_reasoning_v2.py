from __future__ import annotations

from pathlib import Path

import pytest

from copilot.audio.producer_analyze_v1 import apply_reasoning_result, gate_read_only
from copilot.perf.trace import CAT_CPU, CAT_MODEL, active_trace
from copilot.reasoning.errors import ReasoningFailure
from copilot.reasoning.evidence_input import scoped_evidence, serialize_for_prompt
from copilot.reasoning.fixtures import (
    output_clear_no_action,
    output_clear_temporal,
    pack_clear_no_action,
    pack_clear_temporal,
)
from copilot.reasoning.grounding import validate_reasoning
from copilot.reasoning.pipeline import reason, semantic_fingerprint
from copilot.reasoning.prompt import build_prompt
from copilot.reasoning.provider import FailingProvider, ScriptedProvider, SequenceProvider
from copilot.reasoning.schema import (
    CandidateStrategy,
    GroundedHypothesis,
    ReasoningOutput,
)
from copilot.runtime.evidence import graph_from_pack
from copilot.schemas.diagnosis import CandidateActionType, Confidence, DiagnosisStatus, FindingType
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspLimitation,
    DspObservation,
    DspProvenance,
    DspQuality,
    DspSubject,
    DspSubjectKind,
    MeasuredValue,
    TimeSpan,
)
from copilot.schemas.evidence import (
    CaptureQuality,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    EvidenceRequest,
    EvidenceRequestKind,
    ObservationLimitation,
)

REPO = Path(__file__).resolve().parents[1]
FROZEN_PACK = REPO / "fixtures" / "frozen" / "evidence_pack_v1_astra_external_reasoning.json"
LOG_PACK = REPO / "logs" / "evidence_pack_v1.json"


def _limits(pack: EvidencePack, *extra: str) -> list[str]:
    return list(dict.fromkeys([item.code for item in pack.limitations] + list(extra)))


def _request(**overrides: object) -> EvidenceRequest:
    payload = {
        "request_kind": EvidenceRequestKind.READ_DEVICE_PARAMETERS,
        "why_needed": "MIDI is present but Post Mixer is quiet.",
        "target": "High String",
        "region": "56->68qn",
        "expected_information_gain": "output/gain and dynamics parameters",
        "goal": "DEVICE_CAUSAL_CONTEXT",
        "required_evidence_kinds": ["FACT"],
        "priority": "HIGH",
    }
    payload.update(overrides)
    return EvidenceRequest.model_validate(payload)


def _hypo(pack: EvidencePack, **overrides: object) -> GroundedHypothesis:
    payload = {
        "claim": "Measured facts are compatible with no mix change.",
        "evidence_refs": [pack.items[0].evidence_id],
        "reasoning_summary": "Signal presence is not a quality judgment.",
        "confidence": Confidence.LOW,
        "alternatives_considered": ["NO_ACTION_REQUIRED"],
        "contradicting_evidence_refs": [pack.items[1].evidence_id] if len(pack.items) > 1 else [],
        "entity_refs": ["track:kick"],
        "missing_evidence": [],
        "limitations": _limits(pack),
        "status": DiagnosisStatus.NO_ACTION_REQUIRED,
    }
    payload.update(overrides)
    return GroundedHypothesis.model_validate(payload)


def _output(pack: EvidencePack, **overrides: object) -> ReasoningOutput:
    first = pack.items[0].evidence_id
    payload = {
        "question": "Is a mix change warranted?",
        "scope": pack.region,
        "category": FindingType.NO_ACTION_REQUIRED,
        "summary": "Facts are present; no mix problem is established.",
        "status": DiagnosisStatus.NO_ACTION_REQUIRED,
        "confidence": Confidence.LOW,
        "hypotheses": [_hypo(pack)],
        "evidence_refs": [first],
        "contradicting_evidence_refs": [pack.items[1].evidence_id] if len(pack.items) > 1 else [first],
        "limitations": _limits(pack),
        "candidate_actions": [
            {
                "action_type": CandidateActionType.NO_CHANGE,
                "target": "mix",
                "reason": "No change is valid.",
                "expected_effect": "Leave the region.",
                "risk": "Delaying a needed change.",
                "evidence_refs": [first],
                "entity_refs": ["track:kick"],
            }
        ],
        "candidate_strategies": [
            {
                "strategy": "inspect attenuation in the target signal chain",
                "reason": "Only if later evidence shows post-note-generation loss.",
                "evidence_refs": [first],
                "entity_refs": ["track:kick"],
            }
        ],
        "requested_evidence": [],
        "entity_refs": ["track:kick", "track:bass"],
    }
    payload.update(overrides)
    if "hypotheses" in overrides and isinstance(overrides["hypotheses"], list):
        payload["hypotheses"] = overrides["hypotheses"]
    return ReasoningOutput.model_validate(payload)


def _dsp_observation(*, overlap: bool = True, extra_limits: list[DspLimitation] | None = None) -> DspObservation:
    limits = [
        DspLimitation(
            "POTENTIAL_OVERLAP_IS_MEASURED_RELATIONSHIP",
            "Measured relationship only. Not a corrective suggestion.",
        )
    ]
    limits.extend(extra_limits or [])
    return DspObservation(
        observation_id="obsreloverlap1",
        analyzer_id=AnalyzerFamily.RELATIONAL.value,
        subject=DspSubject(
            kind=DspSubjectKind.SOURCE_PAIR,
            source_id="track:kick",
            source_id_b="track:bass",
            label="kick/bass",
        ),
        time_span=TimeSpan(start_s=0.0, end_s=2.0, start_qn=0.0, end_qn=8.0),
        values=[
            MeasuredValue(
                "relationship",
                "POTENTIAL_OVERLAP" if overlap else "LOW_MEASURED_OVERLAP",
            )
        ],
        quality=DspQuality.OK,
        limitations=limits,
        provenance=DspProvenance(
            provider_id="physical-dsp-v2",
            provider_version="2.0.0",
            method="relational",
            parameters_hash="abc",
            source_artifact_hash="deadbeef",
            cache_key="k",
        ),
        source_artifact_hash="deadbeef",
    )


def _pack_with_items(pack: EvidencePack, items: list[EvidenceItem], extra_limits: list[str] | None = None) -> EvidencePack:
    copied = pack.model_copy(deep=True)
    copied.items = [*list(copied.items), *items]
    for code in extra_limits or []:
        copied.limitations.append(ObservationLimitation(code=code, detail=code))
    return copied


def test_evidence_view_input_is_scoped_not_a_dump() -> None:
    pack = pack_clear_no_action()
    graph = graph_from_pack(pack)
    graph.fuse_comparable()
    view = graph.view()
    scoped = scoped_evidence(pack, view)
    serialized = serialize_for_prompt(scoped)
    prompt = build_prompt(pack, view)
    assert "EVIDENCE_VIEW" in prompt
    assert "parent-pack" not in prompt.lower()
    assert serialized["support"]
    assert "limitations" in serialized
    assert "provenance" in serialized
    assert serialized["serialization"]["skipped_limitation_nodes"] >= 1
    result = reason(pack, ScriptedProvider({pack.pack_id: output_clear_no_action()}), view=view)
    assert result.accepted is True
    assert result.audit.input_stats["serialized_node_count"] == len(serialized["nodes"])
    assert result.diagnosis is not None
    assert result.diagnosis.project_identity
    assert result.diagnosis.question
    assert result.diagnosis.scope
    assert result.diagnosis.confidence_components["combined"] is None


def test_statuses_are_preserved_and_not_collapsed() -> None:
    pack = pack_clear_no_action()
    cases = [
        (DiagnosisStatus.SUPPORTED, FindingType.TEMPORAL_MASKING),
        (DiagnosisStatus.WEAKLY_SUPPORTED, FindingType.SPECTRAL_MASKING),
        (DiagnosisStatus.INSUFFICIENT_EVIDENCE, FindingType.INSUFFICIENT_EVIDENCE),
        (DiagnosisStatus.NO_ACTION_REQUIRED, FindingType.NO_ACTION_REQUIRED),
        (DiagnosisStatus.DIAGNOSIS_UNSTABLE, FindingType.TEMPORAL_MASKING),
    ]
    for status, category in cases:
        kwargs: dict = {
            "status": status,
            "category": category,
            "hypotheses": [
                _hypo(
                    pack,
                    claim=f"Status check {status.value}",
                    status=status,
                    missing_evidence=["READ_MIDI"]
                    if status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
                    else [],
                )
            ],
            "confidence": Confidence.LOW
            if status
            in {DiagnosisStatus.INSUFFICIENT_EVIDENCE, DiagnosisStatus.DIAGNOSIS_UNSTABLE}
            else Confidence.MEDIUM,
        }
        if status is DiagnosisStatus.INSUFFICIENT_EVIDENCE:
            kwargs["requested_evidence"] = [_request(target="track:bass", region=pack.region)]
        output = _output(pack, **kwargs)
        result = reason(pack, ScriptedProvider({pack.pack_id: output}))
        assert result.accepted is True, (status, result.audit.issues)
        assert result.output is not None
        assert result.output.status is status
        assert result.diagnosis is not None
        assert result.diagnosis.status is status
        if status is DiagnosisStatus.INSUFFICIENT_EVIDENCE:
            assert result.output.status is not DiagnosisStatus.NO_ACTION_REQUIRED
            assert result.output.status is not DiagnosisStatus.DIAGNOSIS_UNSTABLE
            gate = gate_read_only(
                diagnosis_status=status.value,
                diagnosis_accepted=True,
                category=category.value,
                candidate_actions=[{"action_type": "NO_CHANGE"}],
            )
            assert gate["milestone_status"] != DiagnosisStatus.NO_ACTION_REQUIRED.value
        if status is DiagnosisStatus.DIAGNOSIS_UNSTABLE:
            gate = gate_read_only(
                diagnosis_status=status.value,
                diagnosis_accepted=True,
                category=category.value,
            )
            assert gate["milestone_status"] != DiagnosisStatus.INSUFFICIENT_EVIDENCE.value


def test_hypothesis_carries_support_counterevidence_and_missing() -> None:
    pack = pack_clear_temporal()
    output = output_clear_temporal()
    output.hypotheses[0].missing_evidence = ["READ_MIDI"]
    output.hypotheses[0].limitations = list(output.limitations)
    result = reason(pack, ScriptedProvider({pack.pack_id: output}))
    assert result.accepted is True
    hypo = result.diagnosis.hypotheses[0]
    assert hypo.evidence_refs
    assert hypo.contradicting_evidence_refs
    assert hypo.missing_evidence == ["READ_MIDI"]
    assert hypo.limitations
    assert hypo.support_status is DiagnosisStatus.SUPPORTED


def test_contradict_fusion_cannot_be_picked_as_fact() -> None:
    pack = _pack_with_items(
        pack_clear_no_action(),
        [
            EvidenceItem(
                evidence_id="ev.src.a.rms",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="asset:frozen",
                region="0->8qn",
                analysis_version="lowend-obs-1",
                name="source_rms",
                value=0.0,
                quality=CaptureQuality.OK,
                limitations=["ALIGNMENT_LIMITED"],
                project_token=pack_clear_no_action().project_token,
                audible_token=pack_clear_no_action().audible_token,
            ),
            EvidenceItem(
                evidence_id="ev.src.b.rms",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="asset:frozen",
                region="0->8qn",
                analysis_version="lowend-obs-1",
                name="source_rms",
                value=0.90,
                quality=CaptureQuality.OK,
                limitations=["ALIGNMENT_LIMITED"],
                project_token=pack_clear_no_action().project_token,
                audible_token=pack_clear_no_action().audible_token,
            ),
        ],
    )
    graph = graph_from_pack(pack)
    graph.fuse_comparable()
    view = graph.view()
    assert any(row.status == "CONTRADICT" for row in graph.fusions)
    bad = _output(
        pack,
        status=DiagnosisStatus.SUPPORTED,
        category=FindingType.LEVEL_IMBALANCE,
        confidence=Confidence.HIGH,
        summary="Source RMS is 0.0 so the other reading is ignored.",
        evidence_refs=["ev.src.a.rms"],
        contradicting_evidence_refs=[],
        hypotheses=[
            _hypo(
                pack,
                claim="Level is 0.10.",
                evidence_refs=["ev.src.a.rms"],
                contradicting_evidence_refs=[],
                status=DiagnosisStatus.SUPPORTED,
                confidence=Confidence.HIGH,
            )
        ],
    )
    rejected = reason(pack, ScriptedProvider({pack.pack_id: bad}), view=view)
    applied = apply_reasoning_result(rejected)
    assert rejected.accepted is False
    assert applied["status"] == DiagnosisStatus.DIAGNOSIS_UNSTABLE.value
    good = _output(
        pack,
        status=DiagnosisStatus.DIAGNOSIS_UNSTABLE,
        category=FindingType.LEVEL_IMBALANCE,
        confidence=Confidence.LOW,
        summary="Two RMS readings disagree (0.0 vs 0.9) so neither is taken as fact.",
        evidence_refs=["ev.src.a.rms", "ev.src.b.rms"],
        contradicting_evidence_refs=["ev.src.a.rms", "ev.src.b.rms"],
        hypotheses=[
            _hypo(
                pack,
                claim="Sources disagree on source_rms.",
                evidence_refs=["ev.src.a.rms", "ev.src.b.rms"],
                contradicting_evidence_refs=["ev.src.a.rms", "ev.src.b.rms"],
                status=DiagnosisStatus.DIAGNOSIS_UNSTABLE,
                confidence=Confidence.LOW,
            )
        ],
    )
    accepted = reason(pack, ScriptedProvider({pack.pack_id: good}), view=view)
    assert accepted.accepted is True
    assert accepted.diagnosis.status is DiagnosisStatus.DIAGNOSIS_UNSTABLE
    assert accepted.diagnosis.confidence_components["cross_source_agreement"] == "CONTRADICT"
    assert accepted.diagnosis.confidence_components["combined"] is None


def test_stale_missing_ref_invented_number_wrong_subject_wrong_region() -> None:
    pack = pack_clear_no_action()
    graph = graph_from_pack(pack)
    graph.mark_stale(["ev.kick.count"])
    stale_view = graph.view(include_stale=True)
    stale_out = output_clear_no_action()
    rejected = reason(pack, ScriptedProvider({pack.pack_id: stale_out}), view=stale_view)
    assert rejected.accepted is False
    assert any("stale" in issue["detail"] for issue in rejected.audit.issues)

    missing = output_clear_no_action()
    missing.evidence_refs = ["ev.does.not.exist"]
    missing.hypotheses[0].evidence_refs = ["ev.does.not.exist"]
    assert validate_reasoning(missing, pack).accepted is False

    invented = output_clear_no_action()
    invented.summary = f"{invented.summary} Peak is -6.0 dB at 300 Hz."
    report = validate_reasoning(invented, pack)
    assert report.accepted is False
    assert any("ungrounded" in issue.detail or "cited EvidenceRefs" in issue.detail for issue in report.issues)

    subject_item = EvidenceItem(
        evidence_id="ev.bass.only",
        kind=EvidenceKind.MEASUREMENT,
        source_ref="track:bass",
        region=pack.region,
        analysis_version="lowend-obs-1",
        name="bass_only_rms",
        value=0.22,
        quality=CaptureQuality.OK,
        limitations=["ALIGNMENT_LIMITED"],
        project_token=pack.project_token,
        audible_token=pack.audible_token,
    )
    subject_pack = _pack_with_items(pack, [subject_item])
    wrong_subject = _output(
        subject_pack,
        evidence_refs=["ev.bass.only"],
        hypotheses=[
            _hypo(
                subject_pack,
                claim="Kick-only reading is 0.22.",
                evidence_refs=["ev.bass.only"],
                entity_refs=["track:kick"],
            )
        ],
        entity_refs=["track:kick"],
    )
    assert validate_reasoning(wrong_subject, subject_pack).accepted is False

    region_item = EvidenceItem(
        evidence_id="ev.other.region",
        kind=EvidenceKind.MEASUREMENT,
        source_ref="asset:frozen",
        region="56->68qn",
        analysis_version="lowend-obs-1",
        name="other_region_rms",
        value=0.33,
        quality=CaptureQuality.OK,
        limitations=["ALIGNMENT_LIMITED"],
        project_token=pack.project_token,
        audible_token=pack.audible_token,
    )
    region_pack = _pack_with_items(pack, [region_item])
    wrong_region = _output(
        region_pack,
        evidence_refs=["ev.other.region"],
        hypotheses=[
            _hypo(
                region_pack,
                claim="Current region rms is 0.33.",
                evidence_refs=["ev.other.region"],
            )
        ],
    )
    report = validate_reasoning(wrong_region, region_pack)
    assert report.accepted is False
    assert any("region mismatch" in issue.detail for issue in report.issues)


def test_dsp_overlap_is_not_muddy_and_limitations_propagate() -> None:
    pack = pack_clear_no_action()
    obs = _dsp_observation(
        extra_limits=[
            DspLimitation("ITU_LRA_NOT_IMPLEMENTED", "not an ITU meter"),
            DspLimitation("KEY_IS_CANDIDATE_SET_NOT_CERTAIN", "candidate set only"),
        ]
    )
    items = obs.to_evidence_items(
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        region=pack.region,
    )
    assert all(item.kind is EvidenceKind.RELATIONSHIP or item.kind is EvidenceKind.LIMITATION for item in items)
    dsp_pack = _pack_with_items(
        pack,
        items,
        extra_limits=[
            "ITU_LRA_NOT_IMPLEMENTED",
            "KEY_IS_CANDIDATE_SET_NOT_CERTAIN",
            "POTENTIAL_OVERLAP_IS_MEASURED_RELATIONSHIP",
        ],
    )
    overlap_id = items[0].evidence_id
    muddy = _output(
        dsp_pack,
        status=DiagnosisStatus.SUPPORTED,
        category=FindingType.SPECTRAL_MASKING,
        confidence=Confidence.HIGH,
        summary="The mix is muddy because of POTENTIAL_OVERLAP.",
        evidence_refs=[overlap_id],
        hypotheses=[
            _hypo(
                dsp_pack,
                claim="The mix is muddy.",
                evidence_refs=[overlap_id],
                status=DiagnosisStatus.SUPPORTED,
                confidence=Confidence.HIGH,
                limitations=_limits(dsp_pack),
            )
        ],
        limitations=_limits(dsp_pack),
    )
    muddy_report = validate_reasoning(muddy, dsp_pack)
    assert muddy_report.accepted is False
    assert any("muddy" in issue.detail for issue in muddy_report.issues)

    dropped = _output(
        dsp_pack,
        limitations=["ALIGNMENT_LIMITED"],
        hypotheses=[_hypo(dsp_pack, limitations=["ALIGNMENT_LIMITED"], evidence_refs=[overlap_id])],
        evidence_refs=[overlap_id],
        summary="Potential masking is plausible from measured overlap.",
        status=DiagnosisStatus.WEAKLY_SUPPORTED,
        category=FindingType.SPECTRAL_MASKING,
    )
    dropped_report = validate_reasoning(dropped, dsp_pack)
    assert dropped_report.accepted is False
    assert any("limitation dropped" in issue.detail for issue in dropped_report.issues)

    key_claim = _output(
        dsp_pack,
        status=DiagnosisStatus.WEAKLY_SUPPORTED,
        category=FindingType.SPECTRAL_MASKING,
        summary="Potential masking is plausible. The key is C major.",
        evidence_refs=[overlap_id],
        limitations=_limits(dsp_pack),
        hypotheses=[
            _hypo(
                dsp_pack,
                claim="Potential masking is plausible.",
                evidence_refs=[overlap_id],
                status=DiagnosisStatus.WEAKLY_SUPPORTED,
                limitations=_limits(dsp_pack),
            )
        ],
    )
    assert validate_reasoning(key_claim, dsp_pack).accepted is False

    ok = _output(
        dsp_pack,
        status=DiagnosisStatus.WEAKLY_SUPPORTED,
        category=FindingType.SPECTRAL_MASKING,
        confidence=Confidence.LOW,
        summary="Measured POTENTIAL_OVERLAP means potential masking is plausible, not a muddy measurement.",
        evidence_refs=[overlap_id],
        limitations=_limits(dsp_pack),
        hypotheses=[
            _hypo(
                dsp_pack,
                claim="Potential masking is plausible given measured overlap.",
                evidence_refs=[overlap_id],
                status=DiagnosisStatus.WEAKLY_SUPPORTED,
                limitations=_limits(dsp_pack),
                confidence=Confidence.LOW,
            )
        ],
        candidate_strategies=[
            CandidateStrategy(
                strategy="rebalance kick/bass relationship",
                reason="Overlap is a relationship, not an automatic EQ write.",
                evidence_refs=[overlap_id],
                entity_refs=["track:kick", "track:bass"],
            )
        ],
    )
    result = reason(dsp_pack, ScriptedProvider({dsp_pack.pack_id: ok}))
    assert result.accepted is True, result.audit.issues
    assert result.musical_writes == 0
    assert "ITU_LRA_NOT_IMPLEMENTED" in result.diagnosis.limitations
    assert "KEY_IS_CANDIDATE_SET_NOT_CERTAIN" in result.diagnosis.limitations


def test_capture_failed_is_not_measured_silence() -> None:
    pack = _pack_with_items(
        pack_clear_no_action(),
        [],
        extra_limits=["CAPTURE_FAILED"],
    )
    output = _output(
        pack,
        summary="Measured silence proves the source is gone.",
        limitations=_limits(pack),
        hypotheses=[_hypo(pack, limitations=_limits(pack))],
    )
    report = validate_reasoning(output, pack)
    assert report.accepted is False
    assert any("CAPTURE_FAILED" in issue.detail for issue in report.issues)


def test_minimal_next_evidence_and_no_command_sequencing() -> None:
    pack = pack_clear_no_action()
    ie = _output(
        pack,
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        category=FindingType.INSUFFICIENT_EVIDENCE,
        requested_evidence=[_request()],
        hypotheses=[
            _hypo(
                pack,
                claim="MIDI-present / quiet-post-mixer is unresolved.",
                status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
                missing_evidence=["READ_DEVICE_PARAMETERS"],
            )
        ],
    )
    result = reason(pack, ScriptedProvider({pack.pack_id: ie}))
    assert result.accepted is True
    req = result.diagnosis.requested_evidence[0]
    assert req.request_kind is EvidenceRequestKind.READ_DEVICE_PARAMETERS
    assert req.target == "High String"
    assert "56->68qn" in req.region
    assert req.goal
    assert req.priority == "HIGH"
    assert "analyze everything" not in req.why_needed.lower()

    broad = ie.model_copy(deep=True)
    broad.requested_evidence = [
        _request(why_needed="analyze everything in the whole project", target="all tracks")
    ]
    assert validate_reasoning(broad, pack).accepted is False

    empty = ie.model_copy(deep=True)
    empty.requested_evidence = []
    assert validate_reasoning(empty, pack).accepted is False

    command = _output(
        pack,
        summary="Run capture command then python -m copilot.cli analyze-project.",
        hypotheses=[_hypo(pack, claim="Execute Python scripts next.")],
    )
    report = validate_reasoning(command, pack)
    assert report.accepted is False
    assert any("command sequencing" in issue.detail for issue in report.issues)


def test_provider_failures_are_not_musical_verdicts() -> None:
    pack = pack_clear_no_action()
    for kind in (
        ReasoningFailure.MODEL_TIMEOUT,
        ReasoningFailure.MODEL_RATE_LIMITED,
        ReasoningFailure.MODEL_UNAVAILABLE,
    ):
        result = reason(pack, FailingProvider(kind, kind.value))
        applied = apply_reasoning_result(result)
        assert result.accepted is False
        assert result.failure is kind
        assert applied["status"] == DiagnosisStatus.DIAGNOSIS_UNSTABLE.value
        assert applied["gate"]["reason"] == kind.value
        assert applied["status"] != DiagnosisStatus.INSUFFICIENT_EVIDENCE.value

    malformed = reason(pack, ScriptedProvider({pack.pack_id: '{"category":'}))
    applied = apply_reasoning_result(malformed)
    assert malformed.failure is ReasoningFailure.MODEL_OUTPUT_INVALID
    assert "truncated" in malformed.audit.issues[0]["detail"]
    assert applied["status"] == DiagnosisStatus.DIAGNOSIS_UNSTABLE.value


def test_semantic_stability_and_swing_is_unstable() -> None:
    pack = pack_clear_no_action()
    stable = ScriptedProvider({pack.pack_id: output_clear_no_action()})
    fingerprints = [
        semantic_fingerprint(reason(pack, stable).output)  # type: ignore[arg-type]
        for _ in range(3)
    ]
    assert len(set(fingerprints)) == 1
    swinging = SequenceProvider([output_clear_no_action(), output_clear_temporal()])
    first = reason(pack, swinging)
    second = reason(pack, swinging)
    assert semantic_fingerprint(first.output) != semantic_fingerprint(second.output)


def test_performance_spans_and_view_size() -> None:
    pack = pack_clear_no_action()
    with active_trace("ASTRA_REASONING_V2") as trace:
        result = reason(pack, ScriptedProvider({pack.pack_id: output_clear_no_action()}))
    names = {item.name: item.category for item in trace.spans}
    for required in (
        "evidence_view_construction",
        "evidence_view_serialization",
        "provider_wait",
        "response_parsing",
        "grounding",
        "post_processing",
    ):
        assert required in names
    assert names["provider_wait"] == CAT_MODEL
    assert names["grounding"] == CAT_CPU
    assert result.audit.timings["serialize_s"] >= 0
    assert result.audit.input_stats["prompt_bytes"] > 0
    assert result.audit.input_stats["approx_input_tokens"] > 0
    serialized_nodes = result.audit.input_stats["serialized_node_count"]
    assert serialized_nodes <= len(pack.items)
    assert result.musical_writes == 0


def test_pack_compatibility_and_musical_writes_zero() -> None:
    pack = pack_clear_no_action()
    result = reason(pack, ScriptedProvider({pack.pack_id: output_clear_no_action()}))
    assert result.accepted is True
    assert result.musical_writes == 0
    assert result.diagnosis.status is DiagnosisStatus.NO_ACTION_REQUIRED
    assert "ALIGNMENT_LIMITED" in result.diagnosis.limitations


def test_ungrounded_compressor_claim_is_rejected() -> None:
    pack = pack_clear_no_action()
    output = output_clear_no_action()
    output.summary = (
        "The compressor is suppressing the synth by 6 dB even though that value is not measured."
    )
    report = validate_reasoning(output, pack)
    assert report.accepted is False


def test_real_project_pack_if_present_does_not_force_a_diagnosis() -> None:
    path = FROZEN_PACK if FROZEN_PACK.is_file() else LOG_PACK
    if not path.is_file():
        pytest.skip("no persisted Groove Rider evidence pack/view; truthful abstention path covered by fixtures")
    from copilot.audio.astra_external_reasoning_v1 import load_persisted_pack

    pack = load_persisted_pack(path)
    first = pack.items[0].evidence_id
    output = ReasoningOutput(
        category=FindingType.INSUFFICIENT_EVIDENCE,
        summary="Persisted facts do not force a mix diagnosis.",
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        confidence=Confidence.LOW,
        hypotheses=[
            GroundedHypothesis(
                claim="Current pack does not establish a mix diagnosis.",
                evidence_refs=[first],
                reasoning_summary="Read-only replay. Do not force a stronger diagnosis.",
                confidence=Confidence.LOW,
                alternatives_considered=["NO_ACTION_REQUIRED"],
                limitations=[item.code for item in pack.limitations],
                status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
                missing_evidence=["READ_DEVICE_PARAMETERS"],
            )
        ],
        evidence_refs=[first],
        contradicting_evidence_refs=[],
        limitations=[item.code for item in pack.limitations] or ["ALIGNMENT_LIMITED"],
        candidate_actions=[],
        requested_evidence=[
            EvidenceRequest(
                request_kind=EvidenceRequestKind.READ_DEVICE_PARAMETERS,
                why_needed="Smallest useful next fact if a quiet source still has MIDI.",
                target=pack.entities[0].name if pack.entities else "target source",
                region=pack.region,
                expected_information_gain="gain/dynamics if present",
                goal="DEVICE_CAUSAL_CONTEXT",
                required_evidence_kinds=["FACT"],
                priority="HIGH",
            )
        ],
        question="Does this persisted view warrant a mix change?",
        scope=pack.region,
    )
    graph = graph_from_pack(pack)
    view = graph.view()
    result = reason(pack, ScriptedProvider({pack.pack_id: output}), view=view)
    applied = apply_reasoning_result(result)
    assert result.musical_writes == 0
    if result.accepted:
        assert applied["status"] in {
            DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
            DiagnosisStatus.NO_ACTION_REQUIRED.value,
            DiagnosisStatus.WEAKLY_SUPPORTED.value,
            DiagnosisStatus.SUPPORTED.value,
            DiagnosisStatus.DIAGNOSIS_UNSTABLE.value,
        }
    else:
        assert applied["status"] == DiagnosisStatus.DIAGNOSIS_UNSTABLE.value
