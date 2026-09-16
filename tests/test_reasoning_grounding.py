from __future__ import annotations

import ast
from pathlib import Path

from copilot.reasoning.errors import ReasoningFailure
from copilot.reasoning.fixtures import (
    output_ambiguous_eq_name,
    output_clear_no_action,
    output_clear_temporal,
    output_hallucination_trap,
    output_microtiming,
    pack_ambiguous_entity,
    pack_clear_no_action,
    pack_clear_temporal,
    pack_hallucination_trap,
)
from copilot.reasoning.grounding import (
    cap_confidence,
    observation_fact_problems,
    validate_pack_facts,
    validate_reasoning,
)
from copilot.reasoning.pipeline import reason
from copilot.reasoning.prompt import build_prompt
from copilot.reasoning.provider import FailingProvider, ScriptedProvider, SequenceProvider
from copilot.reasoning.schema import GroundedHypothesis, ReasoningCandidate, ReasoningOutput
from copilot.schemas.diagnosis import CandidateActionType, Confidence, DiagnosisStatus, FindingType
from copilot.schemas.observation import (
    Claim,
    ClaimKind,
    MusicObservation,
    SignalFeatures,
)


def test_observation_rejects_interpretive_claims() -> None:
    obs = MusicObservation(
        source="test",
        region="0->8qn",
        timestamp="2026-09-15T00:00:00Z",
        signal=SignalFeatures(
            duration_seconds=1.0,
            sample_rate=44100,
            channels=2,
            rms=0.1,
            peak=0.2,
        ),
        claims=[
            Claim(kind=ClaimKind.MEASURED, name="kick_attack_count", value=8, unit="count"),
            Claim(kind=ClaimKind.HYPOTHESIS, name="mix", value="kick is weak"),
        ],
    )
    problems = observation_fact_problems(obs)
    assert any("MEASURED" in item for item in problems)
    assert any("kick is weak" in item for item in problems)


def test_prompt_is_vendor_neutral_and_includes_alignment() -> None:
    prompt = build_prompt(pack_clear_temporal())
    assert "GPT" not in prompt
    assert "Gemini" not in prompt
    assert "PACK_ID:CLEAR_TEMPORAL" in prompt
    assert "LIMITED ±52 ms" in prompt
    assert "NO_ACTION_REQUIRED is valid" in prompt
    assert "EQ last" in prompt


def test_valid_scripted_output_is_accepted() -> None:
    pack = pack_clear_no_action()
    result = reason(pack, ScriptedProvider({"CLEAR_NO_ACTION": output_clear_no_action()}))
    assert result.accepted is True
    assert result.failure is None
    assert result.musical_writes == 0
    assert result.diagnosis is not None
    assert result.diagnosis.status is DiagnosisStatus.NO_ACTION_REQUIRED
    assert result.diagnosis.user_facing.endswith("No hice cambios.")
    assert result.audit.prompt_version
    assert result.audit.output_hash
    assert "prompt_build_s" in result.audit.timings
    assert "model_s" in result.audit.timings
    assert "validation_s" in result.audit.timings


def test_confidence_cap_from_limited_alignment() -> None:
    output = output_clear_temporal()
    output.confidence = Confidence.HIGH
    capped = cap_confidence(output, pack_clear_temporal())
    assert capped is Confidence.MEDIUM


def test_hallucinated_numbers_and_entities_are_rejected() -> None:
    report = validate_reasoning(output_hallucination_trap(), pack_hallucination_trap())
    kinds = {issue.kind for issue in report.issues}
    assert ReasoningFailure.UNKNOWN_EVIDENCE_REF in kinds
    assert ReasoningFailure.UNKNOWN_ENTITY_REF in kinds
    assert report.accepted is False


def test_microtiming_under_limited_alignment_is_rejected() -> None:
    report = validate_reasoning(output_microtiming(), pack_clear_no_action())
    kinds = {issue.kind for issue in report.issues}
    assert ReasoningFailure.UNSUPPORTED_PRECISION in kinds or ReasoningFailure.LLM_GROUNDING_VIOLATION in kinds
    assert report.accepted is False


