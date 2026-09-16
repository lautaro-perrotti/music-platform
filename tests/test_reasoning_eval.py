from __future__ import annotations

from copilot.reasoning.eval import run_eval, scripted_provider, semantic_signature
from copilot.reasoning.fixtures import (
    ALL_PACKS,
    EXPECTED_ACTION_FAMILY,
    EXPECTED_CATEGORY,
    EXPECTED_STATUS,
    SCRIPTED_VALID,
    output_clear_spectral,
    pack_clear_no_action,
)
from copilot.reasoning.pipeline import reason
from copilot.reasoning.provider import ScriptedProvider
from copilot.schemas.diagnosis import DiagnosisStatus, FindingType


def test_synthetic_fixtures_a_through_f_accept_scripted_contract() -> None:
    provider = scripted_provider()
    for name, factory in SCRIPTED_VALID.items():
        result = reason(ALL_PACKS[name](), provider)
        assert result.accepted, (name, result.failure, result.audit.issues)
        assert result.output is not None
        assert result.output.status is EXPECTED_STATUS[name]
        assert result.output.category is EXPECTED_CATEGORY[name]
        family = {item.action_type for item in result.output.candidate_actions}
        assert family & EXPECTED_ACTION_FAMILY[name]
        assert result.musical_writes == 0
        del factory


def test_eval_harness_reports_separate_metrics() -> None:
    report = run_eval(scripted_provider(), repeats=3, include_real_model=False)
    assert report["scalar_score"] is None
    metrics = report["metrics"]
    assert metrics["accepted_core_fixtures"] == 6
    assert metrics["grounding_violation_rate"] == 0.0
    assert metrics["NO_ACTION_REQUIRED_usage"] >= 1
    assert metrics["correct_abstention"] >= 2
    assert metrics["diagnosis_stability"] == 1.0
    assert report["adversarial"]["injected_hallucination_rejected"] is True
    assert report["real_model"]["status"] == "NOT_RUN"
    assert report["musical_writes"] == 0
    assert report["human_label_schema"]
    assert "false_positive_diagnosis" in metrics
    assert "evidence_request_usefulness" in metrics


def test_same_input_semantic_stability() -> None:
    pack = pack_clear_no_action()
    provider = scripted_provider()
    signatures = [semantic_signature(reason(pack, provider)) for _ in range(5)]
    assert len(set(signatures)) == 1


def test_temporal_does_not_default_to_eq() -> None:
    result = reason(ALL_PACKS["CLEAR_TEMPORAL"](), scripted_provider())
    assert result.output is not None
    types = [item.action_type.value for item in result.output.candidate_actions]
    assert "REDUCE_LOW_BAND_ENERGY" not in types
    assert "SHORTEN_BASS_RELEASE" in types


def test_spectral_is_not_called_temporal() -> None:
    result = reason(ALL_PACKS["CLEAR_SPECTRAL"](), ScriptedProvider({"CLEAR_SPECTRAL": output_clear_spectral()}))
    assert result.output is not None
    assert result.output.category is FindingType.SPECTRAL_MASKING
    assert result.output.status is DiagnosisStatus.SUPPORTED


def test_insufficient_and_no_action_are_first_class() -> None:
    ambiguous = reason(ALL_PACKS["AMBIGUOUS"](), scripted_provider())
    no_action = reason(ALL_PACKS["CLEAR_NO_ACTION"](), scripted_provider())
    assert ambiguous.output is not None
    assert no_action.output is not None
    assert ambiguous.output.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert ambiguous.output.requested_evidence
    assert no_action.output.status is DiagnosisStatus.NO_ACTION_REQUIRED
    decisions = {item["request_kind"] for item in ambiguous.audit.request_decisions}
    assert "READ_MIDI" in decisions
    assert all(
        item["decision"] in {"ALLOWED", "ALREADY_CACHED", "UNSUPPORTED", "TOO_EXPENSIVE", "REDUNDANT"}
        for item in ambiguous.audit.request_decisions
    )


def test_reason_eval_cli_writes_report(tmp_path, monkeypatch, capsys) -> None:
    from copilot.cli import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    code = main(["reason-eval", "--log", str(tmp_path / "logs" / "copilot.log")])
    captured = capsys.readouterr().out
    report = __import__("json").loads(captured)
    assert code == 0
    assert report["musical_writes"] == 0
    assert report["metrics"]["accepted_core_fixtures"] == 6
    assert (tmp_path / "logs" / "reason_eval.json").is_file()
