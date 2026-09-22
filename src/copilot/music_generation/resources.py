"""Platform-neutral worker resource discovery for music generators.

The result is local evidence only.  It does not select a weaker model to fit
the current host; it tells the router whether the requested quality tier can
run here or must be sent to another worker/cloud tier.
"""

from __future__ import annotations

import os
import platform
import shutil
from pydantic import BaseModel, Field


class StorageVolume(BaseModel):
    mount: str
    filesystem: str | None = None
    total_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    writable: bool
    fixed_local: bool = True


class WorkerResources(BaseModel):
    operating_system: str
    architecture: str
    processor: str
    memory_bytes: int = Field(ge=0)
    gpu_name: str | None = None
    gpu_vram_bytes: int | None = Field(default=None, ge=0)
    volumes: list[StorageVolume] = Field(default_factory=list)


class ExecutionRoute(BaseModel):
    route: str
    reason: str
    selected_mount: str | None = None
    required_bytes: int = Field(ge=0)
    safety_margin_bytes: int = Field(ge=0)


def _gpu_evidence() -> tuple[str | None, int | None]:
    try:
        import torch

        if not torch.cuda.is_available():
            return None, None
        props = torch.cuda.get_device_properties(0)
        return torch.cuda.get_device_name(0), int(props.total_memory)
    except Exception:
        return None, None


def discover_worker_resources() -> WorkerResources:
    """Discover resources on the worker where this function executes.

    A Mac worker therefore reports its own root volume and unified memory; it
    never inherits the Windows worker's drive letters or GPU assumptions.
    """
    volumes: list[StorageVolume] = []
    try:
        import psutil

        partitions = psutil.disk_partitions(all=False)
        for partition in partitions:
            mount = partition.mountpoint
            if not mount or partition.fstype.lower() in {"", "squashfs", "overlay", "tmpfs"}:
                continue
            try:
                usage = shutil.disk_usage(mount)
            except OSError:
                continue
            volumes.append(
                StorageVolume(
                    mount=mount,
                    filesystem=partition.fstype or None,
                    total_bytes=usage.total,
                    free_bytes=usage.free,
                    writable=os.access(mount, os.W_OK),
                    fixed_local=not any(token in partition.opts.lower() for token in ("remote", "ro")),
                )
            )
    except Exception:
        # A worker with no partition API still returns OS/GPU/RAM evidence;
        # routing remains fail-closed when storage is required.
        pass

    try:
        import psutil

        memory_bytes = int(psutil.virtual_memory().total)
    except Exception:
        memory_bytes = 0
    gpu_name, gpu_vram_bytes = _gpu_evidence()
    return WorkerResources(
        operating_system=platform.system(),
        architecture=platform.machine(),
        processor=platform.processor(),
        memory_bytes=memory_bytes,
        gpu_name=gpu_name,
        gpu_vram_bytes=gpu_vram_bytes,
        volumes=volumes,
    )


def choose_execution_route(
    resources: WorkerResources,
    *,
    required_bytes: int,
    safety_margin_bytes: int = 2 * 1024**3,
    mount_preference: str | None = None,
) -> ExecutionRoute:
    """Route a requested quality/model footprint without degrading it."""
    eligible = [volume for volume in resources.volumes if volume.fixed_local and volume.writable]
    if mount_preference:
        eligible.sort(key=lambda volume: 0 if volume.mount == mount_preference else 1)
    for volume in eligible:
        if volume.free_bytes >= required_bytes + safety_margin_bytes:
            return ExecutionRoute(
                route="LOCAL",
                reason="worker volume has measured free space plus safety margin",
                selected_mount=volume.mount,
                required_bytes=required_bytes,
                safety_margin_bytes=safety_margin_bytes,
            )
    return ExecutionRoute(
        route="CLOUD_REQUIRED",
        reason="no local fixed writable volume satisfies measured model footprint and safety margin",
        required_bytes=required_bytes,
        safety_margin_bytes=safety_margin_bytes,
    )
