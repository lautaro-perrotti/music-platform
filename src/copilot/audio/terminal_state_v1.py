"""Shared terminal-state verifier for bootstrap / preflight / analyze / run.

Does not loosen project-state semantics. Reports only; never fakes success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copilot.agent.journal import DurableJournal
from copilot.agent.recovery import RecoveryStatus, classify_journal
from copilot.audio.capture_journal_recovery import unresolved_capture_journals
from copilot.audio.tap_trust import JOURNAL_DIR, interpret_send_level
from copilot.schemas.transaction import TransactionStatus

BOOTSTRAP_JOURNAL_DIR = Path("logs") / "bootstrap_journal"
AGENT_JOURNAL_GLOBS = (
    Path("logs") / "journals",
)
OPEN_TX_STATUSES = frozenset(
    {
        TransactionStatus.PLANNED.value,
        TransactionStatus.SENT.value,
        TransactionStatus.APPLIED.value,
        TransactionStatus.IN_DOUBT.value,
    }
)
SKIP_JOURNAL_PARTS = ("capture_journal", "bootstrap_journal")


def _tap_rec_is_off(row: dict[str, Any] | None) -> bool:
    if row is None or row.get("rec") is None:
        return False
    try:
        return abs(float(row["rec"])) < 0.01
    except (TypeError, ValueError):
        return False


def unresolved_bootstrap_journals(directory: Path | None = None) -> list[dict[str, Any]]:
    root = directory or BOOTSTRAP_JOURNAL_DIR
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.jsonl")):
        journal = DurableJournal(path)
        rows = journal.read_all()
        if not rows:
            continue
        last = str(rows[-1].get("status") or "")
        if last in {"PREPARED", "APPLYING"}:
            out.append(
                {
                    "pass_id": path.stem,
                    "path": str(path),
                    "last_status": last,
                    "rows": len(rows),
                }
            )
    return out


def unresolved_agent_transactions(
    roots: list[Path] | None = None,
) -> list[dict[str, Any]]:
    search = list(roots or AGENT_JOURNAL_GLOBS)
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in search:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.jsonl")):
            lowered = str(path).replace("\\", "/").lower()
            if any(part in lowered for part in SKIP_JOURNAL_PARTS):
                continue
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                records = DurableJournal(path).read_all()
            except OSError:
                continue
            for report in classify_journal(records):
                recovery = str(report.get("recovery") or "")
                last = str(report.get("last_status") or "")
                if last in OPEN_TX_STATUSES or recovery in {
                    RecoveryStatus.IN_DOUBT.value,
                    RecoveryStatus.RECOVERY_REQUIRED.value,
                    RecoveryStatus.ROLLBACK_REQUIRED.value,
                    RecoveryStatus.ROLLBACK_CONFLICT.value,
                }:
                    found.append({**report, "path": str(path)})
    return found


def host_routing_restored(info: dict[str, Any] | None) -> dict[str, Any]:
    if not info:
        return {"ok": False, "reason": "host_info_missing"}
    output = str(info.get("output_routing_type") or info.get("output") or "")
    through_main = output.lower() in {"main", "master"}
    sends = list(info.get("sends") or [])
    interpreted = [interpret_send_level(row) for row in sends]
    return {
        "ok": not through_main,
        "output": output,
        "through_main": through_main,
        "sends_silent": bool(interpreted)
        and all(item.get("semantic_silence") for item in interpreted),
    }


def verify_terminal_state(
    *,
    transport_playing: bool | None = None,
    taps: list[dict[str, Any]] | None = None,
    host_infos: dict[str, dict[str, Any]] | None = None,
    capture_journal_dir: Path | None = None,
    bootstrap_journal_dir: Path | None = None,
    agent_journal_roots: list[Path] | None = None,
    open_transaction_in_memory: bool = False,
) -> dict[str, Any]:
    """Single verifier used by bootstrap, preflight, analyze, and run."""
    failures: list[str] = []
    tap_rows = list(taps or [])
    taps_idle = all(_tap_rec_is_off(row) for row in tap_rows) if tap_rows else True
    if tap_rows and not taps_idle:
        failures.append("taps_not_idle")
    if transport_playing:
        failures.append("transport_playing")

    hosts_restored: dict[str, Any] = {}
    for name, info in (host_infos or {}).items():
        card = host_routing_restored(info)
        hosts_restored[name] = card
        if not card.get("ok"):
            failures.append(f"host_not_restored:{name}")

    capture_open = unresolved_capture_journals(capture_journal_dir or JOURNAL_DIR)
    bootstrap_open = unresolved_bootstrap_journals(bootstrap_journal_dir)
    agent_open = unresolved_agent_transactions(agent_journal_roots)
    in_doubt = [
        row
        for row in agent_open
        if str(row.get("last_status") or "") == TransactionStatus.IN_DOUBT.value
        or str(row.get("recovery") or "") == RecoveryStatus.IN_DOUBT.value
    ]
    if capture_open:
        failures.append("stale_capture_journal")
    if bootstrap_open:
        failures.append("stale_bootstrap_journal")
    if agent_open:
        failures.append("open_transaction")
    if in_doubt:
        failures.append("unresolved_IN_DOUBT")
    if open_transaction_in_memory:
        failures.append("open_transaction_in_memory")

    return {
        "ok": not failures,
        "transport_stopped": transport_playing is False,
        "transport_playing": bool(transport_playing),
        "taps_idle": taps_idle,
        "capture_hosts_restored": hosts_restored,
        "routing_restored": all(card.get("ok") for card in hosts_restored.values())
        if hosts_restored
        else True,
        "open_capture_journals": capture_open,
        "open_bootstrap_journals": bootstrap_open,
        "open_transactions": agent_open,
        "unresolved_in_doubt": in_doubt,
        "failures": failures,
    }