def test_ambiguous_entity_name_is_rejected() -> None:
    report = validate_reasoning(output_ambiguous_eq_name(), pack_ambiguous_entity())
    kinds = {issue.kind for issue in report.issues}
    assert ReasoningFailure.AMBIGUOUS_ENTITY_REFERENCE in kinds


def test_invalid_json_retries_then_fails() -> None:
    valid = output_clear_no_action()
    provider = SequenceProvider(["not-json", "{", valid])
    result = reason(pack_clear_no_action(), provider)
    assert provider.calls == 2
    assert result.accepted is False
    assert result.failure is ReasoningFailure.MODEL_OUTPUT_INVALID


def test_schema_retry_accepts_second_valid_payload() -> None:
    provider = SequenceProvider(["not-json", output_clear_no_action()])
    result = reason(pack_clear_no_action(), provider)
    assert result.accepted is True
    assert provider.calls == 2


def test_timeout_and_unavailable_fail_closed() -> None:
    timeout = reason(
        pack_clear_no_action(),
        FailingProvider(ReasoningFailure.MODEL_TIMEOUT, "deadline"),
    )
    assert timeout.accepted is False
    assert timeout.failure is ReasoningFailure.MODEL_TIMEOUT
    assert timeout.musical_writes == 0
    missing = reason(
        pack_clear_no_action(),
        FailingProvider(ReasoningFailure.MODEL_UNAVAILABLE, "down"),
    )
    assert missing.failure is ReasoningFailure.MODEL_UNAVAILABLE


def test_extra_keys_are_invalid() -> None:
    payload = output_clear_no_action().model_dump()
    payload["prose"] = "ignore me"
    result = reason(pack_clear_no_action(), ScriptedProvider({"CLEAR_NO_ACTION": __import__("json").dumps(payload)}))
    assert result.failure is ReasoningFailure.MODEL_OUTPUT_INVALID


def test_hypothesis_without_refs_is_rejected() -> None:
    output = output_clear_no_action()
    output.hypotheses[0].evidence_refs = []
    report = validate_reasoning(output, pack_clear_no_action())
    assert report.accepted is False
    assert any(issue.kind is ReasoningFailure.LLM_GROUNDING_VIOLATION for issue in report.issues)


def test_unknown_entity_id_is_rejected() -> None:
    output = output_clear_no_action()
    output.entity_refs = ["device:serum"]
    report = validate_reasoning(output, pack_clear_no_action())
    assert any(issue.kind is ReasoningFailure.UNKNOWN_ENTITY_REF for issue in report.issues)


def test_unknown_evidence_ids_always_rejected() -> None:
    pack = pack_clear_no_action()
    for suffix in range(12):
        output = output_clear_no_action()
        bad = f"ev.unknown_{suffix}"
        output.evidence_refs = [bad]
        output.hypotheses[0].evidence_refs = [bad]
        report = validate_reasoning(output, pack)
        assert report.accepted is False
        assert any(issue.kind is ReasoningFailure.UNKNOWN_EVIDENCE_REF for issue in report.issues)


def test_quoted_missing_measurement_is_rejected() -> None:
    output = output_clear_no_action()
    output.summary = "bass has 7 collisions and energy peaks at 63 Hz"
    report = validate_reasoning(output, pack_clear_no_action())
    assert any(issue.kind is ReasoningFailure.LLM_GROUNDING_VIOLATION for issue in report.issues)


def test_alignment_limitation_10ms_quote_is_capability_not_measurement() -> None:
    output = output_clear_no_action()
    output.summary = f"{output.summary} 10 ms precision is unsupported."
    output.limitations = [*output.limitations, "Microtiming 5-10 ms is not supported."]
    report = validate_reasoning(output, pack_clear_no_action())
    assert report.accepted is True


def test_invented_10ms_overlap_is_still_rejected() -> None:
    output = output_clear_no_action()
    output.summary = f"Bass overlap of 10 ms masks the kick. {output.summary}"
    report = validate_reasoning(output, pack_clear_no_action())
    assert report.accepted is False
    assert any("ungrounded duration 10 ms" in issue.detail for issue in report.issues)


def test_prompt_limitations_do_not_leak_capability_fields() -> None:
    prompt = build_prompt(pack_clear_temporal())
    assert "limitation_id" not in prompt
    assert "capability_ms" not in prompt
    assert '"code": "ALIGNMENT_LIMITED"' in prompt


