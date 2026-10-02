"""Reconcile one known failed browser load against authoritative Live state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from copilot.agent.journal import DurableJournal
from copilot.agent.recovery import classify_journal
from copilot.agent.transactions import TransactionManager
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.write import ReconcileResult
from copilot.importing.ableton_launcher_v1 import paths_match
from copilot.importing.working_copy_manager_v1 import is_copilot_working_copy


def run(working_als: Path, journal_path: Path, transaction_id: str) -> dict:
    if not is_copilot_working_copy(working_als):
        raise RuntimeError("WORKING_COPY_REQUIRED")
    journal = DurableJournal(journal_path)
    events = [row for row in journal.read_all() if row.get("transaction_id") == transaction_id]
    if not events or events[-1].get("status") != "IN_DOUBT":
        raise RuntimeError("EXPECTED_ONE_IN_DOUBT_TRANSACTION")
    prepared = [row for row in events if row.get("operation") == "load_browser_item" and row.get("status") == "PLANNED"]
    if len(prepared) != 1:
        raise RuntimeError("EXPECTED_ONE_PREPARED_BROWSER_LOAD")
    plan = prepared[0]
    if plan.get("target_name_at_apply") != "LAB_KICK_SIMPLER" or plan.get("expected_after", {}).get("sample_uri") != "Samples/kick-deep-beater.wav":
        raise RuntimeError("UNEXPECTED_BROWSER_LOAD_TARGET")
    daw = AbletonTcpAdapter()
    daw.connect()
    try:
        if not paths_match(daw.get_session_path().get("path"), working_als):
            raise RuntimeError("PROJECT_MISMATCH")
        session = daw.snapshot()
        matches = [track for track in session.tracks if track.name == "LAB_KICK_SIMPLER"]
        if len(matches) != 1:
            raise RuntimeError("TARGET_AMBIGUOUS_OR_MISSING")
        track = matches[0]
        if track.devices or len(track.clips) != 1 or track.clips[0].sample_uri:
            raise RuntimeError("POST_STATE_NOT_KNOWN_ABSENT")
        if not any(row.get("kind") == "write" and row.get("status") == "SENT" for row in events):
            raise RuntimeError("COMMAND_NOT_MARKED_SENT")
        observed = TransactionManager(daw).reconcile(
            operation="load_browser_item",
            before=plan["before"],
            expected_after=plan["expected_after"],
            session=session,
        )
        if observed is not ReconcileResult.ABSENT:
            raise RuntimeError(f"RECONCILIATION_NOT_ABSENT: {observed}")
        terminal = journal.append({
            "transaction_id": transaction_id,
            "kind": "reconcile",
            "status": "FAILED",
            "operation": "load_browser_item",
            "reason": "Remote returned browser relative path not found; authoritative readback proves absent",
            "working_copy": str(working_als.resolve()),
            "project_identity": session.project_identity,
            "target_name": track.name,
            "target_index": track.index,
            "observed_device_count": len(track.devices),
            "observed_clip_sample_uri": track.clips[0].sample_uri,
            "reconciliation": observed.value,
        })
        recovery = next(row for row in classify_journal(journal.read_all()) if row["transaction_id"] == transaction_id)
        return {"terminal": terminal, "recovery": recovery}
    finally:
        daw.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("working_als", type=Path)
    parser.add_argument("journal_path", type=Path)
    parser.add_argument("transaction_id")
    args = parser.parse_args()
    print(json.dumps(run(args.working_als, args.journal_path, args.transaction_id), indent=2))


if __name__ == "__main__":
    main()
