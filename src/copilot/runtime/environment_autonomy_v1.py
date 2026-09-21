"""ENVIRONMENT_AUTONOMY_POLICY_V1.

Portable orchestration for a controlled Ableton certification run.  This
module owns discovery and lifecycle coordination; it does not own musical
state, SafeWrite, capture, DSP, or Astra reasoning.

The only successful terminal state is ``PROJECT_READY`` after the existing
session-readiness handshake and project identity check have completed.
"""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from copilot.daw.ableton_tcp import DEFAULT_HOST, DEFAULT_PORT
from copilot.daw.detect import AbletonDetection, detect_ableton
from copilot.daw.install_remote_script import install_remote_script
from copilot.daw.session_ready_v1 import SESSION_READY, probe_session_ready
from copilot.importing.ableton_launcher_v1 import launch_working_copy, names_match, paths_match
from copilot.importing.working_copy_manager_v1 import create_working_copy
from copilot.platform.ableton import driver_for_system
from copilot.platform.system import host_architecture, host_shell, host_system

MILESTONE = "ENVIRONMENT_AUTONOMY_POLICY_V1"
PROJECT_READY = "PROJECT_READY"
HUMAN_ACTION_REQUIRED = "HUMAN_ACTION_REQUIRED"


@dataclass(frozen=True)
class EnvironmentProfile:
    system: str
    architecture: str
    user: str
    repository_path: str
    python_executable: str
    shell: str
    process_control: tuple[str, ...]
    ableton: AbletonDetection
    host: str
    port: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ableton"] = self.ableton.to_dict()
        payload["process_control"] = list(self.process_control)
        return payload


def discover_environment(
    *,
    repo: str | Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    detector: Callable[..., AbletonDetection] = detect_ableton,
) -> EnvironmentProfile:
    """Discover this host without importing another developer's paths."""
    system = host_system()
    driver = driver_for_system(system)
    available = driver.available_process_controls()
    return EnvironmentProfile(
        system=system,
        architecture=host_architecture(),
        user=getpass.getuser(),
        repository_path=str(Path(repo or Path.cwd()).resolve()),
        python_executable=os.path.abspath(sys.executable),
        shell=host_shell(system),
        process_control=available,
        ableton=detector(port, include_start_menu=False),
        host=host,
        port=port,
    )


def ensure_ableton_ready(
    *,
    working_als: str | Path | None = None,
    source_als: str | Path | None = None,
    project_root: str | Path | None = None,
    copy_scope: str = "project_directory",
    workspace: str | Path | None = None,
    repo: str | Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    deadline_s: float = 12 * 60,
    force_launch: bool = False,
    provisioner: Callable[..., dict[str, Any]] = install_remote_script,
    launcher: Callable[..., dict[str, Any]] = launch_working_copy,
    detector: Callable[..., AbletonDetection] = detect_ableton,
) -> dict[str, Any]:
    """Drive discovery → integration → launch → handshake → project ready.

    ``working_als`` must already be a Copilot-owned working copy.  Supplying
    ``source_als`` is the convenience path that creates/reuses that copy.  A
    raw .als path without a manifest is rejected fail-closed.
    """
    profile = discover_environment(repo=repo, host=host, port=port, detector=detector)
    report: dict[str, Any] = {
        "milestone": MILESTONE,
        "status": "DISCOVERED",
        "lifecycle": ["DISCOVER_ENVIRONMENT"],
        "environment": profile.to_dict(),
        "MUSICAL WRITES": 0,
        "NO MUSICAL WRITE": True,
        "ENVIRONMENT_MUTATION": True,
    }

    copy = _resolve_working_copy(
        working_als=working_als,
        source_als=source_als,
        project_root=project_root,
        copy_scope=copy_scope,
        workspace=workspace,
    )
    report["working_copy"] = copy
    if copy.get("status") not in {"READY", "CREATED", "REUSED"}:
        return _blocked(report, "WORKING_COPY_REQUIRED")
    target = Path(str(copy["working_als"]))

    report["lifecycle"].append("ENSURE_INTEGRATION")
    try:
        integration = provisioner()
    except Exception as exc:  # noqa: BLE001
        integration = {"status": "FAILED", "error": str(exc)}
    report["integration"] = integration
    if str(integration.get("status")) in {"BLOCKED", "BLOCKED_BY_ENVIRONMENT", "FAILED"}:
        return _blocked(report, "INTEGRATION_PROVISION_FAILED")

    report["lifecycle"].append("START_ABLETON")
    try:
        launch = launcher(
            target,
            deadline_s=deadline_s,
            force=force_launch or str(integration.get("status")) in {"INSTALLED", "UPDATED"},
            host=host,
            port=port,
        )
    except TypeError:
        # Small compatibility seam for older test doubles and callers.
        launch = launcher(target, deadline_s=deadline_s, force=force_launch)
    except Exception as exc:  # noqa: BLE001
        launch = {"status": "LAUNCH_FAILED", "reason": str(exc)}
    report["launch"] = launch
    report["lifecycle"].append("WAIT_PROCESS")
    report["lifecycle"].append("WAIT_BRIDGE")
    if launch.get("status") != SESSION_READY:
        control_surface = _control_surface_boundary(profile, detector, port)
        report["control_surface"] = control_surface
        if control_surface.get("required"):
            return _blocked(report, HUMAN_ACTION_REQUIRED)
        return _blocked(report, str(launch.get("status") or "LIVE_UNAVAILABLE"))

    report["lifecycle"].append("PROTOCOL_HELLO")
    report["lifecycle"].append("CAPABILITY_NEGOTIATION")
    probe = probe_session_ready(host, port)
    report["session"] = probe.to_dict()
    if probe.status != SESSION_READY:
        return _blocked(report, probe.reason or "SESSION_NOT_READY")

    report["lifecycle"].append("PROJECT_RECONCILIATION")
    if not paths_match(probe.project_path, target) and not names_match(probe.project_name, target):
        return _blocked(report, "PROJECT_MISMATCH")

    report["lifecycle"].append(PROJECT_READY)
    report["status"] = PROJECT_READY
    report["PROJECT_READY"] = "VERIFIED"
    report["working_als"] = str(target)
    report["project_identity"] = probe.project_identity
    return report


