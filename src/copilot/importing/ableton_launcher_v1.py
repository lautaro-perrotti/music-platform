"""ABLETON_LAUNCHER_V1 — open a working-copy .als and wait for SESSION_READY."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.detect import detect_ableton
from copilot.daw.session_ready_v1 import SESSION_READY, probe_session_ready, wait_for_session
from copilot.importing.crash_recovery_v1 import (
    dismiss_live_blocking_dialogs,
    preserve_crash_recovery,
)

MILESTONE = "ABLETON_LAUNCHER_V1"
CRASH_CFG_REL = Path("Preferences") / "CrashDetection.cfg"
LIVE_IMAGE_NAMES = (
    "Ableton Live 12 Trial.exe",
    "Ableton Live 12.exe",
    "Ableton Live 11 Trial.exe",
    "Ableton Live 11.exe",
)


def launch_working_copy(
    working_als: str | Path,
    *,
    deadline_s: float = 12 * 60,
    force: bool = False,
) -> dict[str, Any]:
    als = Path(working_als)
    if not als.is_file():
        return {
            "milestone": MILESTONE,
            "status": "WORKING_ALS_MISSING",
            "working_als": str(als),
        }
    detection = detect_ableton()
    exe = detection.exe_path
    if not exe:
        return {
            "milestone": MILESTONE,
            "status": "ABLETON_NOT_FOUND",
            "working_als": str(als),
        }

    already = probe_session_ready()
    if (
        not force
        and already.status == SESSION_READY
        and (paths_match(already.project_path, als) or names_match(already.project_name, als))
    ):
        if _snapshot_ok():
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

    crash = preserve_crash_recovery(detection.prefs_root, quarantine=False)
    _stop_live()
    crash_after = preserve_crash_recovery(detection.prefs_root, quarantine=True)
    _clear_crash_flag(detection.prefs_root)
    time.sleep(1.5)
    _clear_crash_flag(detection.prefs_root)
    try:
        _start_live(exe, als)
    except OSError as exc:
        return {
            "milestone": MILESTONE,
            "status": "LAUNCH_FAILED",
            "working_als": str(als),
            "reason": str(exc),
            "crash_recovery": crash_after or crash,
        }

    dialog_events: list[dict[str, Any]] = []
    stop = threading.Event()

    def _poll_crash_dialog() -> None:
        while True:
            result = dismiss_live_blocking_dialogs(als, recover_policy="open_command_line")
            if result.get("status") != "NO_DIALOG":
                dialog_events.append(result)
            if stop.wait(0.7):
                break

    poller = threading.Thread(target=_poll_crash_dialog, daemon=True)
    poller.start()
    try:
        ready, relaunches = _wait_ready_allowing_one_relaunch(
            als,
            exe,
            detection.prefs_root,
            deadline_s=deadline_s,
            dialog_events=dialog_events,
        )
    finally:
        stop.set()
    payload_crash = crash_after or crash
    extra = {
        "crash_recovery": payload_crash,
        "dialog_events": dialog_events[-8:],
        "controlled_relaunches": relaunches,
    }
    if ready is None or ready.status != SESSION_READY:
        return {
            "milestone": MILESTONE,
            "status": (ready.status if ready is not None else "LIVE_UNAVAILABLE"),
            "working_als": str(als),
            "session": ready.to_dict() if ready is not None else {},
            "launch": "started",
            **extra,
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
    }


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
    return str(value).replace("/", "\\").lower().strip()


def _snapshot_ok() -> bool:
    daw = AbletonTcpAdapter()
    try:
        daw.connect()
        daw.snapshot(include_notes=False)
        return True
    except Exception:
        return False
    finally:
        daw.disconnect()


def _start_live(exe: str, als: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [exe, str(als)],
        cwd=str(als.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_ready_allowing_one_relaunch(
    als: Path,
    exe: str,
    prefs_root: str | None,
    *,
    deadline_s: float,
    dialog_events: list[dict[str, Any]],
) -> tuple[Any, int]:
    deadline = time.time() + deadline_s
    relaunches = 0
    last = None
    while time.time() < deadline:
        chunk = min(20.0, deadline - time.time())
        if chunk <= 0:
            break
        last = wait_for_session(deadline_s=chunk)
        if last.status == SESSION_READY:
            return last, relaunches
        if detect_ableton().process_running:
            continue
        if relaunches >= 1:
            break
        _stop_live()
        preserve_crash_recovery(prefs_root, quarantine=True)
        _clear_crash_flag(prefs_root)
        time.sleep(2.0)
        _clear_crash_flag(prefs_root)
        try:
            _start_live(exe, als)
        except OSError:
            break
        relaunches += 1
        dialog_events.append(
            {
                "status": "CONTROLLED_RELAUNCH",
                "reason": "LIVE_EXITED_BEFORE_SESSION_READY",
            }
        )
    return last, relaunches


def _stop_live() -> None:
    for image in LIVE_IMAGE_NAMES:
        subprocess.run(
            ["taskkill", "/IM", image, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    subprocess.run(
        ["taskkill", "/IM", "Ableton Crash Reporter.exe", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        if not detect_ableton().process_running:
            time.sleep(3.0)
            return
        time.sleep(0.4)


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
