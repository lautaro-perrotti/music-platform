from __future__ import annotations

from copilot.audio.capture_preflight import generic_capture_preflight


def test_generic_preflight_waives_only_dispensable_pista_expectations() -> None:
    report = {
        "pass": False,
        "missing": [
            "Drums track not found",
            "Sub Sub Bass track not found",
            "Main first device is 'EQ Eight', expected Patience Master",
            "TARGET_SOURCE_UNSUPPORTED: legacy kick pad route is not present",
        ],
    }

    result = generic_capture_preflight(report)

    assert result["pass"] is True
    assert result["missing"] == []
    assert result["waived_source_specific_checks"] == report["missing"]


def test_generic_preflight_preserves_identity_failure() -> None:
    report = {
        "pass": False,
        "missing": ["PROJECT_MISMATCH: original project is open"],
    }

    result = generic_capture_preflight(report)

    assert result is report
    assert result["pass"] is False


def test_generic_preflight_preserves_topology_failure() -> None:
    report = {
        "pass": False,
        "missing": ["Copilot Capture Audio has no Copilot Audio Tap"],
    }

    result = generic_capture_preflight(report)

    assert result is report
    assert result["pass"] is False


def test_legacy_waiver_never_masks_identity_or_topology_failure() -> None:
    report = {
        "pass": False,
        "missing": [
            "Drums track not found",
            "PROJECT_IDENTITY_MISMATCH",
            "duplicate tap slots: [1, 1]",
        ],
    }

    result = generic_capture_preflight(report)

    assert result is report
    assert result["pass"] is False
    assert result["missing"] == report["missing"]
