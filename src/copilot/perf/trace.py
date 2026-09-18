"""ExecutionSpan / PerformanceTrace / OperationMetrics.

One trace per user-visible operation. Spans nest, carry a category so time can
be attributed (intrinsic audio vs Ableton RPC vs model vs local CPU/IO), and
record cache hit/miss, attempt number and bytes where relevant.

Design constraints:
  * stdlib only
  * monotonic durations (perf_counter); wall clock stored for correlation only
  * never raises into the measured code path
  * thread-safe enough for the single-threaded pipeline plus helper threads
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

TRACE_VERSION = "perf-trace-1"

# Category drives the critical-path split required by the audit.
CAT_INTRINSIC = "INTRINSIC_AUDIO"   # real-time playback we cannot compress
CAT_ABLETON = "ABLETON_RPC"         # Remote Script round trips
CAT_MODEL = "MODEL"                 # provider/network reasoning latency
CAT_CPU = "LOCAL_CPU"               # DSP, parsing, hashing
CAT_IO = "LOCAL_IO"                 # filesystem, artifacts, journals
CAT_WAIT = "WAIT"                   # sleeps, polling, readiness waits
CAT_ORCH = "ORCHESTRATION"          # everything else the pipeline spends

CATEGORIES = (
    CAT_INTRINSIC,
    CAT_ABLETON,
    CAT_MODEL,
    CAT_CPU,
    CAT_IO,
    CAT_WAIT,
    CAT_ORCH,
)


class SpanStatus:
    OK = "OK"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def classify_error(exc: BaseException) -> str:
    """Stable, machine-actionable error class describing the mechanism only.

    Infrastructure failure and musical abstention must stay distinguishable, so
    this never returns a musical verdict such as INSUFFICIENT_EVIDENCE.
    """
    if isinstance(exc, KeyboardInterrupt):
        return "CANCELLED_BY_USER"
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    if isinstance(exc, json.JSONDecodeError):
        return "PARSE_ERROR"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, FileNotFoundError):
        return "FILE_MISSING"
    if isinstance(exc, OSError):
        return "OS_ERROR"
    return type(exc).__name__


@dataclass
class ExecutionSpan:
    name: str
    category: str = CAT_ORCH
    span_id: str = field(default_factory=lambda: f"sp_{uuid4().hex[:12]}")
    parent_id: str | None = None
    depth: int = 0
    started_perf: float = 0.0
    ended_perf: float | None = None
    started_process: float = 0.0
    ended_process: float | None = None
    started_wall: str = ""
    status: str = SpanStatus.OK
    error_class: str | None = None
    error_detail: str | None = None
    attempt: int = 1
    cache: str | None = None           # "HIT" | "MISS" | None
    bytes_processed: int | None = None
    external_call: str | None = None   # "ableton" | "http" | "filesystem"
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def wall_s(self) -> float:
        end = self.ended_perf if self.ended_perf is not None else time.perf_counter()
        return max(0.0, end - self.started_perf)

    @property
    def cpu_s(self) -> float | None:
        if self.ended_process is None:
            return None
        return max(0.0, self.ended_process - self.started_process)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "name": self.name,
            "category": self.category,
            "wall_s": round(self.wall_s, 6),
            "status": self.status,
            "started_wall": self.started_wall,
            "attempt": self.attempt,
        }
        cpu = self.cpu_s
        if cpu is not None:
            payload["cpu_s"] = round(cpu, 6)
        for key, value in (
            ("error_class", self.error_class),
            ("error_detail", self.error_detail),
            ("cache", self.cache),
            ("bytes_processed", self.bytes_processed),
            ("external_call", self.external_call),
        ):
            if value is not None:
                payload[key] = value
        if self.attributes:
            payload["attributes"] = self.attributes
        return payload


@dataclass
class OperationMetrics:
    """Aggregate view of one trace: where did the wall time actually go?

    ``by_category`` uses exclusive time (a span's own wall time minus its
    children's) so the categories sum to the root without double counting.
    """

    operation: str
    total_s: float
    by_category: dict[str, float]
    span_count: int
    rpc_count: int
    rpc_s: float
    slowest: list[dict[str, Any]]
    errors: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "total_s": round(self.total_s, 6),
            "by_category_exclusive_s": {
                key: round(value, 6) for key, value in sorted(self.by_category.items())
            },
            "span_count": self.span_count,
            "ableton_rpc_count": self.rpc_count,
            "ableton_rpc_s": round(self.rpc_s, 6),
            "slowest_spans": self.slowest,
            "errors": self.errors,
        }


class PerformanceTrace:
    """Collects spans for one operation. Cheap enough to always leave on."""

    def __init__(
        self,
        operation: str,
        *,
        operation_id: str | None = None,
        project_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.operation = operation
        self.operation_id = operation_id or f"op_{uuid4().hex[:12]}"
        self.project_id = project_id
        self.attributes = dict(attributes or {})
        self.trace_version = TRACE_VERSION
        self.started_wall = _now_iso()
        self._started_perf = time.perf_counter()
        self._ended_perf: float | None = None
        self.spans: list[ExecutionSpan] = []
        self._stack: list[ExecutionSpan] = []
        self._lock = threading.RLock()

    # -- lifecycle -------------------------------------------------------
    @property
    def total_s(self) -> float:
        end = self._ended_perf if self._ended_perf is not None else time.perf_counter()
        return max(0.0, end - self._started_perf)

    def finish(self) -> None:
        if self._ended_perf is None:
            self._ended_perf = time.perf_counter()

    def set_project(self, project_id: str | None) -> None:
        if project_id:
            self.project_id = project_id

    # -- span recording --------------------------------------------------
    @contextmanager
    def span(
        self,
        name: str,
        *,
        category: str = CAT_ORCH,
        attempt: int = 1,
        external_call: str | None = None,
        **attributes: Any,
    ) -> Iterator[ExecutionSpan]:
        with self._lock:
            parent = self._stack[-1] if self._stack else None
            record = ExecutionSpan(
                name=name,
                category=category,
                parent_id=parent.span_id if parent else None,
                depth=len(self._stack),
                started_perf=time.perf_counter(),
                started_process=time.process_time(),
                started_wall=_now_iso(),
                attempt=attempt,
                external_call=external_call,
                attributes={k: v for k, v in attributes.items() if v is not None},
            )
            self.spans.append(record)
            self._stack.append(record)
        try:
            yield record
        except BaseException as exc:  # measurement must observe cancellation too
            if isinstance(exc, KeyboardInterrupt):
                record.status = SpanStatus.CANCELLED
            elif isinstance(exc, TimeoutError):
                record.status = SpanStatus.TIMEOUT
            else:
                record.status = SpanStatus.FAILED
            record.error_class = classify_error(exc)
            record.error_detail = str(exc)[:300] or None
            raise
        finally:
            with self._lock:
                record.ended_perf = time.perf_counter()
                record.ended_process = time.process_time()
                if self._stack and self._stack[-1] is record:
                    self._stack.pop()

    # -- analysis --------------------------------------------------------
    def _exclusive(self) -> dict[str, float]:
        child_time: dict[str, float] = {}
        for record in self.spans:
            if record.parent_id:
                child_time[record.parent_id] = (
                    child_time.get(record.parent_id, 0.0) + record.wall_s
                )
        by_category: dict[str, float] = {}
        for record in self.spans:
            own = record.wall_s - child_time.get(record.span_id, 0.0)
            by_category[record.category] = by_category.get(record.category, 0.0) + max(
                0.0, own
            )
        roots = sum(r.wall_s for r in self.spans if r.parent_id is None)
        unattributed = self.total_s - roots
        if unattributed > 0.0005:
            by_category[CAT_ORCH] = by_category.get(CAT_ORCH, 0.0) + unattributed
        return by_category

    def metrics(self) -> OperationMetrics:
        # Only leaf round trips count as RPCs. A wrapper span grouping several
        # of them shares the category but is not itself a round trip.
        rpc = [r for r in self.spans if r.external_call == "ableton"]
        slowest = sorted(self.spans, key=lambda r: r.wall_s, reverse=True)[:12]
        return OperationMetrics(
            operation=self.operation,
            total_s=self.total_s,
            by_category=self._exclusive(),
            span_count=len(self.spans),
            rpc_count=len(rpc),
            rpc_s=sum(r.wall_s for r in rpc),
            slowest=[
                {"name": r.name, "category": r.category, "wall_s": round(r.wall_s, 6)}
                for r in slowest
            ],
            errors=[
                {
                    "name": r.name,
                    "status": r.status,
                    "error_class": r.error_class,
                    "detail": r.error_detail,
                }
                for r in self.spans
                if r.status != SpanStatus.OK
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_version": self.trace_version,
            "operation": self.operation,
            "operation_id": self.operation_id,
            "project_id": self.project_id,
            "started_wall": self.started_wall,
            "total_s": round(self.total_s, 6),
            "attributes": self.attributes,
            "metrics": self.metrics().to_dict(),
            "spans": [record.to_dict() for record in self.spans],
        }

    def render(self, *, min_s: float = 0.0, ascii_only: bool = True) -> str:
        """Indented tree: the report the audit asks for.

        ASCII by default: the Windows console is cp1252 and box-drawing
        characters raise UnicodeEncodeError on print().
        """
        glyphs = ("+- ", "\\- ", "|  ") if ascii_only else ("├─ ", "└─ ", "│  ")
        tee, elbow_last, pipe = glyphs
        lines = [f"{self.operation:<38}{self.total_s:>9.2f}s  [{self.operation_id}]"]
        by_parent: dict[str | None, list[ExecutionSpan]] = {}
        for record in self.spans:
            by_parent.setdefault(record.parent_id, []).append(record)

        def walk(parent: str | None, prefix: str) -> None:
            children = [r for r in (by_parent.get(parent) or []) if r.wall_s >= min_s]
            for position, record in enumerate(children):
                last = position == len(children) - 1
                elbow = elbow_last if last else tee
                label = f"{prefix}{elbow}{record.name}"
                flags = []
                if record.cache:
                    flags.append(record.cache.lower())
                if record.status != SpanStatus.OK:
                    flags.append(record.status.lower())
                suffix = f"  ({', '.join(flags)})" if flags else ""
                lines.append(
                    f"{label:<38}{record.wall_s:>9.2f}s  {record.category}{suffix}"
                )
                walk(record.span_id, prefix + ("   " if last else pipe))

        walk(None, "")
        return "\n".join(lines)

    def persist(self, evidence: Path, *, name: str | None = None) -> Path:
        directory = Path(evidence) / "performance"
        directory.mkdir(parents=True, exist_ok=True)
        filename = name or f"{self.operation}_{self.operation_id}.json"
        path = directory / filename
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8"
        )
        os.replace(tmp, path)
        return path


_CURRENT: contextvars.ContextVar[PerformanceTrace | None] = contextvars.ContextVar(
    "copilot_performance_trace", default=None
)


def current_trace() -> PerformanceTrace | None:
    return _CURRENT.get()


@contextmanager
def active_trace(
    operation: str,
    *,
    project_id: str | None = None,
    **attributes: Any,
) -> Iterator[PerformanceTrace]:
    """Install a trace for the duration of an operation."""
    trace = PerformanceTrace(operation, project_id=project_id, attributes=attributes)
    token = _CURRENT.set(trace)
    try:
        yield trace
    finally:
        trace.finish()
        _CURRENT.reset(token)


@contextmanager
def span(
    name: str,
    *,
    category: str = CAT_ORCH,
    attempt: int = 1,
    external_call: str | None = None,
    **attributes: Any,
) -> Iterator[ExecutionSpan | None]:
    """Record a span on the active trace, or do nothing when untraced.

    Instrumentation is opt-in per operation, so library code can call this
    unconditionally without forcing every caller to own a trace.
    """
    trace = _CURRENT.get()
    if trace is None:
        yield None
        return
    with trace.span(
        name,
        category=category,
        attempt=attempt,
        external_call=external_call,
        **attributes,
    ) as record:
        yield record
