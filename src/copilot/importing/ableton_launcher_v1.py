"""ABLETON_LAUNCHER_V1 — open a working-copy .als and wait for SESSION_READY."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from copilot.daw.ableton_tcp import AbletonTcpAdapter, DEFAULT_HOST, DEFAULT_PORT
from copilot.daw.detect import detect_ableton
from copilot.daw.session_ready_v1 import SESSION_READY, probe_session_ready, wait_for_session
from copilot.importing.crash_recovery_v1 import (
    dismiss_live_blocking_dialogs,
    inspect_recovery_metadata,
    preserve_crash_recovery,
    quarantine_controlled_recovery_metadata,
)
from copilot.platform.ableton import driver_for_system

MILESTONE = "ABLETON_LAUNCHER_V1"
CRASH_CFG_REL = Path("Preferences") / "CrashDetection.cfg"
PROCESS_POLL_S = 0.25
DIALOG_POLL_S = 0.25
SHUTDOWN_TIMEOUT_S = 30.0
MAX_AUTOMATIC_RESTARTS = 1
READY_STABILITY_S = 5.0


def launch_working_copy(
    working_als: str | Path,
    *,
    deadline_s: float = 12 * 60,
    force: bool = False,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    als = Path(working_als)
    if not als.is_file():
        return {
            "milestone": MILESTONE,
            "status": "WORKING_ALS_MISSING",
            "working_als": str(als),
        }
    detection = detect_ableton(port)
    exe = detection.exe_path
    if not exe:
        return {
            "milestone": MILESTONE,
            "status": "ABLETON_NOT_FOUND",
            "working_als": str(als),
        }

    already = probe_session_ready(host, port)
    if (
        not force
        and already.status == SESSION_READY
        and (paths_match(already.project_path, als) or names_match(already.project_name, als))
    ):
        if _snapshot_ok(host, port):
            return {
                "milestone": MILESTONE,
                "status": SESSION_READY,
                "working_als": str(als),
                "opened_path": already.project_path,
                "opened_name": already.project_name,
                "project_identity": already.project_identity,
                "session": already.to_dict(),
                "launch": "already_open",
            }

    if detection.process_running:
        return {
            "milestone": MILESTONE,
            "status": "LIVE_SESSION_CONFLICT",
            "working_als": str(als),
            "reason": "A Live process is already running and is not proven to be Copilot-owned.",
            "force_requested": force,
            "detection": detection.to_dict(),
        }

    recovery_before = inspect_recovery_metadata(detection.prefs_root, als)
    if recovery_before.get("classification") == "RECOVERY_OF_ORIGINAL_PROJECT":
        return {
            "milestone": MILESTONE,
            "status": "RECOVERY_OF_ORIGINAL_PROJECT",
            "working_als": str(als),
            "recovery_metadata": recovery_before,
            "ORIGINAL_SET_ON_DISK_UNTOUCHED": True,
        }
    if recovery_before.get("classification") == "UNKNOWN_RECOVERY_STATE":
        return {
            "milestone": MILESTONE,
            "status": "UNKNOWN_RECOVERY_STATE",
            "working_als": str(als),
            "recovery_metadata": recovery_before,
            "ORIGINAL_SET_ON_DISK_UNTOUCHED": True,
        }

    crash = preserve_crash_recovery(detection.prefs_root, quarantine=False)
    crash_after = preserve_crash_recovery(detection.prefs_root, quarantine=True)
    recovery_metadata = quarantine_controlled_recovery_metadata(
        detection.prefs_root, als
    )
    _clear_crash_flag(detection.prefs_root)
    try:
        process = _start_live(exe, als)
    except OSError as exc:
        return {
            "milestone": MILESTONE,
            "status": "LAUNCH_FAILED",
            "working_als": str(als),
            "reason": str(exc),
            "crash_recovery": crash_after or crash,
            "recovery_metadata": recovery_metadata,
        }

    dialog_events: list[dict[str, Any]] = []
    stop = threading.Event()
    modal_ack_failed = False

    def _poll_crash_dialog() -> None:
        nonlocal modal_ack_failed
        while True:
            result = dismiss_live_blocking_dialogs(als, recover_policy="open_command_line")
            if result.get("status") != "NO_DIALOG":
                if result.get("status") == "MODAL_ACK_FAILED":
                    if not modal_ack_failed:
                        dialog_events.append(result)
                        modal_ack_failed = True
                else:
                    dialog_events.append(result)
            if stop.wait(DIALOG_POLL_S):
                break

    poller = threading.Thread(target=_poll_crash_dialog, daemon=True)
    poller.start()
    try:
        ready, relaunches = _wait_ready_allowing_one_relaunch(
            als,
            exe,
            detection.prefs_root,
            host,
            port,
            deadline_s=deadline_s,
            dialog_events=dialog_events,
            process=process,
        )
        if ready is not None and ready.status == SESSION_READY:
            ready = _wait_for_stable_ready(
                ready,
                host,
                port,
                process,
                dialog_events=dialog_events,
                modal_ack_failed=lambda: modal_ack_failed,
            )
    finally:
        stop.set()
    payload_crash = crash_after or crash
    extra = {
        "crash_recovery": payload_crash,
        "dialog_events": dialog_events[-8:],
        "controlled_relaunches": relaunches,
        "restart_required": bool(force),
        "restart_count": 0,
        "max_automatic_restarts": MAX_AUTOMATIC_RESTARTS,
        "same_process_pid": process.pid,
    }
    if ready is None or ready.status != SESSION_READY:
        shutdown = _shutdown_owned_process(process)
        return {
            "milestone": MILESTONE,
            "status": (ready.status if ready is not None else "LIVE_UNAVAILABLE"),
            "working_als": str(als),
            "session": ready.to_dict() if ready is not None else {},
            "launch": "started",
            **extra,
            "recovery_metadata": recovery_metadata,
            "process_lifecycle": shutdown,
        }
    if not paths_match(ready.project_path, als) and not names_match(ready.project_name, als):
        return {
            "milestone": MILESTONE,
            "status": "OPENED_PROJECT_MISMATCH",
            "working_als": str(als),
            "opened_path": ready.project_path,
            "opened_name": ready.project_name,
            "session": ready.to_dict(),
            **extra,
            "recovery_metadata": recovery_metadata,
        }
    return {
        "milestone": MILESTONE,
        "status": SESSION_READY,
        "working_als": str(als),
        "opened_path": ready.project_path,
        "opened_name": ready.project_name,
        "project_identity": ready.project_identity,
        "session": ready.to_dict(),
        "launch": "started",
        **extra,
        "recovery_metadata": recovery_metadata,
        "process_lifecycle": {
            "pid": process.pid,
            "owned": True,
            "left_running": True,
            "same_pid_preserved_after_ack": True,
        },
        "restart_required": bool(force),
        "restart_count": 0,
        "max_automatic_restarts": MAX_AUTOMATIC_RESTARTS,
        "remote_script_restart_policy": "ONE_RESTART_ONLY_IF_VERSION_CHANGED",
    }


def _wait_for_stable_ready(
    initial: Any,
    host: str,
    port: int,
    process: subprocess.Popen[bytes],
    *,
    dialog_events: list[dict[str, Any]],
    modal_ack_failed: Any,
) -> Any:
    """Keep the startup watcher alive after the first handshake.

    Trial UI can appear after the Remote Script answers its first request.  A
    first successful handshake is therefore not enough to declare readiness.
    This bounded stability window never relaunches Live; it only lets the
    existing modal watcher acknowledge a known safe dialog in-place.
    """
    deadline = time.monotonic() + READY_STABILITY_S
    stable_since: float | None = None
    last = initial
    while time.monotonic() < deadline:
        if modal_ack_failed():
            return replace(
                last,
                status="MODAL_ACK_FAILED",
                reason="Known Trial modal did not satisfy the dismissal postcondition",
                writes_permitted=False,
            )
        if process.poll() is not None:
            return replace(
                last,
                status="LIVE_UNAVAILABLE",
                reason="Ableton exited during readiness stability window",
                writes_permitted=False,
            )
        current = probe_session_ready(host, port)
        if current.status == SESSION_READY:
            last = current
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= READY_STABILITY_S:
                return current
        else:
            last = current
            stable_since = None
        time.sleep(DIALOG_POLL_S)
    return last


def paths_match(opened: str | None, expected: Path) -> bool:
    if not opened:
        return False
    left = _norm_path(opened)
    right = _norm_path(expected)
    return left == right or left.endswith(right) or right.endswith(left)


def names_match(opened_name: str | None, expected: Path) -> bool:
    if not opened_name:
        return False
    return Path(opened_name).stem.lower() == expected.stem.lower()


def _norm_path(value: str | Path) -> str:
    return str(Path(str(value))).replace("\\", "/").rstrip("/").casefold().strip()


def _snapshot_ok(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    daw = AbletonTcpAdapter(host, port)
    try:
        daw.connect()
        daw.snapshot(include_notes=False)
        return True
    except Exception:
        return False
    finally:
        daw.disconnect()


def _start_live(exe: str, als: Path) -> subprocess.Popen[bytes]:
    # Platform process semantics belong to the driver, not the launcher.
    return driver_for_system().launch(exe, als)


def _wait_ready_allowing_one_relaunch(
    als: Path,
    exe: str,
    prefs_root: str | None,
    host: str,
    port: int,
    *,
    deadline_s: float,
    dialog_events: list[dict[str, Any]],
    process: subprocess.Popen[bytes],
) -> tuple[Any, int]:
    deadline = time.monotonic() + deadline_s
    last = None
    while time.monotonic() < deadline:
        chunk = min(20.0, deadline - time.monotonic())
        if chunk <= 0:
            break
        last = wait_for_session(
            deadline_s=chunk,
            probe=lambda: probe_session_ready(host, port),
        )
        if last.status == SESSION_READY:
            return last, 0
        if process.poll() is not None:
            dialog_events.append(
                {
                    "status": "PROCESS_EXITED_BEFORE_SESSION_READY",
                    "pid": process.pid,
                    "returncode": process.returncode,
                }
            )
            break
    return last, 0


def _shutdown_owned_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_s: float = SHUTDOWN_TIMEOUT_S,
) -> dict[str, Any]:
    """Close only the process launched in this invocation; force is last resort."""
    driver = driver_for_system()
    result: dict[str, Any] = {
        "pid": process.pid,
        "owned": True,
        "request_sent": False,
        "force_sent": False,
        "exited": process.poll() is not None,
    }
    if result["exited"]:
        result["returncode"] = process.returncode
        return result
    driver.request_shutdown(process)
    result["request_sent"] = True
    deadline = time.monotonic() + timeout_s
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(min(PROCESS_POLL_S, max(0.0, deadline - time.monotonic())))
    if process.poll() is None:
        driver.force_shutdown(process)
        result["force_sent"] = True
        force_deadline = time.monotonic() + timeout_s
        while process.poll() is None and time.monotonic() < force_deadline:
            time.sleep(min(PROCESS_POLL_S, max(0.0, force_deadline - time.monotonic())))
    result["exited"] = process.poll() is not None
    result["returncode"] = process.returncode
    return result


def _clear_crash_flag(prefs_root: str | None) -> None:
    if not prefs_root:
        return
    cfg = Path(prefs_root) / CRASH_CFG_REL
    if not cfg.is_file():
        return
    try:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    updated = text.replace('String Value = "Running"', 'String Value = ""')
    if updated == text:
        return
    try:
        cfg.write_text(updated, encoding="utf-8")
    except OSError:
        return
