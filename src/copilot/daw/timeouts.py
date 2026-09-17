from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeoutPolicy:
    connect: float = 5.0
    read: float = 30.0
    simple_mutation: float = 8.0
    large_operation: float = 30.0


DEFAULT_TIMEOUTS = TimeoutPolicy()
# Probe must fail a dead socket fast. Real snapshots still use DEFAULT_TIMEOUTS.
PROBE_TIMEOUTS = TimeoutPolicy(
    connect=2.0,
    read=8.0,
    simple_mutation=8.0,
    large_operation=8.0,
)
