"""Persistent producer decision ledger.

This is planning memory only.  It is not a transaction journal, does not hold
Ableton authority, and never executes a musical mutation.  SafeWrite remains
the only write authority for the runtime.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from copilot.human_eval.store import atomic_write, now_iso


class ProducerPhase(StrEnum):
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    CRITIQUING = "CRITIQUING"
    ADJUSTING = "ADJUSTING"
    COMPLETE = "COMPLETE"
    ABSTAINED = "ABSTAINED"


class ProducerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: str = Field(default_factory=now_iso)
    kind: str = Field(min_length=1, max_length=64)
    detail: str = Field(default="", max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)


class ProducerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "producer-state-v1"
    session_id: str = Field(min_length=1, max_length=128)
    project_identity: str = Field(min_length=1, max_length=256)
    phase: ProducerPhase = ProducerPhase.PLANNING
    section_name: str | None = None
    iteration: int = Field(default=0, ge=0)
    observations: dict[str, Any] = Field(default_factory=dict)
    decisions: dict[str, Any] = Field(default_factory=dict)
    pending_issues: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    events: list[ProducerEvent] = Field(default_factory=list, max_length=256)

    def record(
        self,
        kind: str,
        *,
        detail: str = "",
        payload: dict[str, Any] | None = None,
        phase: ProducerPhase | None = None,
        section_name: str | None = None,
    ) -> "ProducerState":
        """Return the next state; callers persist it explicitly."""

        updated = self.model_copy(deep=True)
        if phase is not None:
            updated.phase = phase
        if section_name is not None:
            updated.section_name = section_name
        updated.events.append(
            ProducerEvent(kind=kind, detail=detail, payload=dict(payload or {}))
        )
        return updated


class ProducerStateStore:
    """Small atomic store keyed by producer session, not by Live runtime IDs."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, session_id: str) -> Path:
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_" )
        if not safe:
            raise ValueError("PRODUCER_SESSION_ID_REQUIRED")
        return self.root / f"producer_state_{safe}.json"

    def save(self, state: ProducerState) -> Path:
        destination = self.path(state.session_id)
        existing = self.load(state.session_id)
        if existing is not None and existing.project_identity != state.project_identity:
            raise ValueError("PRODUCER_STATE_PROJECT_IDENTITY_MISMATCH")
        atomic_write(destination, state.model_dump(mode="json"))
        return destination

    def load(self, session_id: str) -> ProducerState | None:
        source = self.path(session_id)
        if not source.is_file():
            return None
        return ProducerState.model_validate(json.loads(source.read_text(encoding="utf-8")))

    def create(self, *, session_id: str, project_identity: str) -> ProducerState:
        state = ProducerState(session_id=session_id, project_identity=project_identity)
        self.save(state)
        return state
