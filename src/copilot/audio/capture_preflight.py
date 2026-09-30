"""Adapt the historical source-specific session check for generic track capture."""

from __future__ import annotations

from typing import Any


def generic_capture_preflight(report: dict[str, Any]) -> dict[str, Any]:
    """Waive only named pista-specific assumptions in generic track capture.

    The capture primitive verifies and restores routing for each target. Project
    identity, host topology, working-copy and transport failures remain blocking.
    """
    if report.get("pass"):
        return report
    missing = list(report.get("missing") or [])
    from copilot.audio.session_diagnose import BASS_TARGET

    legacy_project_checks = (
        "TARGET_SOURCE_UNSUPPORTED:",
        "Copilot Capture Bass routing claim=",
        "Copilot Capture Bass source is Bassline chain;",
        f"{BASS_TARGET} track not found",
        "Drums track not found",
    )
    if not missing or any(
        not item.startswith(legacy_project_checks)
        and not (item.startswith("Main first device is ") and "expected Patience Master" in item)
        for item in missing
    ):
        return report
    adapted = dict(report)
    adapted["pass"] = True
    adapted["status"] = "GENERIC_TRACK_CAPTURE_READY"
    adapted["waived_source_specific_checks"] = missing
    adapted["missing"] = []
    adapted["instruction"] = "Generic track capture may proceed; each target routing is verified and restored per capture."
    return adapted