def test_pack_facts_reject_interpretive_evidence() -> None:
    pack = pack_clear_no_action()
    pack.items[0].value = "bass is muddy"
    assert validate_pack_facts(pack)


def test_reasoning_package_does_not_import_daw() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "copilot" / "reasoning"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("copilot.daw"), path.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("copilot.daw"), path.name


def test_reasoning_package_has_no_transitive_daw_import() -> None:
    """Importing reasoning must not load Ableton TCP implementation."""
    import importlib
    import subprocess
    import sys

    code = (
        "import importlib, sys\n"
        "importlib.import_module('copilot.reasoning.session_astra')\n"
        "importlib.import_module('copilot.reasoning.from_dsp')\n"
        "importlib.import_module('copilot.reasoning.from_fullmix')\n"
        "importlib.import_module('copilot.reasoning.grounding')\n"
        "daw=[n for n in sys.modules if n.startswith('copilot.daw')]\n"
        "raise SystemExit(0 if not daw else 1)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**dict(**{k: v for k, v in __import__('os').environ.items()}), "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_invented_timecode_is_rejected() -> None:
    output = output_clear_no_action()
    output.summary = f"Dropout at 01:23.456 is definitive. {output.summary}"
    report = validate_reasoning(output, pack_clear_no_action())
    assert report.accepted is False
    assert any("ungrounded time position" in issue.detail for issue in report.issues)


def test_invented_qn_position_is_rejected() -> None:
    output = output_clear_no_action()
    output.summary = f"Silence begins at qn 123.45 without support. {output.summary}"
    report = validate_reasoning(output, pack_clear_no_action())
    assert report.accepted is False
    assert any("ungrounded time position" in issue.detail for issue in report.issues)


def test_invented_seconds_position_is_rejected() -> None:
    output = output_clear_no_action()
    output.summary = f"Event at 83.456 s is the drop. {output.summary}"
    report = validate_reasoning(output, pack_clear_no_action())
    assert report.accepted is False
    assert any("ungrounded time position" in issue.detail for issue in report.issues)


def test_valid_measured_timestamp_is_accepted() -> None:
    from copilot.schemas.evidence import (
        CaptureQuality,
        EvidenceItem,
        EvidenceKind,
        EvidencePack,
        ObservationLimitation,
    )

    pack = pack_clear_no_action().model_copy(deep=True)
    pack.items = [
        *list(pack.items),
        EvidenceItem(
            evidence_id="ev.event.t0",
            kind=EvidenceKind.MEASUREMENT,
            source_ref="test",
            region=pack.region,
            analysis_version="test",
            name="event_start_s",
            value=83.456,
            unit="s",
            quality=CaptureQuality.OK,
        ),
    ]
    output = output_clear_no_action()
    output.summary = f"{output.summary} Measured event onset is 83.456 s."
    output.evidence_refs = list(dict.fromkeys([*output.evidence_refs, "ev.event.t0"]))
    output.hypotheses[0].evidence_refs = list(
        dict.fromkeys([*output.hypotheses[0].evidence_refs, "ev.event.t0"])
    )
    report = validate_reasoning(output, pack)
    assert report.accepted is True, [(i.kind, i.detail) for i in report.issues]


def test_valid_qn_evidence_time_is_accepted() -> None:
    from copilot.schemas.evidence import CaptureQuality, EvidenceItem, EvidenceKind

    pack = pack_clear_no_action().model_copy(deep=True)
    pack.items = [
        *list(pack.items),
        EvidenceItem(
            evidence_id="ev.event.qn",
            kind=EvidenceKind.MEASUREMENT,
            source_ref="test",
            region=pack.region,
            analysis_version="test",
            name="event_start_qn",
            value=33.878,
            unit="qn",
            quality=CaptureQuality.OK,
        ),
    ]
    output = output_clear_no_action()
    output.summary = f"{output.summary} Gap starts at qn 33.878."
    output.evidence_refs = list(dict.fromkeys([*output.evidence_refs, "ev.event.qn"]))
    output.hypotheses[0].evidence_refs = list(
        dict.fromkeys([*output.hypotheses[0].evidence_refs, "ev.event.qn"])
    )
    report = validate_reasoning(output, pack)
    assert report.accepted is True, [(i.kind, i.detail) for i in report.issues]


def test_candidate_actions_are_not_ableton_commands() -> None:
    output = ReasoningOutput(
        category=FindingType.NO_ACTION_REQUIRED,
        summary="Overlap exists (2 of 8 kick attacks) but kick transient remains 0.42.",
        status=DiagnosisStatus.NO_ACTION_REQUIRED,
        confidence=Confidence.MEDIUM,
        hypotheses=[
            GroundedHypothesis(
                claim="No change.",
                evidence_refs=["ev.kick.count"],
                reasoning_summary="See evidence.",
                confidence=Confidence.MEDIUM,
                alternatives_considered=["TEMPORAL_MASKING"],
                contradicting_evidence_refs=["ev.overlap.median"],
            )
        ],
        evidence_refs=["ev.kick.count"],
        contradicting_evidence_refs=["ev.overlap.median"],
        limitations=["ALIGNMENT_LIMITED"],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.NO_CHANGE,
                target="mix",
                reason="set_device_parameter release=0.23",
                expected_effect="none",
                risk="none",
                evidence_refs=["ev.kick.count"],
            )
        ],
    )
    report = validate_reasoning(output, pack_clear_no_action())
    assert report.accepted is False


