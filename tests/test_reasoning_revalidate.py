from __future__ import annotations

from pathlib import Path

from copilot.reasoning.revalidate import run_m13


def test_m13_does_not_call_a_model_and_separates_false_positive() -> None:
    source = Path("logs/reason_real_full.json")
    report = run_m13(source=source)
    assert report["model_calls"] == 0
    assert report["previous_accepted"] == 17
    assert report["previous_rejected"] == 13
    assert report["sole_10ms_rejections"] == 13
    assert report["VALIDATOR_FALSE_POSITIVE"] == "YES"
    assert report["CLEAR_TEMPORAL_EVIDENCE"] == "SUFFICIENT"
    assert report["CLEAR_SPECTRAL_EVIDENCE"] == "SUFFICIENT"
    assert report["STATUS_CONTRACT"] == "AMBIGUOUS"
    assert report["MODEL_QUALITY"] == "PARTIAL"
    assert report["CORE_SAFETY_AFFECTED"] == "NO"
    probes = report["validator_probes"]
    for name, row in probes.items():
        assert row["scripted_baseline_accepted"] is True, name
        assert row["capability_limit_10ms_accepted"] is True, name
        assert row["invented_overlap_10ms_accepted"] is False, name
    temporal = report["fixture_summary"]["CLEAR_TEMPORAL"]
    assert temporal["adjudication_counts"].get("OVER_ABSTAIN") == 5
    spectral = report["fixture_summary"]["CLEAR_SPECTRAL"]
    assert spectral["adjudication_counts"].get("WRONG_CATEGORY") == 4
    assert spectral["adjudication_counts"].get("OVER_ABSTAIN") == 1
    assert report["hypothesis_identification"]["CLEAR_TEMPORAL"]["hits"] == 5
    assert report["hypothesis_identification"]["CLEAR_SPECTRAL"]["hits"] == 1
