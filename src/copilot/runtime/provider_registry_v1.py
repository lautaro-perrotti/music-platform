"""Small capability-based provider registry with bounded health caching."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Callable

from copilot.schemas.musical_intelligence import ProviderCapabilityRecord, ProviderHealth


@dataclass
class ProviderCapability:
    provider_id: str
    capability: str
    model_id: str | None = None
    local_or_remote: str = "UNKNOWN"
    timeout_s: float | None = None
    schema_support: bool | None = None
    context_limit: int | None = None
    audio_support: bool = False
    configured: bool = False
    provenance: list[str] = field(default_factory=list)
    _health: ProviderHealth = ProviderHealth.UNKNOWN
    _reason: str | None = None
    _checked_at: float | None = None

    def record(self, *, health: ProviderHealth, reason: str | None = None) -> None:
        self._health = health
        self._reason = reason
        self._checked_at = monotonic()

    def to_record(self) -> ProviderCapabilityRecord:
        available = self._health is ProviderHealth.HEALTHY
        if self._health is ProviderHealth.CONFIGURED:
            available = False
        return ProviderCapabilityRecord(
            provider_id=self.provider_id,
            model_id=self.model_id,
            capability=self.capability,
            availability=available,
            health=self._health,
            local_or_remote=self.local_or_remote,
            timeout_s=self.timeout_s,
            schema_support=self.schema_support,
            context_limit=self.context_limit,
            audio_support=self.audio_support,
            failure_code=self._reason,
            provenance=self.provenance,
        )


class ProviderCapabilityRegistry:
    """Registry is evidence-oriented; registration is not health verification."""

    def __init__(self, *, cache_ttl_s: float = 300.0) -> None:
        self.cache_ttl_s = cache_ttl_s
        self._entries: dict[tuple[str, str], ProviderCapability] = {}

    def register(self, entry: ProviderCapability) -> None:
        self._entries[(entry.provider_id, entry.capability)] = entry
        if entry.configured and entry._health is ProviderHealth.UNKNOWN:
            entry.record(health=ProviderHealth.CONFIGURED)

    def probe(
        self,
        provider_id: str,
        capability: str,
        check: Callable[[], bool],
        *,
        force: bool = False,
    ) -> ProviderCapabilityRecord:
        entry = self._entries[(provider_id, capability)]
        if (
            not force
            and entry._health is not ProviderHealth.CONFIGURED
            and entry._checked_at is not None
            and monotonic() - entry._checked_at < self.cache_ttl_s
        ):
            return entry.to_record()
        if not entry.configured:
            entry.record(health=ProviderHealth.UNAVAILABLE, reason="NOT_CONFIGURED")
            return entry.to_record()
        try:
            ok = bool(check())
        except Exception as exc:  # health probes must never escape into production work
            entry.record(health=ProviderHealth.UNAVAILABLE, reason=type(exc).__name__)
        else:
            entry.record(health=ProviderHealth.HEALTHY if ok else ProviderHealth.UNAVAILABLE,
                         reason=None if ok else "HEALTH_CHECK_FAILED")
        return entry.to_record()

    def snapshot(self) -> list[ProviderCapabilityRecord]:
        return [self._entries[key].to_record() for key in sorted(self._entries)]


__all__ = ["ProviderCapability", "ProviderCapabilityRegistry"]
