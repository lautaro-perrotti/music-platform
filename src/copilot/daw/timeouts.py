from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeoutPolicy:
    connect: float = 5.0
    read: float = 5.0
    simple_mutation: float = 8.0
    large_operation: float = 30.0


DEFAULT_TIMEOUTS = TimeoutPolicy()
