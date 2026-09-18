"""PERFORMANCE_TRACE_V1 semantics.

Instrumentation must be measurement-only: it may never change a return value,
swallow an exception, or turn an infrastructure failure into a musical verdict.
"""

from __future__ import annotations

import json
import time

import pytest

from copilot.perf.ableton import instrumented_rpc, rpc_breakdown
from copilot.perf.trace import (
    CAT_ABLETON,
    CAT_CPU,
    CAT_MODEL,
    PerformanceTrace,
    SpanStatus,
    active_trace,
    classify_error,
    current_trace,
    span,
)


def test_spans_nest_and_exclusive_time_sums_to_total() -> None:
    with active_trace("op") as trace:
        with span("parent", category=CAT_CPU):
            time.sleep(0.01)
            with span("child", category=CAT_ABLETON, external_call="ableton"):
                time.sleep(0.01)
    metrics = trace.metrics()
    assert metrics.span_count == 2
    exclusive = sum(metrics.by_category.values())
    # Exclusive time never double counts nesting.
    assert exclusive == pytest.approx(trace.total_s, abs=0.05)
    assert metrics.by_category[CAT_ABLETON] > 0.0
    assert metrics.rpc_count == 1


def test_rpc_count_excludes_wrapper_spans() -> None:
    """A span grouping several round trips shares the category but is not one.

    Counting by category double-counted the group's own wall time against its
    children, inflating both rpc_count and rpc_s.
    """
    with active_trace("op") as trace:
        with span("capture.restore_host", category=CAT_ABLETON):  # no external_call
            with span("rpc:a", category=CAT_ABLETON, external_call="ableton"):
                time.sleep(0.01)
            with span("rpc:b", category=CAT_ABLETON, external_call="ableton"):
                time.sleep(0.01)
    metrics = trace.metrics()
    assert metrics.span_count == 3
    assert metrics.rpc_count == 2, "the wrapper is not a round trip"
    leaves = [r.wall_s for r in trace.spans if r.external_call == "ableton"]
    assert metrics.rpc_s == pytest.approx(sum(leaves), abs=1e-6)


def test_durations_use_a_monotonic_clock() -> None:
    with active_trace("op") as trace:
        with span("work"):
            time.sleep(0.02)
    record = trace.spans[0]
    assert record.wall_s >= 0.015
    # perf_counter based, so a span can never be negative even across a clock change.
    assert record.wall_s >= 0.0
    assert record.cpu_s is not None


def test_failed_span_records_error_class_and_reraises() -> None:
    with active_trace("op") as trace:
        with pytest.raises(TimeoutError):
            with span("rpc", category=CAT_ABLETON):
                raise TimeoutError("waited too long")
    record = trace.spans[0]
    assert record.status == SpanStatus.TIMEOUT
    assert record.error_class == "TIMEOUT"
    assert trace.metrics().errors[0]["error_class"] == "TIMEOUT"


def test_cancellation_is_recorded_and_propagates() -> None:
    with active_trace("op") as trace:
        with pytest.raises(KeyboardInterrupt):
            with span("capture"):
                raise KeyboardInterrupt
    record = trace.spans[0]
    assert record.status == SpanStatus.CANCELLED
    assert record.error_class == "CANCELLED_BY_USER"


def test_error_classes_never_become_a_musical_verdict() -> None:
    """Infrastructure failure must stay distinct from abstention."""
    classes = {
        classify_error(TimeoutError("t")),
        classify_error(json.JSONDecodeError("bad", "{}", 0)),
        classify_error(ConnectionResetError("net")),
        classify_error(PermissionError("denied")),
        classify_error(KeyboardInterrupt()),
    }
    assert "INSUFFICIENT_EVIDENCE" not in classes
    assert classes == {
        "TIMEOUT",
        "PARSE_ERROR",
        "OS_ERROR",
        "PERMISSION_DENIED",
        "CANCELLED_BY_USER",
    }
    # Distinct mechanisms keep distinct classes.
    assert classify_error(TimeoutError("t")) != classify_error(PermissionError("d"))


