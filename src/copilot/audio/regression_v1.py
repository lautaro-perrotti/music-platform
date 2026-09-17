"""Supported-envelope regression suite. No live destructive writes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from copilot.human_eval.store import now_iso

MILESTONE = "REGRESSION_V1"
ARTIFACT = "regression_v1.json"
SUITE = (
    "tests/test_state_trust.py",
    "tests/test_reasoning_grounding.py",
    "tests/test_musicplan_v1.py",
    "tests/test_musicplan_gate.py",
    "tests/test_write_indoubt.py",
    "tests/test_journal_recovery.py",
    "tests/test_capture_journal_recovery.py",
    "tests/test_cross_project_bootstrap_v1.py",
    "tests/test_project_ready_v1.py",
    "tests/test_session_diagnose.py",
    "tests/test_generic_source_isolation_v1.py",
    "tests/test_evidence_pack_v1.py",
    "tests/test_producer_status_semantics.py",
    "tests/test_terminal_state_v1.py",
    "tests/test_doctor_v1.py",
    "tests/test_session_ready_v1.py",
    "tests/test_portability_cli_v1.py",
    "tests/test_cross_project_musical_validation_v1.py",
    "tests/test_project_folder_import_v1.py",
    "tests/test_track_monitoring_v1.py",
    "tests/test_m4l_runtime_v1.py",
    "tests/test_crash_recovery_injection_v1.py",
    "tests/test_external_post_mixer_capture_v1.py",
    "tests/test_astra_external_reasoning_v1.py",
    "tests/test_external_evidence_enrichment_v1.py",
    "tests/test_midi_read_only_v1.py",
    "tests/test_analyze_region_v1.py",
    "tests/test_active_source_region_analysis_v1.py",
    "tests/test_external_causal_context_v1.py",
    "tests/test_second_machine_installer_v1.py",
    "tests/test_macos_detect_v1.py",
    # test_detect_and_cli.py omitted: Live-environment sensitive (hits Ableton).
)


def run_regression_v1(*, evidence: Path, root: Path | None = None) -> dict[str, Any]:
    repo = root or Path(__file__).resolve().parents[3]
    rows: list[dict[str, Any]] = []
    for rel in SUITE:
        path = repo / rel
        if not path.is_file():
            rows.append({"target": rel, "status": "BLOCKED", "reason": "missing_test_file"})
            continue
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "-q"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        status = "PASS" if proc.returncode == 0 else "FAIL"
        rows.append(
            {
                "target": rel,
                "status": status,
                "returncode": proc.returncode,
                "tail": (proc.stdout + proc.stderr)[-400:],
            }
        )
    blocked = [row for row in rows if row["status"] == "BLOCKED"]
    failed = [row for row in rows if row["status"] == "FAIL"]
    passed = [row for row in rows if row["status"] == "PASS"]
    report = {
        "milestone": MILESTONE,
        "ts": now_iso(),
        "summary": {
            "PASS": len(passed),
            "FAIL": len(failed),
            "BLOCKED": len(blocked),
        },
        "overall": "PASS" if not failed and not blocked else "FAIL" if failed else "BLOCKED",
        "rows": rows,
        "NO LIVE DESTRUCTIVE WRITES": True,
        "note": "Does not run historical giant synthetic Astra campaigns.",
    }
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / ARTIFACT).write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return report
