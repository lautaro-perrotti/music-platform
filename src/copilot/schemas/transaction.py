from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TransactionStatus(StrEnum):
    PLANNED = "PLANNED"
    SENT = "SENT"
    APPLIED = "APPLIED"
    VERIFIED = "VERIFIED"
    IN_DOUBT = "IN_DOUBT"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    ROLLBACK_CONFLICT = "ROLLBACK_CONFLICT"


class TargetFingerprint(BaseModel):
    kind: str = "track"
    role: str = "unknown"
    device_names: list[str] = Field(default_factory=list)
    clip_slots: list[int] = Field(default_factory=list)
    clip_names: list[str] = Field(default_factory=list)
    note_counts: list[int] = Field(default_factory=list)


class TargetLocator(BaseModel):
    track_index: int
    clip_index: int | None = None
    device_index: int | None = None
    parameter_index: int | None = None


class TransactionAction(BaseModel):
    target_stable_id: str
    target_locator_at_apply: TargetLocator
    target_fingerprint: TargetFingerprint
    target_name_at_apply: str = ""
    operation: str
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    expected_after: dict[str, Any] = Field(default_factory=dict)
    inverse_operation: str
    inverse_params: dict[str, Any] = Field(default_factory=dict)
    command_id: str = ""
    session_incarnation_id: str = ""
    expected_revision: int | None = None
    mutation_class: str = "NON_IDEMPOTENT_WRITE"
    status: TransactionStatus = TransactionStatus.PLANNED


class AgentTransaction(BaseModel):
    transaction_id: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    user_intent: str
    session_revision: int
    session_hash: str = ""
    session_incarnation_id: str = ""
    preconditions: list[str] = Field(default_factory=list)
    actions: list[TransactionAction] = Field(default_factory=list)
    assets_created: list[str] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    status: TransactionStatus = TransactionStatus.PLANNED
    error: str | None = None
