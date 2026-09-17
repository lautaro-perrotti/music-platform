from copilot.audio.producer_analyze_v1 import (
    gate_read_only,
    observations_from_captures,
    preserve_status,
)
from copilot.audio.source_capture_pool_v1 import review_existing_capture, select_idle_host
from copilot.schemas.session import SessionState, TrackState


def test_statuses_are_never_collapsed() -> None:
    assert preserve_status("WEAKLY_SUPPORTED") == "WEAKLY_SUPPORTED"
    assert preserve_status("SUPPORTED") == "SUPPORTED"
    assert preserve_status("INSUFFICIENT_EVIDENCE") == "INSUFFICIENT_EVIDENCE"
    assert preserve_status("NO_ACTION_REQUIRED") == "NO_ACTION_REQUIRED"
    assert preserve_status("DIAGNOSIS_UNSTABLE") == "DIAGNOSIS_UNSTABLE"
    assert preserve_status("ACTION_NOT_AVAILABLE") == "ACTION_NOT_AVAILABLE"


def test_gate_preserves_weakly_supported() -> None:
    gate = gate_read_only(diagnosis_status="WEAKLY_SUPPORTED", diagnosis_accepted=True)
    assert gate["milestone_status"] == "WEAKLY_SUPPORTED"
    assert gate["decision"] == "ABSTAIN"
    assert gate["NO WRITE"] is True
    assert gate["proceed_to_write"] is False


def test_gate_ie_with_no_change_stays_ie() -> None:
    """Never collapse INSUFFICIENT_EVIDENCE into NO_ACTION_REQUIRED."""
    gate = gate_read_only(
        diagnosis_status="INSUFFICIENT_EVIDENCE",
        diagnosis_accepted=False,
        candidate_actions=[{"action_type": "NO_CHANGE"}],
    )
    assert gate["milestone_status"] == "INSUFFICIENT_EVIDENCE"
    assert gate["NO WRITE"] is True


def test_gate_supported_volume_is_read_only() -> None:
    gate = gate_read_only(
        diagnosis_status="SUPPORTED",
        diagnosis_accepted=True,
        category="LEVEL_IMBALANCE",
        candidate_actions=[{"action_type": "SET_TRACK_VOLUME"}],
    )
    assert gate["milestone_status"] == "SUPPORTED"
    assert gate["write_justified_if_autonomous"] is True
    assert gate["NO WRITE"] is True
    assert gate["proceed_to_write"] is False


def test_gate_supported_eq_is_action_not_available() -> None:
    gate = gate_read_only(
        diagnosis_status="SUPPORTED",
        diagnosis_accepted=True,
        category="SPECTRAL_MASKING",
        candidate_actions=[{"action_type": "REDUCE_LOW_BAND_ENERGY"}],
    )
    assert gate["milestone_status"] == "ACTION_NOT_AVAILABLE"


def test_source_capture_pool_review() -> None:
    review = review_existing_capture()
    assert review["SOURCE_CAPTURE_POOL"] == "IMPLEMENTED"
    assert review["rewrote_frozen_v1"] is False
    session = SessionState(
        tracks=[
            TrackState(stable_id="h", index=4, name="Copilot Capture Bass", role="audio")
        ]
    )
    host = select_idle_host(session)
    assert host["name"] == "Copilot Capture Bass"
    assert host["slot"] == 2


def test_observations_from_captures_reports_missing_main() -> None:
    derived = observations_from_captures(
        [{"ok": False, "error": "x"}],
        region_id="R",
        start_qn=0.0,
        end_qn=32.0,
        tempo=120.0,
    )
    assert derived["main_capture"] is None
    assert any(row["code"] == "MAIN_CAPTURE_MISSING" for row in derived["extra_limitations"])