def test_persist_window_range_is_session_state_not_ungrounded_measurement() -> None:
    """Citing ev.persist.window bounds next to persist/decay language must not reject."""
    from copilot.schemas.evidence import (
        CaptureQuality,
        EvidenceItem,
        EvidenceKind,
        EvidencePack,
        ObservationLimitation,
    )

    pack = EvidencePack(
        pack_id="WINDOW_FACT",
        analysis_version="test",
        prompt_schema_version="test",
        region="32->64qn",
        project_token="p",
        audible_token="a",
        items=[
            EvidenceItem(
                evidence_id="ev.kick.count",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="test",
                region="32->64qn",
                analysis_version="test",
                name="kick_attack_count",
                value=4,
                unit="count",
                quality=CaptureQuality.OK,
            ),
            EvidenceItem(
                evidence_id="ev.persist.energy",
                kind=EvidenceKind.MEASUREMENT,
                source_ref="test",
                region="32->64qn",
                analysis_version="test",
                name="median_bass_energy_50_200ms_after_kick",
                value=0.0,
                quality=CaptureQuality.OK,
            ),
            EvidenceItem(
                evidence_id="ev.persist.window",
                kind=EvidenceKind.FACT,
                source_ref="test",
                region="32->64qn",
                analysis_version="test",
                name="persist_analysis_window_ms",
                value=[50.0, 200.0],
                unit="ms",
                quality=CaptureQuality.OK,
            ),
        ],
        limitations=[
            ObservationLimitation(
                code="ALIGNMENT_LIMITED",
                detail="ALIGNMENT_LIMITED ±52 ms",
                precision_ms=52.0,
                capability_ms=[52.0],
            )
        ],
    )
    output = ReasoningOutput(
        category=FindingType.ENERGY_STRUCTURE,
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        confidence=Confidence.LOW,
        summary="Main events observed; cause unresolved.",
        hypotheses=[
            GroundedHypothesis(
                claim="Cause of Main low-energy events is unresolved.",
                evidence_refs=["ev.persist.energy", "ev.persist.window"],
                reasoning_summary="Measure-only.",
                confidence=Confidence.LOW,
                alternatives_considered=["intentional rest"],
                contradicting_evidence_refs=[],
            )
        ],
        evidence_refs=["ev.persist.energy", "ev.persist.window"],
        contradicting_evidence_refs=[],
        limitations=[
            "ev.persist.energy is normalized energy in the 50.0–200.0 ms analysis window "
            "defined by ev.persist.window, not a measured decay duration.",
        ],
        candidate_actions=[
            ReasoningCandidate(
                action_type=CandidateActionType.NO_CHANGE,
                target="mix",
                reason="Insufficient causal evidence.",
                expected_effect="none",
                risk="none",
                evidence_refs=["ev.persist.energy"],
            )
        ],
    )
    report = validate_reasoning(output, pack)
    assert report.accepted is True, [(i.kind, i.detail) for i in report.issues]
    assert not any("ungrounded duration" in i.detail for i in report.issues)
