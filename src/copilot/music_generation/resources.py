"""Platform-neutral worker resource discovery for music generators.

The result is local evidence only.  It does not select a weaker model to fit
the current host; it tells the router whether the requested quality tier can
run here or must be sent to another worker/cloud tier.
"""

from __future__ import annotations

import os
import shutil
from pydantic import BaseModel, Field
from copilot.platform.system import host_architecture, host_processor, host_system


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
    memory_kind: str = "SYSTEM"
    gpu_name: str | None = None
    gpu_vram_bytes: int | None = Field(default=None, ge=0)
    volumes: list[StorageVolume] = Field(default_factory=list)


class ExecutionRoute(BaseModel):
    route: str
    reason: str
    selected_mount: str | None = None
    required_bytes: int = Field(ge=0)
    safety_margin_bytes: int = Field(ge=0)
    required_memory_bytes: int = Field(default=0, ge=0)
    required_gpu_vram_bytes: int = Field(default=0, ge=0)
    resource_checks: dict[str, str] = Field(default_factory=dict)


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
        operating_system=host_system(),
        architecture=host_architecture(),
        processor=host_processor(),
        memory_bytes=memory_bytes,
        memory_kind=("UNIFIED" if host_system() == "Darwin" else "SYSTEM"),
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
    required_memory_bytes: int | None = None,
    required_gpu_vram_bytes: int | None = None,
) -> ExecutionRoute:
    """Route a requested quality/model footprint without degrading it.

    ``required_bytes`` is the measured aggregate footprint for the selected
    model/runtime (weights, environment, temporary headroom and outputs), not
    a guess tied to a drive letter.  Memory and VRAM are evaluated against the
    worker that supplied ``resources``.  A failed local check is an explicit
    cloud route; this function never selects a smaller model as a workaround.
    """
    required_memory = required_memory_bytes or 0
    required_vram = required_gpu_vram_bytes or 0
    checks: dict[str, str] = {}

    if required_memory and resources.memory_bytes < required_memory:
        checks["memory"] = "INSUFFICIENT"
    elif required_memory:
        checks["memory"] = "PASS"

    if required_vram:
        if resources.gpu_vram_bytes is None:
            checks["gpu_vram"] = "UNKNOWN"
        elif resources.gpu_vram_bytes < required_vram:
            checks["gpu_vram"] = "INSUFFICIENT"
        else:
            checks["gpu_vram"] = "PASS"

    eligible = [volume for volume in resources.volumes if volume.fixed_local and volume.writable]
    if mount_preference:
        eligible.sort(key=lambda volume: 0 if volume.mount == mount_preference else 1)
    for volume in eligible:
        if volume.free_bytes >= required_bytes + safety_margin_bytes:
            checks["storage"] = "PASS"
            if any(value in {"INSUFFICIENT", "UNKNOWN"} for value in checks.values()):
                break
            return ExecutionRoute(
                route="LOCAL",
                reason="worker volume has measured free space plus safety margin",
                selected_mount=volume.mount,
                required_bytes=required_bytes,
                safety_margin_bytes=safety_margin_bytes,
                required_memory_bytes=required_memory,
                required_gpu_vram_bytes=required_vram,
                resource_checks=checks,
            )
    if "storage" not in checks:
        checks["storage"] = "INSUFFICIENT"
    failed = ", ".join(key for key, value in checks.items() if value != "PASS")
    return ExecutionRoute(
        route="CLOUD_REQUIRED",
        reason=(
            "worker cannot satisfy the requested model/runtime footprint locally"
            + (f" ({failed})" if failed else "")
        ),
        required_bytes=required_bytes,
        safety_margin_bytes=safety_margin_bytes,
        required_memory_bytes=required_memory,
        required_gpu_vram_bytes=required_vram,
        resource_checks=checks,
    )
