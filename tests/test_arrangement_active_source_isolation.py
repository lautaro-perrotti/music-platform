"""Unit tests for ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1 (no Ableton)."""

from __future__ import annotations

from copilot.audio.arrangement_active_source_isolation import (
    ENGINEERING_REGION,
    VALIDATION_TARGETS,
    _signal_class,
    diagnose_fixed_kick_tap,
)
from copilot.schemas.session import MixerState, RoutingState, SessionState, TrackState


def test_engineering_region_is_320_352() -> None:
    assert ENGINEERING_REGION["start_qn"] == 320.0
    assert ENGINEERING_REGION["end_qn"] == 352.0


def test_validation_targets_include_rose_drums_sub() -> None:
    assert "Rose Bass" in VALIDATION_TARGETS
    assert "Drums" in VALIDATION_TARGETS
    assert "Sub Sub Bass" in VALIDATION_TARGETS
    assert "Kick 808 Deep" not in VALIDATION_TARGETS


def test_signal_class_silence() -> None:
    assert _signal_class(0.0, 0.0) == "SILENCE"
    assert _signal_class(0.05, 0.2) == "HAS_SIGNAL"


def test_fixed_kick_diagnostic_without_daw() -> None:
    session = SessionState(
        tracks=[
            TrackState(
                stable_id="trk_drums",
                index=2,
                name="Drums",
                role="midi",
                mixer=MixerState(),
                routing=RoutingState(),
                devices=[],
            )
        ]
    )
    preflight = {
        "capture_hosts": {
            "Copilot Capture": {
                "index": 15,
                "routing": {
                    "input_type": "Drums",
                    "input_channel": "Drum Rack | Kick 808 Deep | Post Mixer",
                    "output": "Sends Only",
                    "monitoring": "in",
                },
                "tap": {"device_on": 1.0, "rec": 0.0},
            }
        }
    }
    # diagnose_fixed_kick_tap only reads preflight/session — daw unused for facts
    out = diagnose_fixed_kick_tap(None, preflight=preflight, session=session)  # type: ignore[arg-type]
    assert out["status"] == "FIXED_TAP_NOT_REPRESENTATIVE"
    assert out["evidenced_cause"] == "wrong_sub_source_for_region_or_pad_inactive"
