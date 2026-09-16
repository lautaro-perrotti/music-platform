from __future__ import annotations

from pathlib import Path

from copilot.agent.journal import DurableJournal
from copilot.agent.recovery import RecoveryStatus, classify_journal
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.mock import MockAbletonAdapter


def test_journal_persists_planned_before_side_effect(tmp_path: Path) -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    journal = DurableJournal(tmp_path / "journal.jsonl")
    tools = AgentTools(daw, TransactionManager(daw, journal=journal))
    before = tools.get_session_snapshot()
    tools.transactions.begin("journaled", before)
    daw.fail_after_writes = 0
    try:
        tools.create_midi_track("AI Test")
    except Exception:
        tools.transactions.abort("injected")
    records = journal.read_all()
    planned = [
        item
        for item in records
        if item.get("status") == "PLANNED" and item.get("operation") == "create_midi_track"
    ]
    assert planned
    assert daw.snapshot().tracks == []


def test_crash_classifications(tmp_path: Path) -> None:
    journal = DurableJournal(tmp_path / "journal.jsonl")
    journal.append(
        {"transaction_id": "a", "kind": "write", "status": "PLANNED", "operation": "create_midi_track"}
    )
    journal.append(
        {"transaction_id": "b", "kind": "write", "status": "SENT", "operation": "create_midi_track"}
    )
    journal.append(
        {"transaction_id": "c", "kind": "write", "status": "APPLIED", "operation": "create_midi_track"}
    )
    journal.append(
        {"transaction_id": "d", "kind": "rollback", "status": "SENT", "operation": "delete_track"}
    )
    reports = {item["transaction_id"]: item for item in classify_journal(journal.read_all())}
    assert reports["a"]["recovery"] == RecoveryStatus.RECOVERY_REQUIRED.value
    assert reports["b"]["recovery"] == RecoveryStatus.IN_DOUBT.value
    assert reports["c"]["recovery"] == RecoveryStatus.RECOVERY_REQUIRED.value
    assert reports["d"]["recovery"] == RecoveryStatus.ROLLBACK_REQUIRED.value
    assert "Whether Live applied" in reports["b"]["unknown"]


def test_corrupt_trailing_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text('{"seq":1,"transaction_id":"x","status":"VERIFIED"}\n{truncated', encoding="utf-8")
    journal = DurableJournal(path)
    assert len(journal.read_all()) == 1
    journal.append({"transaction_id": "y", "status": "PLANNED"})
    assert all(isinstance(item.get("seq"), int) for item in journal.read_all())
