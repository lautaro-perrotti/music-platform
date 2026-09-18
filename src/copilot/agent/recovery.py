from __future__ import annotations

from enum import StrEnum
from typing import Any

from copilot.schemas.transaction import TransactionStatus


class RecoveryStatus(StrEnum):
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    IN_DOUBT = "IN_DOUBT"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
    ROLLBACK_CONFLICT = "ROLLBACK_CONFLICT"
    VERIFIED = "VERIFIED"


TERMINAL = {
    TransactionStatus.VERIFIED.value,
    TransactionStatus.FAILED.value,
    TransactionStatus.ROLLED_BACK.value,
    TransactionStatus.ROLLBACK_CONFLICT.value,
    TransactionStatus.CANCELLED.value,
    TransactionStatus.SUPERSEDED.value,
}


def classify_journal(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify incomplete work. Never invent certainty. Never auto-mutate."""
    by_txn: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        txn_id = str(record.get("transaction_id") or "")
        if not txn_id:
            continue
        by_txn.setdefault(txn_id, []).append(record)

    reports: list[dict[str, Any]] = []
    for txn_id, events in by_txn.items():
        last = events[-1]
        status = str(last.get("status") or "")
        kind = str(last.get("kind") or "")
        if status == TransactionStatus.VERIFIED.value:
            recovery = RecoveryStatus.VERIFIED
        elif status in {
            TransactionStatus.CANCELLED.value,
            TransactionStatus.SUPERSEDED.value,
        }:
            recovery = RecoveryStatus.VERIFIED
        elif status == TransactionStatus.ROLLBACK_CONFLICT.value:
            recovery = RecoveryStatus.ROLLBACK_CONFLICT
        elif status == TransactionStatus.IN_DOUBT.value or (
            status == TransactionStatus.SENT.value and kind != "rollback"
        ):
            recovery = RecoveryStatus.IN_DOUBT
        elif kind == "rollback" or status == TransactionStatus.ROLLED_BACK.value:
            if status == TransactionStatus.ROLLED_BACK.value:
                recovery = RecoveryStatus.VERIFIED
            else:
                recovery = RecoveryStatus.ROLLBACK_REQUIRED
        elif status in {
            TransactionStatus.PLANNED.value,
            TransactionStatus.PREPARED.value,
        }:
            recovery = RecoveryStatus.RECOVERY_REQUIRED
        elif status == TransactionStatus.APPLIED.value:
            recovery = RecoveryStatus.RECOVERY_REQUIRED
        elif status == TransactionStatus.PARTIAL_FAILURE.value:
            recovery = RecoveryStatus.ROLLBACK_REQUIRED
        elif status in TERMINAL:
            recovery = RecoveryStatus.VERIFIED
        else:
            recovery = RecoveryStatus.RECOVERY_REQUIRED
        reports.append(
            {
                "transaction_id": txn_id,
                "last_status": status,
                "last_kind": kind,
                "recovery": recovery.value,
                "known": _known(status, kind),
                "unknown": _unknown(status, kind),
            }
        )
    return reports


def _known(status: str, kind: str) -> str:
    if status in {
        TransactionStatus.PLANNED.value,
        TransactionStatus.PREPARED.value,
    }:
        return "Intent and durable pre-state were persisted; command was not marked sent."
    if status == TransactionStatus.CANCELLED.value:
        return "Work was cancelled before an unknown remote outcome."
    if status == TransactionStatus.SUPERSEDED.value:
        return "Plan was replaced by a newer plan before dispatch."
    if status == TransactionStatus.PARTIAL_FAILURE.value:
        return "Some steps applied; later steps failed with a known outcome."
    if status == TransactionStatus.SENT.value:
        return "Command was handed to the transport."
    if status == TransactionStatus.APPLIED.value:
        return "A write acknowledgement was recorded."
    if status == TransactionStatus.IN_DOUBT.value:
        return "Transport outcome was unknown after send."
    if kind == "rollback":
        return "Rollback started; later inverses may be missing."
    return f"Last recorded status is {status}."


def _unknown(status: str, kind: str) -> str:
    if status in {
        TransactionStatus.PLANNED.value,
        TransactionStatus.PREPARED.value,
    }:
        return "Whether the process died before send."
    if status in {
        TransactionStatus.CANCELLED.value,
        TransactionStatus.SUPERSEDED.value,
    }:
        return "Nothing beyond the last journal record."
    if status == TransactionStatus.PARTIAL_FAILURE.value:
        return "Whether remaining inverses would restore applied steps."
    if status == TransactionStatus.SENT.value or status == TransactionStatus.IN_DOUBT.value:
        return "Whether Live applied the mutation."
    if status == TransactionStatus.APPLIED.value:
        return "Whether read-back would have verified the postcondition."
    if kind == "rollback":
        return "Whether remaining inverses ran."
    return "Nothing beyond the last journal record."
