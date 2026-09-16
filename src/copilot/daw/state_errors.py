"""Fail-closed errors for session state trust. None of these may warn-and-write."""

from __future__ import annotations

from copilot.daw.adapter import DawError

PROJECT_MISMATCH = "PROJECT_MISMATCH"
STALE_PLAN = "STALE_PLAN"
TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
STATE_UNAVAILABLE = "STATE_UNAVAILABLE"
STATE_TOKEN_MISMATCH = "STATE_TOKEN_MISMATCH"
CACHE_STALE = "CACHE_STALE"
CACHE_MISS = "CACHE_MISS"
IDENTITY_RECONCILIATION_FAILED = "IDENTITY_RECONCILIATION_FAILED"

FAIL_CLOSED_CODES = frozenset(
    {
        PROJECT_MISMATCH,
        STALE_PLAN,
        TARGET_NOT_FOUND,
        TARGET_AMBIGUOUS,
        STATE_UNAVAILABLE,
        STATE_TOKEN_MISMATCH,
        CACHE_STALE,
        IDENTITY_RECONCILIATION_FAILED,
    }
)


class StateTrustError(DawError):
    def __init__(self, code: str, message: str, *, details: dict | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.details = details or {}
