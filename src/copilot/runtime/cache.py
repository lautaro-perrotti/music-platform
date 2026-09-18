"""Strong-key cache. Never keyed by track name, index, or a mutable path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class WeakCacheKey(ValueError):
    pass


_FORBIDDEN_KEY_PARTS = ("track_name", "index", "logs/", "current.json")


def assert_strong_key(key: str) -> None:
    lowered = key.lower()
    for part in _FORBIDDEN_KEY_PARTS:
        if part in lowered:
            raise WeakCacheKey(f"weak cache key: {key}")
    if not key or ":" not in key:
        raise WeakCacheKey(f"cache key must be typed and versioned: {key}")


@dataclass
class StrongCache:
    store: dict[str, Any] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0
    bound_project_token: str | None = None

    def get(self, key: str, *, project_token: str | None = None) -> Any | None:
        assert_strong_key(key)
        if (
            self.bound_project_token
            and project_token
            and project_token != self.bound_project_token
        ):
            self.misses += 1
            return None
        if key in self.store:
            self.hits += 1
            return self.store[key]
        self.misses += 1
        return None

    def put(self, key: str, value: Any, *, project_token: str | None = None) -> None:
        assert_strong_key(key)
        if project_token:
            if self.bound_project_token is None:
                self.bound_project_token = project_token
            elif project_token != self.bound_project_token:
                self.invalidate()
                self.bound_project_token = project_token
        self.store[key] = value

    def invalidate(self) -> None:
        self.store.clear()
        self.bound_project_token = None
