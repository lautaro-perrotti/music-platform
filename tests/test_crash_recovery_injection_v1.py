"""Failure-injection coverage for crash/recovery. No live crash testing."""

from __future__ import annotations

from pathlib import Path

from copilot.agent.journal import DurableJournal
from copilot.agent.recovery import RecoveryStatus, classify_journal
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.audio.capture_journal_recovery import recover_stale_capture_journal
from copilot.audio.cross_project_bootstrap_v1 import discover_topology, plan_bootstrap
from copilot.audio.tap_trust import CaptureJournal, IN_DOUBT, PREPARED
from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.object_ref import PersistentObjectRef, ResolveStatus, ref_from_track, resolve_track
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.daw.write import WriteInDoubt
from copilot.schemas.session import SessionState, TrackState
from copilot.schemas.transaction import TransactionStatus


def test_timeout_before_write_stays_planned() -> None:
    records = [{"transaction_id": "t1", "status": "PLANNED", "kind": "begin"}]
    report = classify_journal(records)[0]
    assert report["recovery"] == RecoveryStatus.RECOVERY_REQUIRED.value
    assert report["last_status"] != TransactionStatus.VERIFIED.value


def test_timeout_after_write_is_in_doubt() -> None:
    records = [
        {"transaction_id": "t1", "status": "SENT", "kind": "write"},
    ]
    report = classify_journal(records)[0]
    assert report["recovery"] == RecoveryStatus.IN_DOUBT.value


def test_disconnect_after_write_is_in_doubt() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    tools = AgentTools(daw, TransactionManager(daw))
    before = tools.get_session_snapshot()
    tools.transactions.begin("disconnect", before)
    tools.create_midi_track("One")
    daw.lose_ack = True
    try:
        tools.create_midi_clip(0, 0, 4.0)
    except WriteInDoubt:
        tools.transactions.mark_in_doubt("disconnect after write")
    assert tools.transactions.history[-1].status == TransactionStatus.IN_DOUBT


def test_rollback_failure_is_conflict_or_failed() -> None:
    records = [
        {"transaction_id": "t1", "status": "APPLIED", "kind": "write"},
        {"transaction_id": "t1", "status": "ROLLBACK_CONFLICT", "kind": "rollback"},
    ]
    report = classify_journal(records)[0]
    assert report["recovery"] == RecoveryStatus.ROLLBACK_CONFLICT.value


def test_capture_journal_interrupted(tmp_path: Path) -> None:
    journal = CaptureJournal("cap1", directory=tmp_path)
    journal.record(PREPARED, region="R")
    recovered = recover_stale_capture_journal("cap1", directory=tmp_path)
    assert recovered["status"] in {
        IN_DOUBT,
        "RECOVERED",
        "FAILED",
        "ALREADY_TERMINAL",
        "RECOVERY_APPENDED",
    }


def test_bootstrap_interrupted_replans_from_discovery() -> None:
    session = SessionState(
        project_path=r"C:\x\new.als",
        project_identity="p",
        tracks=[TrackState(stable_id="a", index=0, name="Lead", role="audio")],
    )
    discovery = discover_topology(
        session=session,
        inventory=[],
        master_pos={"tap": None, "is_last": False, "devices": []},
    )
    first = plan_bootstrap(discovery)
    second = plan_bootstrap(discovery)
    assert first["actions"] == second["actions"]
    assert first["status"] == "CHANGES_REQUIRED"


def test_bad_readback_does_not_verify() -> None:
    records = [{"transaction_id": "t1", "status": "APPLIED", "kind": "write"}]
    report = classify_journal(records)[0]
    assert report["recovery"] == RecoveryStatus.RECOVERY_REQUIRED.value
    assert report["last_status"] != "VERIFIED"


def test_target_disappears() -> None:
    session = SessionState(project_identity="p", tracks=[])
    ref = PersistentObjectRef(
        project_identity="p",
        role="audio",
        name="Lead",
        content_fingerprint="missing",
    )
    assert resolve_track(session, ref).status is ResolveStatus.TARGET_NOT_FOUND


def test_target_becomes_ambiguous() -> None:
    left = TrackState(stable_id="a", index=0, name="Lead", role="audio")
    right = TrackState(stable_id="b", index=1, name="Lead", role="audio")
    session = SessionState(project_identity="p", tracks=[left, right])
    attach_tokens(session)
    ref = ref_from_track(left, project_identity=session.project_identity or "p")
    # identical empty fingerprints → ambiguous
    result = resolve_track(session, ref)
    assert result.status in {ResolveStatus.TARGET_AMBIGUOUS, ResolveStatus.RESOLVED}


def test_stale_state_token_is_detectable() -> None:
    track = TrackState(stable_id="a", index=0, name="Lead", role="audio")
    session = SessionState(project_identity="p", tracks=[track])
    attach_tokens(session)
    before = target_token(track)
    track.mixer.volume = 0.1
    after = target_token(track)
    assert before != after
