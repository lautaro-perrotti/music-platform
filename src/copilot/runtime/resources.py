"""Explicit runtime resources. Scheduler uses these; capabilities declare them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResourceKind(StrEnum):
    ABLETON_SESSION = "ABLETON_SESSION"
    TRANSPORT = "TRANSPORT"
    PROJECT_MUTATION = "PROJECT_MUTATION"
    CAPTURE_HOST_SLOT_1 = "CAPTURE_HOST_SLOT_1"
    CAPTURE_HOST_SLOT_2 = "CAPTURE_HOST_SLOT_2"
    CAPTURE_HOST_POOL = "CAPTURE_HOST_POOL"
    FILESYSTEM_IO = "FILESYSTEM_IO"
    CPU_DSP = "CPU_DSP"
    GPU = "GPU"
    ASTRA_MODEL = "ASTRA_MODEL"
    MUSIC_PERCEPTION_MODEL = "MUSIC_PERCEPTION_MODEL"
    EMBEDDING_MODEL = "EMBEDDING_MODEL"
    PLUGIN_RENDERER = "PLUGIN_RENDERER"


class AccessMode(StrEnum):
    SHARED = "SHARED"
    EXCLUSIVE = "EXCLUSIVE"


@dataclass(frozen=True)
class ResourceReq:
    kind: ResourceKind
    access: AccessMode = AccessMode.SHARED

    def conflicts(self, other: ResourceReq) -> bool:
        if self.kind != other.kind:
            return False
        return AccessMode.EXCLUSIVE in {self.access, other.access}


def exclusive(kind: ResourceKind) -> ResourceReq:
    return ResourceReq(kind, AccessMode.EXCLUSIVE)


def shared(kind: ResourceKind) -> ResourceReq:
    return ResourceReq(kind, AccessMode.SHARED)


class ResourceConflict(RuntimeError):
    pass


class ResourceLease:
    def __init__(self) -> None:
        self.held: list[tuple[str, ResourceReq]] = []

    def can_acquire(self, owner: str, reqs: list[ResourceReq]) -> bool:
        for req in reqs:
            for other_owner, held in self.held:
                if other_owner == owner:
                    continue
                if req.conflicts(held):
                    return False
        return True

    def acquire(self, owner: str, reqs: list[ResourceReq]) -> None:
        if not self.can_acquire(owner, reqs):
            raise ResourceConflict(f"resource conflict for {owner}")
        for req in reqs:
            self.held.append((owner, req))

    def release(self, owner: str) -> None:
        self.held = [row for row in self.held if row[0] != owner]
