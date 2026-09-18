"""Time every Ableton Remote Script round trip.

The adapter already counts RPCs (``tcp_counts``); it does not time them. On this
protocol latency is dominated by Live's main-thread scheduler quantum, so RPC
*count* and RPC *wall time* are the two numbers that explain most of a workflow.

This is a measurement shim only. It wraps ``AbletonTcpAdapter._command`` with a
span and calls straight through: same arguments, same return value, same
exceptions, same request-id and capability checks. It never retries, never
caches, never suppresses an error, and installs nothing when no trace is active.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from copilot.perf.trace import CAT_ABLETON, span

# Reads that return the whole session in one packet. Split out so a report can
# separate "one big authoritative read" from "many small reads".
HEAVY_READS = frozenset(
    {
        "get_session_info",
        "get_capture_topology",
        "get_tracks_info",
        "get_track_info",
        "get_all_track_names",
        "get_session_path",
    }
)


@contextmanager
def instrumented_rpc(adapter: Any) -> Iterator[Any]:
    """Temporarily time every ``_command`` on this adapter instance.

    Patches the bound method on the instance only; the class and every other
    adapter are untouched, and the original is always restored.
    """
    original = adapter._command

    def timed(command_type: str, params: dict[str, Any] | None = None, **kwargs: Any):
        with span(
            f"rpc:{command_type}",
            category=CAT_ABLETON,
            external_call="ableton",
            command=command_type,
            heavy_read=command_type in HEAVY_READS or None,
            side_effect=bool(kwargs.get("side_effect")) or None,
        ):
            return original(command_type, params, **kwargs)

    adapter._command = timed
    try:
        yield adapter
    finally:
        adapter._command = original


def rpc_breakdown(trace: Any) -> dict[str, Any]:
    """Per-command counts and latency from a finished trace."""
    rows: dict[str, dict[str, Any]] = {}
    for record in trace.spans:
        # Key on external_call, not category: a wrapper span around a group of
        # RPCs is also ABLETON_RPC and would otherwise double-count its children.
        if record.external_call != "ableton":
            continue
        command = str((record.attributes or {}).get("command") or record.name)
        row = rows.setdefault(command, {"count": 0, "total_s": 0.0, "samples": []})
        row["count"] += 1
        row["total_s"] += record.wall_s
        row["samples"].append(round(record.wall_s, 6))
    for row in rows.values():
        samples = sorted(row.pop("samples"))
        row["total_s"] = round(row["total_s"], 6)
        row["min_s"] = samples[0]
        row["median_s"] = samples[len(samples) // 2]
        row["max_s"] = samples[-1]
    ordered = dict(
        sorted(rows.items(), key=lambda item: item[1]["total_s"], reverse=True)
    )
    return {
        "total_calls": sum(row["count"] for row in rows.values()),
        "total_s": round(sum(row["total_s"] for row in rows.values()), 6),
        "by_command": ordered,
    }
