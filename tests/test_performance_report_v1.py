"""PERFORMANCE_REPORT_V1 CLI contract.

The report is part of the supported envelope, so it must be read-only, must not
require Ableton, and must not invent statistical confidence it does not have.
"""

from __future__ import annotations

import json

from copilot.cli import CANONICAL_COMMANDS, main
from copilot.perf.report import _estimate_quantum, _stats, render_summary


def test_offline_report_runs_without_ableton(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(["performance-report", "--offline", "--repeats", "2"])
    captured = capsys.readouterr().out
    assert code == 0
    assert "PERFORMANCE_REPORT_V1" in captured
    artifact = tmp_path / "logs" / "performance_report_v1.json"
    assert artifact.is_file()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["READ ONLY"] is True
    assert payload["MUSICAL WRITES"] == 0
    assert payload["ableton"]["status"] == "SKIPPED"
    assert payload["operation_id"].startswith("op_")


def test_report_is_in_the_canonical_envelope() -> None:
    assert "performance-report" in CANONICAL_COMMANDS


def test_small_samples_do_not_claim_a_p95() -> None:
    row = _stats([0.1, 0.2, 0.3])
    assert row["n"] == 3
    assert row["p95_s"] is None
    assert "sample too small" in row["p95_note"]
    wide = _stats([i / 100 for i in range(40)])
    assert wide["p95_s"] is not None


def test_quantum_detection_needs_separable_clusters() -> None:
    assert _estimate_quantum([0.1, 0.1]) == {
        "detected": False,
        "reason": "too few samples",
    }
    flat = _estimate_quantum([0.10, 0.101, 0.102, 0.103, 0.104, 0.105])
    assert flat["detected"] is False
    ticked = _estimate_quantum(
        [0.213, 0.214, 0.213, 0.320, 0.321, 0.320, 0.427, 0.426, 0.427]
    )
    assert ticked["detected"] is True
    assert 0.10 < ticked["quantum_s"] < 0.115


def test_summary_survives_a_blocked_section() -> None:
    text = render_summary(
        {
            "ableton": {"status": "BLOCKED", "reason": "LIVE_UNAVAILABLE"},
            "model_pipeline": {"status": "BLOCKED"},
            "local_costs": {"status": "BLOCKED"},
        }
    )
    assert "BLOCKED" in text
