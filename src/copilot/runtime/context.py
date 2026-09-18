"""One OperationContext for timeout, cancellation, retry, cleanup, provenance."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.perf.trace import PerformanceTrace, current_trace
from copilot.runtime.cache import StrongCache
from copilot.runtime.frozen import assert_mutable_output
from copilot.runtime.resources import ResourceLease


class RetryClass(StrEnum):
    RETRYABLE_TRANSIENT = "RETRYABLE_TRANSIENT"
    RETRYABLE_AFTER_REFRESH = "RETRYABLE_AFTER_REFRESH"
    NON_RETRYABLE = "NON_RETRYABLE"
    REQUIRES_USER_ACTION = "REQUIRES_USER_ACTION"
    IN_DOUBT = "IN_DOUBT"


class Cancelled(RuntimeError):
    pass


class TaskBlocked(RuntimeError):
    def __init__(self, reason: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.payload = payload or {}


class DeadlineExceeded(TimeoutError):
    pass


class CleanupFailure(RuntimeError):
    pass


def classify_retry(exc: BaseException, *, mutation_started: bool = False) -> RetryClass:
    if isinstance(exc, KeyboardInterrupt):
        return RetryClass.NON_RETRYABLE
    if isinstance(exc, Cancelled):
        return RetryClass.NON_RETRYABLE
    message = str(exc)
    if "PROJECT_MISMATCH" in message or "PROJECT_CHANGED" in message:
        return RetryClass.NON_RETRYABLE
    if mutation_started and (
        "Timeout waiting for Ableton" in message or "socket disconnect" in message
    ):
        return RetryClass.IN_DOUBT
    if "429" in message or "temporarily" in message.lower():
        return RetryClass.RETRYABLE_TRANSIENT
    if "STALE" in message or "refresh" in message.lower():
        return RetryClass.RETRYABLE_AFTER_REFRESH
    if "REQUIRES_USER" in message or "ORIGINAL_SET_OPEN" in message:
        return RetryClass.REQUIRES_USER_ACTION
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return RetryClass.RETRYABLE_TRANSIENT
    return RetryClass.NON_RETRYABLE


class CancellationToken:
    def __init__(self, parent: CancellationToken | None = None) -> None:
        self._event = threading.Event()
        self._reason: str | None = None
        self._parent = parent
        self._children: list[CancellationToken] = []
        if parent is not None:
            parent._children.append(self)

    def cancel(self, reason: str = "cancelled") -> None:
        self._reason = reason
        self._event.set()
        for child in list(self._children):
            child.cancel(reason)

    @property
    def cancelled(self) -> bool:
        if self._event.is_set():
            return True
        return bool(self._parent and self._parent.cancelled)

    @property
    def reason(self) -> str | None:
        if self._event.is_set():
            return self._reason
        if self._parent is not None:
            return self._parent.reason
        return None

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled(self.reason or "cancelled")

    def child(self) -> CancellationToken:
        token = CancellationToken(self)
        if self.cancelled:
            token.cancel(self.reason or "cancelled")
        return token


class Deadline:
    def __init__(self, remaining_s: float | None = None, *, parent: Deadline | None = None) -> None:
        self._parent = parent
        if remaining_s is None and parent is None:
            self._deadline_perf = None
        elif remaining_s is None:
            self._deadline_perf = parent._deadline_perf if parent is not None else None
        else:
            cap = time.perf_counter() + max(0.0, float(remaining_s))
            if parent is not None and parent._deadline_perf is not None:
                cap = min(cap, parent._deadline_perf)
            self._deadline_perf = cap

    def remaining(self) -> float | None:
        if self._deadline_perf is None:
            return None
        return max(0.0, self._deadline_perf - time.perf_counter())

    def check(self) -> None:
        remaining = self.remaining()
        if remaining is not None and remaining <= 0:
            raise DeadlineExceeded("deadline exceeded")

    def child(self, remaining_s: float | None = None) -> Deadline:
        return Deadline(remaining_s, parent=self)

    def timeout_or(self, provider_timeout_s: float) -> float:
        remaining = self.remaining()
        if remaining is None:
            return provider_timeout_s
        return max(0.0, min(provider_timeout_s, remaining))


@dataclass
class Finalizer:
    name: str
    fn: Callable[[], Any]
    order: int = 0


class FinalizerStack:
    def __init__(self) -> None:
        self._items: list[Finalizer] = []
        self.results: list[dict[str, Any]] = []

    def register(
        self, fn: Callable[[], Any], *, name: str, order: int = 0
    ) -> None:
        self._items.append(Finalizer(name=name, fn=fn, order=order))

    def run_all(self) -> list[dict[str, Any]]:
        ordered = sorted(self._items, key=lambda item: item.order, reverse=True)
        self._items = []
        failures: list[str] = []
        for item in ordered:
            row: dict[str, Any] = {"name": item.name, "ok": True}
            try:
                row["result"] = item.fn()
            except Exception as exc:  # noqa: BLE001 — cleanup must stay visible
                row["ok"] = False
                row["error"] = str(exc)
                failures.append(f"{item.name}:{exc}")
            self.results.append(row)
        if failures:
            raise CleanupFailure("; ".join(failures))
        return self.results


@dataclass
class RetryPolicy:
    classification: RetryClass = RetryClass.NON_RETRYABLE
    max_attempts: int = 1
    delay_s: float = 0.0


@dataclass
class OperationContext:
    operation_id: str
    parent_operation_id: str | None = None
    project_identity: str | None = None
    project_state_token: str | None = None
    audible_state_token: str | None = None
    target_state_token: str | None = None
    deadline: Deadline = field(default_factory=Deadline)
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    resources: ResourceLease = field(default_factory=ResourceLease)
    trace: PerformanceTrace | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    provenance: dict[str, Any] = field(default_factory=dict)
    finalizers: FinalizerStack = field(default_factory=FinalizerStack)
    cache: StrongCache = field(default_factory=StrongCache)
    evidence_root: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        parent: OperationContext | None = None,
        evidence_root: Path | None = None,
        deadline_s: float | None = None,
        project_identity: str | None = None,
    ) -> OperationContext:
        if evidence_root is not None:
            assert_mutable_output(evidence_root)
        trace = current_trace()
        if parent is None:
            return cls(
                operation_id=f"op_{uuid4().hex[:12]}",
                project_identity=project_identity,
                deadline=Deadline(deadline_s),
                trace=trace,
                evidence_root=evidence_root,
            )
        child = cls(
            operation_id=f"op_{uuid4().hex[:12]}",
            parent_operation_id=parent.operation_id,
            project_identity=project_identity or parent.project_identity,
            project_state_token=parent.project_state_token,
            audible_state_token=parent.audible_state_token,
            target_state_token=parent.target_state_token,
            deadline=parent.deadline.child(deadline_s),
            cancellation=parent.cancellation.child(),
            resources=parent.resources,
            trace=parent.trace or trace,
            retry=parent.retry,
            provenance=dict(parent.provenance),
            finalizers=parent.finalizers,
            cache=parent.cache,
            evidence_root=evidence_root or parent.evidence_root,
            extra=dict(parent.extra),
        )
        return child

    def bind_tokens(self, session: Any) -> None:
        self.project_identity = getattr(session, "project_identity", None) or self.project_identity
        self.project_state_token = getattr(session, "project_token", None)
        self.audible_state_token = getattr(session, "audible_token", None)
        self.target_state_token = getattr(session, "target_token", None)

    def tokens_match(self, session: Any) -> bool:
        identity = getattr(session, "project_identity", None)
        project = getattr(session, "project_token", None)
        if self.project_identity and identity and identity != self.project_identity:
            return False
        if self.project_state_token and project and project != self.project_state_token:
            return False
        return True

    def check(self) -> None:
        self.cancellation.check()
        self.deadline.check()
