"""Adapt the historical source-specific session check for generic track capture."""

from __future__ import annotations

from typing import Any


def generic_capture_preflight(report: dict[str, Any]) -> dict[str, Any]:
    """Waive only the legacy Kick/Bass routing checks.

    The capture primitive verifies and restores routing for each target. Project
    identity, host topology, working-copy and transport failures remain blocking.
    """
    if report.get("pass"):
        return report
    missing = list(report.get("missing") or [])
    legacy_routing = (
        "TARGET_SOURCE_UNSUPPORTED:",
        "Copilot Capture Bass routing claim=",
    )
    if not missing or any(not item.startswith(legacy_routing) for item in missing):
        return report
    adapted = dict(report)
    adapted["pass"] = True
    adapted["status"] = "GENERIC_TRACK_CAPTURE_READY"
    adapted["waived_source_specific_checks"] = missing
    adapted["missing"] = []
    adapted["instruction"] = "Generic track capture may proceed; each target routing is verified and restored per capture."
    return adapted
