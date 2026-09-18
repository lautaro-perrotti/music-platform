"""Capability registry. Future providers fit the same records; they are not implemented here."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from copilot.runtime.resources import ResourceReq


class LatencyCategory(StrEnum):
    NEGLIGIBLE = "NEGLIGIBLE"
    LOCAL = "LOCAL"
    RPC = "RPC"
    CAPTURE_INTRINSIC = "CAPTURE_INTRINSIC"
    MODEL = "MODEL"


class FailureSemantics(StrEnum):
    FAIL_CLOSED = "FAIL_CLOSED"
    IN_DOUBT = "IN_DOUBT"
    REQUIRES_USER_ACTION = "REQUIRES_USER_ACTION"


@dataclass
class Provider:
    provider_id: str
    version: str = "1"
    available: bool = True
    notes: str = ""


@dataclass
class Capability:
    capability_id: str
    input_contract: str
    output_contract: str
    provider: Provider
    version: str = "1"
    read_only: bool = True
    write: bool = False
    resources: list[ResourceReq] = field(default_factory=list)
    dependencies: tuple[str, ...] = ()
    available: bool = True
    supports_batch: bool = False
    max_batch: int | None = None
    cache_semantics: str = "none"
    failure_semantics: FailureSemantics = FailureSemantics.FAIL_CLOSED
    latency_category: LatencyCategory = LatencyCategory.LOCAL
    execute: Callable[..., Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "provider": self.provider.provider_id,
            "provider_version": self.provider.version,
            "version": self.version,
            "read_only": self.read_only,
            "write": self.write,
            "resources": [
                {"kind": req.kind.value, "access": req.access.value} for req in self.resources
            ],
            "dependencies": list(self.dependencies),
            "available": self.available and self.provider.available,
            "supports_batch": self.supports_batch,
            "max_batch": self.max_batch,
            "cache_semantics": self.cache_semantics,
            "failure_semantics": self.failure_semantics.value,
            "latency_category": self.latency_category.value,
        }


class CapabilityUnavailable(RuntimeError):
    pass


class ProviderUnavailable(RuntimeError):
    pass


class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, list[Capability]] = {}

    def register(self, capability: Capability) -> None:
        self._caps.setdefault(capability.capability_id, []).append(capability)

    def get(self, capability_id: str, *, provider_id: str | None = None) -> Capability:
        rows = self._caps.get(capability_id) or []
        if not rows:
            raise CapabilityUnavailable(capability_id)
        if provider_id:
            for row in rows:
                if row.provider.provider_id == provider_id:
                    if not row.available or not row.provider.available:
                        raise ProviderUnavailable(provider_id)
                    return row
            raise ProviderUnavailable(provider_id)
        for row in rows:
            if row.available and row.provider.available:
                return row
        raise CapabilityUnavailable(capability_id)

    def contents(self) -> list[dict[str, Any]]:
        rows = []
        for items in self._caps.values():
            rows.extend(item.to_dict() for item in items)
        return sorted(rows, key=lambda item: item["capability_id"])
