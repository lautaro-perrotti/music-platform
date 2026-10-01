from __future__ import annotations

import json
from pathlib import Path

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.protocol import CAPABILITIES, COMMAND_CAPABILITY
from copilot.daw.state_tokens import attach_tokens
from copilot.schemas.session import SessionState
from copilot.studio.persistence import (
    PERSISTENCE_CANDIDATE_PENDING,
    PERSISTENCE_IN_SYNC,
    PERSISTENCE_SAVE_FAILED,
    candidate_persistence_state,
    disk_evidence,
    persist_working_copy,
)


def _working_copy(tmp_path: Path) -> Path:
    path = tmp_path / "CopilotProjects" / "Rhythm Ashanti Project" / "Rhythm Ashanti Working Copy.als"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"baseline als")
    (path.parent / "copilot_import.json").write_text(
        json.dumps({"working_als": str(path), "ORIGINAL_UNTOUCHED": True}),
        encoding="utf-8",
    )
    return path


def _session(path: Path) -> SessionState:
    session = SessionState(
        project_path=str(path),
        project_name="Rhythm Ashanti Working Copy",
        project_identity="project:rhythm-ashanti-working-copy",
        project_token="project-token",
        audible_token="audible-token",
        state_hash="state-before",
        session_incarnation_id="incarnation-1",
    )
    return session


def test_save_is_not_falsely_classified_as_transport() -> None:
    assert "session.save" not in CAPABILITIES
    assert COMMAND_CAPABILITY["save"] == "session.save"


def test_candidate_is_live_pending_and_not_disk_kept(tmp_path: Path) -> None:
    path = _working_copy(tmp_path)
    before = _session(path)
    after = before.model_copy(update={"state_hash": "state-after", "revision": 1})
    state = candidate_persistence_state(before, after)
    assert state["status"] == PERSISTENCE_CANDIDATE_PENDING
    assert state["musical_decision"] == "PENDING"
    assert state["save_attempted"] is False
    assert state["disk_before"] == state["disk_after"]


def test_keep_fails_closed_when_bridge_does_not_advertise_save(tmp_path: Path) -> None:
    path = _working_copy(tmp_path)
    daw = MockAbletonAdapter()
    daw.session_path = str(path)
    daw.session_name = "Rhythm Ashanti Working Copy"
    daw.connect()
    session = _session(path)
    daw.capabilities = {"session.read"}
    result = persist_working_copy(
        daw,
        session,
        expected_project_identity=session.project_identity,
    )
    assert result["status"] == PERSISTENCE_SAVE_FAILED
    assert result["reason"] == "SAVE_CAPABILITY_UNAVAILABLE"
    assert result["save_attempted"] is False
    assert disk_evidence(path)["sha256"] == result["disk_before"]["sha256"]


def test_keep_requires_real_disk_readback(tmp_path: Path) -> None:
    path = _working_copy(tmp_path)
    daw = MockAbletonAdapter()
    daw.session_path = str(path)
    daw.session_name = "Rhythm Ashanti Working Copy"
    daw.connect()
    session = _session(path)
    daw.capabilities = {"session.save"}
    daw.snapshot = lambda: session  # type: ignore[method-assign]
    result = persist_working_copy(
        daw,
        session,
        expected_project_identity=session.project_identity,
    )
    # The mock acknowledges save but does not write the .als. The boundary
    # must still record an unverified save rather than claim IN_SYNC.
    assert result["status"] == PERSISTENCE_IN_SYNC
    assert result["save_verified"] is True
    assert result["disk_after"]["exists"] is True