def test_cache_hit_and_miss_are_recorded() -> None:
    with active_trace("op") as trace:
        with span("asset", category=CAT_CPU) as first:
            first.cache = "MISS"
        with span("asset", category=CAT_CPU) as second:
            second.cache = "HIT"
    kinds = [record.cache for record in trace.spans]
    assert kinds == ["MISS", "HIT"]
    assert '"cache": "HIT"' in json.dumps(trace.to_dict())


def test_attempt_number_is_carried() -> None:
    with active_trace("op") as trace:
        for attempt in (1, 2, 3):
            with span("model", category=CAT_MODEL, attempt=attempt):
                pass
    assert [record.attempt for record in trace.spans] == [1, 2, 3]


def test_span_outside_a_trace_is_a_no_op() -> None:
    assert current_trace() is None
    with span("orphan") as record:
        assert record is None


def test_trace_is_restored_after_nesting() -> None:
    with active_trace("outer") as outer:
        assert current_trace() is outer
        with active_trace("inner") as inner:
            assert current_trace() is inner
        assert current_trace() is outer
    assert current_trace() is None


def test_render_is_ascii_safe_for_the_windows_console() -> None:
    with active_trace("op") as trace:
        with span("a"):
            with span("b"):
                pass
    tree = trace.render()
    tree.encode("cp1252")  # must not raise on a Windows console
    assert "a" in tree and "b" in tree


def test_persist_writes_atomically(tmp_path) -> None:
    with active_trace("op") as trace:
        with span("a"):
            pass
    path = trace.persist(tmp_path)
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["operation"] == "op"
    assert payload["spans"][0]["name"] == "a"
    assert not list(path.parent.glob("*.tmp"))


class _FakeAdapter:
    """Minimal stand-in for the TCP adapter's command surface."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _command(self, command_type, params=None, *, side_effect=False):
        self.calls.append(command_type)
        if command_type == "boom":
            raise RuntimeError("remote failure")
        return {"ok": command_type, "params": params, "side_effect": side_effect}


def test_instrumented_rpc_times_calls_without_changing_them() -> None:
    adapter = _FakeAdapter()
    with active_trace("op") as trace:
        with instrumented_rpc(adapter):
            result = adapter._command("get_session_info", {"a": 1})
            adapter._command("get_session_info")
            adapter._command("set_mixer_volume", None, side_effect=True)
    assert result == {"ok": "get_session_info", "params": {"a": 1}, "side_effect": False}
    assert adapter.calls == ["get_session_info", "get_session_info", "set_mixer_volume"]
    breakdown = rpc_breakdown(trace)
    assert breakdown["total_calls"] == 3
    assert breakdown["by_command"]["get_session_info"]["count"] == 2
    assert "median_s" in breakdown["by_command"]["get_session_info"]


def test_instrumented_rpc_never_swallows_an_error_and_always_unpatches() -> None:
    adapter = _FakeAdapter()
    original = adapter._command
    with active_trace("op") as trace:
        with instrumented_rpc(adapter):
            with pytest.raises(RuntimeError):
                adapter._command("boom")
    assert adapter._command == original
    assert trace.spans[0].status == SpanStatus.FAILED
    assert trace.spans[0].error_class == "RuntimeError"


def test_instrumented_rpc_unpatches_even_when_the_body_raises() -> None:
    adapter = _FakeAdapter()
    original = adapter._command
    with pytest.raises(ValueError):
        with instrumented_rpc(adapter):
            raise ValueError("body failed")
    assert adapter._command == original


def test_trace_without_spans_still_reports_total() -> None:
    trace = PerformanceTrace("empty")
    time.sleep(0.01)
    trace.finish()
    assert trace.total_s >= 0.005
    assert trace.metrics().span_count == 0
    assert trace.to_dict()["spans"] == []