def _resolve_working_copy(
    *,
    working_als: str | Path | None,
    source_als: str | Path | None,
    project_root: str | Path | None,
    copy_scope: str,
    workspace: str | Path | None,
) -> dict[str, Any]:
    if source_als is not None:
        source = Path(source_als)
        root = Path(project_root) if project_root is not None else source.parent
        created = create_working_copy(
            source_als=source,
            project_root=root,
            copy_scope=copy_scope,
            workspace=workspace,
        )
        if created.get("status") not in {"CREATED", "REUSED"}:
            return created
        return {**created, "status": "READY"}
    if working_als is None:
        return {"status": "WORKING_COPY_REQUIRED", "reason": "NO_PROJECT_SPECIFIED"}
    target = Path(working_als)
    manifest = target.parent / "copilot_import.json"
    if not target.is_file() or not manifest.is_file():
        return {
            "status": "WORKING_COPY_REQUIRED",
            "reason": "COPILOT_MANIFEST_REQUIRED",
            "working_als": str(target),
        }
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "WORKING_COPY_REQUIRED", "reason": "INVALID_WORKING_COPY_MANIFEST"}
    if payload.get("ORIGINAL_UNTOUCHED") is not True:
        return {"status": "WORKING_COPY_REQUIRED", "reason": "ORIGINAL_NOT_PROTECTED"}
    return {"status": "READY", "working_als": str(target), "manifest": str(manifest)}


def _control_surface_boundary(
    profile: EnvironmentProfile,
    detector: Callable[..., AbletonDetection],
    port: int,
) -> dict[str, Any]:
    current = detector(port, include_start_menu=False)
    return {
        "required": bool(current.found and not current.port_open),
        "reason": "AbletonMCP Control Surface must be selected in Live before TCP can exist",
        "attempted": ["platform_discovery", "remote_script_provision", "native_launch", "bounded_bridge_wait"],
        "system": profile.system,
        "port_open": current.port_open,
    }


def _blocked(report: dict[str, Any], reason: str) -> dict[str, Any]:
    report["status"] = "BLOCKED"
    report["reason"] = reason
    report["PROJECT_READY"] = "BLOCKED"
    return report


def dependencies_available() -> bool:
    """Cheap local check used by automation/doctor without installing packages."""
    return all(importlib.util.find_spec(name) is not None for name in ("numpy", "soundfile", "pyloudnorm"))
