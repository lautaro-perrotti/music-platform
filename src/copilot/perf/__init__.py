"""Performance instrumentation. Measurement only — never changes behavior.

Durations use monotonic clocks (time.perf_counter). Wall clock is persisted
only for correlation with evidence artifacts and journals.

This package must never import DAW, reasoning or capture modules at module
scope: instrumentation has to be importable from anywhere without dragging in
the pipeline it measures.
"""

from copilot.perf.trace import (
    ExecutionSpan,
    OperationMetrics,
    PerformanceTrace,
    SpanStatus,
    active_trace,
    classify_error,
    current_trace,
    span,
)

__all__ = [
    "ExecutionSpan",
    "OperationMetrics",
    "PerformanceTrace",
    "SpanStatus",
    "active_trace",
    "classify_error",
    "current_trace",
    "span",
]
