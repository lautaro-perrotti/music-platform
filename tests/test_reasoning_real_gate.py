from __future__ import annotations

from copilot.reasoning.eval import CORE_FIXTURES
from copilot.reasoning.fixtures import ALL_PACKS, SCRIPTED_VALID
from copilot.reasoning.pipeline import reason
from copilot.reasoning.provider import ScriptedProvider
from copilot.reasoning.real_gate import run_real_model_gate, score_real_model_runs, unavailable_report


def test_real_model_gate_stops_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("COPILOT_REASONING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("copilot.reasoning.real_gate.configured_http_provider", lambda: None)
    report = run_real_model_gate()
    assert report["status"] == "REAL_MODEL_UNAVAILABLE"
    assert report["runs"] == 0
    assert report["musical_writes"] == 0
    assert report["provider"] is None
    assert report["core_safety"]["result"] == "NOT_RUN"


def test_unavailable_report_does_not_claim_verified() -> None:
    report = unavailable_report(reason_text="missing key")
    assert report["status"] == "REAL_MODEL_UNAVAILABLE"
    assert "VERIFIED" not in report["core_safety"]["result"]


def test_scorecard_separates_core_safety_from_scripted_quality() -> None:
    """Scorecard unit test only. Does not count as real-model smoke."""
    provider = ScriptedProvider({name: factory() for name, factory in SCRIPTED_VALID.items()})
    raw = []
    for name in CORE_FIXTURES:
        pack = ALL_PACKS[name]()
        for _ in range(2):
            raw.append((name, reason(pack, provider)))
    report = score_real_model_runs(raw, repeats=2, close_grounding=True)
    assert report["musical_writes"] == 0
    assert report["core_safety"]["accepted_invented_measurements"] == 0
    assert report["core_safety"]["accepted_invented_entities"] == 0
    assert report["core_safety"]["accepted_forbidden_precision"] == 0
    assert report["abstention"]["HALLUCINATION_TRAP_rejected_or_abstained"] is True
    assert report["abstention"]["NO_ACTION_REQUIRED_permitted"] is True
    assert report["core_safety_result"] == "PASS"
    assert report["logical_evals"] == 12
    assert report["api_attempts"] >= 12
    assert report["REAL_MODEL_SEMANTIC_STABILITY"] == "ACCEPTABLE"
