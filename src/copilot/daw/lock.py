from __future__ import annotations

import threading
from contextlib import contextmanager
from collections.abc import Iterator


class SessionMutationLock:
    """Serializes writes. Reads stay unlocked."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def write(self) -> Iterator[None]:
        self._lock.acquire()
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            self._lock.release()

    def locked(self) -> bool:
        return self._depth > 0
