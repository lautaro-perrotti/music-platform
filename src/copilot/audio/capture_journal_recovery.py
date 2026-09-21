"""Recover unresolved capture journals. Append-only. No history rewrite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copilot.audio.file_hash import sha256_file
from copilot.audio.tap_trust import (
    FAILED,
    FINALIZING,
    IN_DOUBT,
    JOURNAL_DIR,
    PREPARED,
    RECORDING,
    RECOVERED,
    VERIFIED,
    CaptureJournal,
    inventory_taps,
)
from copilot.human_eval.store import now_iso
from copilot.platform.system import default_capture_dir

OPEN_STATUSES = frozenset({PREPARED, RECORDING, FINALIZING})
TERMINAL_STATUSES = frozenset({VERIFIED, FAILED, IN_DOUBT, RECOVERED})


def _asset_candidates(pass_id: str, search_roots: list[Path]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if pass_id not in path.name and pass_id not in str(path):
                continue
            if path.suffix.lower() not in {".wav", ".aif", ".aiff", ".flac", ".json"}:
                continue
            found.append(
                {
                    "path": str(path),
                    "exists": True,
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return found


def unresolved_capture_journals(directory: Path | None = None) -> list[dict[str, Any]]:
    root = directory or JOURNAL_DIR
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.jsonl")):
        journal = CaptureJournal(path.stem, directory=root)
        last = journal.last_status()
        if last in OPEN_STATUSES:
            out.append(
                {
                    "pass_id": path.stem,
                    "path": str(path),
                    "last_status": last,
                    "rows": len(journal.journal.read_all()),
                }
            )
    return out


def recover_stale_capture_journal(
    pass_id: str,
    *,
    directory: Path | None = None,
    daw: Any | None = None,
    search_roots: list[Path] | None = None,
    routing_restored: bool | None = None,
) -> dict[str, Any]:
    """Append a durable terminal recovery record. Idempotent on terminal journals."""
    root = directory or JOURNAL_DIR
    journal = CaptureJournal(pass_id, directory=root)
    rows = journal.journal.read_all()
    last = journal.last_status()
    if last in TERMINAL_STATUSES:
        return {
            "pass_id": pass_id,
            "status": "ALREADY_TERMINAL",
            "terminal": last,
            "idempotent": True,
            "appended": False,
            "rows": len(rows),
        }

    roots = search_roots or [
        Path("logs"),
        default_capture_dir(),
        Path("captures"),
    ]
    assets = _asset_candidates(pass_id, roots)
    complete_assets = [a for a in assets if int(a.get("size") or 0) > 0 and a.get("sha256")]

    live_taps: list[dict[str, Any]] = []
    any_rec = False
    if daw is not None:
        try:
            live_taps = inventory_taps(daw)
            any_rec = any(float(t.get("rec") or 0.0) >= 0.5 for t in live_taps)
        except Exception as exc:
            live_taps = [{"error": f"{type(exc).__name__}:{exc}"}]

    if any_rec:
        terminal = IN_DOUBT
        reason = "live_tap_still_recording_cannot_close_as_recovered"
    elif complete_assets and last == FINALIZING:
        # Assets exist but never claimed VERIFIED — do not invent VERIFIED.
        terminal = IN_DOUBT
        reason = "assets_present_without_verified_claim"
    elif not complete_assets and not any_rec:
        # Historical interrupt: no durable capture product, taps idle.
        terminal = RECOVERED
        reason = "stale_open_journal_no_assets_taps_idle_transaction_not_active"
    else:
        terminal = FAILED
        reason = "capture_interrupted_incomplete_or_unrestored"

    body = {
        "recovery": True,
        "recovery_at": now_iso(),
        "reason": reason,
        "prior_status": last,
        "prior_rows": len(rows),
        "assets_found": assets,
        "complete_asset_count": len(complete_assets),
        "live_taps_rec": [
            {"tap_instance_id": t.get("tap_instance_id"), "rec": t.get("rec")}
            for t in live_taps
            if "tap_instance_id" in t
        ],
        "routing_restored": routing_restored,
        "note": (
            "Append-only recovery. History preserved. "
            "VERIFIED is never assigned by recovery."
        ),
    }
    journal.record(terminal, **body)
    return {
        "pass_id": pass_id,
        "status": "RECOVERY_APPENDED",
        "terminal": terminal,
        "idempotent": False,
        "appended": True,
        "reason": reason,
        "assets_found": len(assets),
        "any_rec": any_rec,
    }


def recover_all_unresolved(
    *,
    directory: Path | None = None,
    daw: Any | None = None,
) -> dict[str, Any]:
    open_rows = unresolved_capture_journals(directory=directory)
    results = [
        recover_stale_capture_journal(row["pass_id"], directory=directory, daw=daw)
        for row in open_rows
    ]
    still_open = unresolved_capture_journals(directory=directory)
    return {
        "recovered": results,
        "remaining_unresolved": still_open,
        "ok": len(still_open) == 0,
    }
