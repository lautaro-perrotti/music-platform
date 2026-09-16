"""Capture journal recovery — append-only terminalization."""

from __future__ import annotations

from pathlib import Path

from copilot.audio.capture_journal_recovery import (
    recover_stale_capture_journal,
    unresolved_capture_journals,
)
from copilot.audio.tap_trust import PREPARED, RECORDING, RECOVERED, CaptureJournal


def test_recover_stale_recording_appends_terminal(tmp_path: Path) -> None:
    journal = CaptureJournal("abc123deadbe", directory=tmp_path)
    journal.record(PREPARED, run=1)
    journal.record(RECORDING)
    first = recover_stale_capture_journal("abc123deadbe", directory=tmp_path)
    assert first["appended"] is True
    assert first["terminal"] == RECOVERED
    rows = CaptureJournal("abc123deadbe", directory=tmp_path).journal.read_all()
    assert rows[0]["status"] == PREPARED
    assert rows[1]["status"] == RECORDING
    assert rows[-1]["status"] == RECOVERED
    assert rows[-1].get("recovery") is True


def test_recovery_idempotent(tmp_path: Path) -> None:
    journal = CaptureJournal("deadbeefcafe", directory=tmp_path)
    journal.record(PREPARED)
    journal.record(RECORDING)
    recover_stale_capture_journal("deadbeefcafe", directory=tmp_path)
    second = recover_stale_capture_journal("deadbeefcafe", directory=tmp_path)
    assert second["status"] == "ALREADY_TERMINAL"
    assert second["idempotent"] is True
    assert second["appended"] is False
    statuses = [
        row["status"]
        for row in CaptureJournal("deadbeefcafe", directory=tmp_path).journal.read_all()
    ]
    assert statuses.count(RECOVERED) == 1
    assert unresolved_capture_journals(directory=tmp_path) == []
