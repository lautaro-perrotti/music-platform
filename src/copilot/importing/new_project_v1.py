"""Isolated empty-template projects; no Live mutation or unverified save."""

from __future__ import annotations

import gzip
import hashlib
import json
import ntpath
import re
import shutil
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from copilot.importing.working_copy_manager_v1 import MANIFEST_NAME

MAX_TEMPLATE_BYTES = 64 * 1024 * 1024
TRACK_TAGS = {"AudioTrack", "MidiTrack", "GroupTrack"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _same_path(actual: str | None, expected: Path) -> bool:
    return bool(actual) and Path(actual).resolve() == expected.resolve()


def _empty_template(path: Path) -> bool:
    try:
        with path.open("rb") as raw:
            is_gzip = raw.read(2) == b"\x1f\x8b"
        with (gzip.open(path, "rb") if is_gzip else path.open("rb")) as stream:
            data = stream.read(MAX_TEMPLATE_BYTES + 1)
        if len(data) > MAX_TEMPLATE_BYTES:
            return False
        root = ET.fromstring(data)
        if root.tag != "Ableton" or root.find("LiveSet") is None:
            return False
        live_set = root.find("LiveSet")
        if any(node.tag in TRACK_TAGS or node.tag.endswith("Clip") for node in live_set.iter()):
            return False
        for returned in live_set.iter("ReturnTrack"):
            devices = returned.find(".//Devices")
            if devices is not None and len(devices):
                return False
        return True
    except (OSError, ValueError, ET.ParseError, EOFError):
        return False


def prepare_new_project(*, template_als: str | Path, workspace: str | Path) -> dict[str, Any]:
    """Copy a saved, empty .als to an exclusively created directory in workspace.

    The caller explicitly authorizes workspace. Never reuse an existing project
    or treat a default Live set with musical tracks as an empty template.
    """
    template = Path(template_als).resolve()
    root = Path(workspace).resolve()
    if template.suffix.lower() != ".als" or not template.is_file():
        return {"status": "BLOCKED", "reason": "TEMPLATE_ALS_REQUIRED"}
    if not _empty_template(template):
        return {"status": "BLOCKED", "reason": "TEMPLATE_NOT_VERIFIABLY_EMPTY"}
    if template.parent.joinpath("Samples").is_dir() and any(
        item.is_file() for item in template.parent.joinpath("Samples").rglob("*")
    ):
        return {"status": "BLOCKED", "reason": "TEMPLATE_SAMPLES_NOT_EMPTY"}
    if template.is_relative_to(root):
        return {"status": "BLOCKED", "reason": "TEMPLATE_INSIDE_WORKSPACE"}

    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"copilot-new-{uuid.uuid4().hex}"
    destination.mkdir(exist_ok=False)
    target = destination / template.name
    try:
        source_sha = _digest(template)
        shutil.copy2(template, target)
        if _digest(target) != source_sha or _digest(template) != source_sha or not _empty_template(target):
            raise ValueError("template changed during copy")
        manifest = {
            "milestone": "NEW_PROJECT_TEMPLATE_V1",
            "status": "CREATED",
            "source_als": str(template),
            "working_root": str(destination),
            "working_als": str(target),
            "template_sha256": source_sha,
            "ORIGINAL_UNTOUCHED": True,
            "copy_scope": "empty_template",
        }
        (destination / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
    except (OSError, ValueError) as exc:
        shutil.rmtree(destination)
        return {"status": "BLOCKED", "reason": "COPY_NOT_VERIFIED", "detail": str(exc)}


def open_new_project(
    copy: dict[str, Any],
    *,
    readiness: Callable[..., dict[str, Any]] | None = None,
    daw_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Open only a manifest-backed copy through environment autonomy.

    Requires authoritative *path*, not a coincidentally matching Live set name.
    """
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.runtime.environment_autonomy_v1 import ensure_ableton_ready

    target = Path(str(copy.get("working_als") or ""))
    manifest = target.parent / MANIFEST_NAME
    try:
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            copy.get("status") != "CREATED"
            or recorded != copy
            or not _same_path(recorded.get("working_als"), target)
            or not target.is_file()
            or _digest(target) != recorded["template_sha256"]
            or not _empty_template(target)
        ):
            raise ValueError("copy not intact")
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return {"status": "BLOCKED", "reason": "COPY_NOT_VERIFIED"}

    try:
        ready = (readiness or ensure_ableton_ready)(working_als=target)
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reason": "READINESS_FAILED", "detail": str(exc)}
    session = ready.get("session") or {}
    if (
        ready.get("status") != "PROJECT_READY"
        or not _same_path(session.get("project_path"), target)
        or not session.get("project_identity")
        or session.get("track_count") != 0
    ):
        return {"status": "BLOCKED", "reason": "OPENED_PROJECT_NOT_VERIFIED", "readiness": ready}
    daw = (daw_factory or AbletonTcpAdapter)()
    try:
        daw.connect()
        path = daw.get_session_path().get("path")
        info = daw.get_session_info()
        hello = daw.handshake_info
        if (
            not _same_path(path, target)
            or info.get("track_count") != 0
            or hello.get("mode") == "LEGACY"
            or not hello.get("protocol_version")
        ):
            raise ValueError("live bridge readback differs from readiness")
        browser_advertised = "browser.load" in set(hello.get("capabilities") or ())
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reason": "BRIDGE_READBACK_NOT_VERIFIED", "detail": str(exc)}
    finally:
        daw.disconnect()
    return {
        "status": "OPENED_EMPTY",
        "working_als": str(target),
        "project_identity": session["project_identity"],
        "saved_sha256": recorded["template_sha256"],
        "process_pid": ready.get("same_process_pid"),
        "browser_load_advertised": browser_advertised,
        "browser_load_verified": False,
        "readiness": ready,
    }


def verify_saved_project(
    opened: dict[str, Any],
    *,
    reopened: dict[str, Any],
) -> dict[str, Any]:
    """Verify independently reopened Live readback after a caller-controlled save.

    No save operation is issued here: the bundled bridge does not implement
    one and an unverified UI keystroke is not a persistence guarantee.
    Call only after a distinct reopen, using its authoritative session readback.
    """
    target = Path(str(opened.get("working_als") or ""))
    if opened.get("status") != "OPENED_EMPTY" or not target.is_file():
        return {"status": "BLOCKED", "reason": "OPENED_COPY_REQUIRED"}
    session = reopened.get("session") or {}
    # A distinct, verified process is required. A second read of the same Live
    # session is not a reopen, even when the .als file changed on disk.
    if (
        reopened.get("status") != "PROJECT_READY"
        or not isinstance(opened.get("process_pid"), int)
        or not isinstance(reopened.get("same_process_pid"), int)
        or opened["process_pid"] == reopened["same_process_pid"]
        or not _same_path(session.get("project_path"), target)
        or session.get("project_identity") != opened.get("project_identity")
    ):
        return {"status": "BLOCKED", "reason": "REOPEN_IDENTITY_MISMATCH"}
    if not isinstance(session.get("track_count"), int) or session["track_count"] < 0:
        return {"status": "BLOCKED", "reason": "REOPEN_READBACK_MISSING"}
    digest = _digest(target)
    if digest == opened.get("saved_sha256"):
        return {"status": "BLOCKED", "reason": "SAVE_NOT_OBSERVED_ON_DISK"}
    return {"status": "SAVED_REOPENED", "working_als": str(target), "sha256": digest}


def reopen_and_verify_saved_project(
    opened: dict[str, Any],
    *,
    readiness: Callable[..., dict[str, Any]] | None = None,
    daw_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """After Live is closed externally, relaunch and read back the saved copy.

    Does not force-close another user's process. If the old process still owns
    Live, readiness or the distinct-PID gate will block.
    """
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.runtime.environment_autonomy_v1 import ensure_ableton_ready

    target = Path(str(opened.get("working_als") or ""))
    if opened.get("status") != "OPENED_EMPTY" or not target.is_file():
        return {"status": "BLOCKED", "reason": "OPENED_COPY_REQUIRED"}
    try:
        ready = (readiness or ensure_ableton_ready)(working_als=target)
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reason": "REOPEN_FAILED", "detail": str(exc)}
    result = verify_saved_project(opened, reopened=ready)
    if result["status"] != "SAVED_REOPENED":
        return result
    daw = (daw_factory or AbletonTcpAdapter)()
    try:
        daw.connect()
        if (
            (daw.handshake_info or {}).get("mode") == "LEGACY"
            or not (daw.handshake_info or {}).get("protocol_version")
            or not _same_path(daw.get_session_path().get("path"), target)
            or daw.get_session_info().get("track_count")
            != (ready.get("session") or {}).get("track_count")
        ):
            return {"status": "BLOCKED", "reason": "REOPEN_BRIDGE_READBACK_MISMATCH"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reason": "REOPEN_BRIDGE_READBACK_FAILED", "detail": str(exc)}
    finally:
        daw.disconnect()
    return {**result, "readiness": ready}


def save_new_project_via_windows_ui(
    opened: dict[str, Any],
    *,
    ui: Any = None,
    readiness: Callable[..., dict[str, Any]] | None = None,
    daw_factory: Callable[[], Any] | None = None,
    timeout_s: float = 20.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Save one owned Live window, close it gracefully, then relaunch and verify.

    No Save As, dialog acceptance, process-wide kill, or non-Windows fallback.
    The caller must have obtained ``opened`` from ``open_new_project`` with a
    *newly launched* process (not an already-open Live session).
    """
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.importing.windows_save_ui_v1 import WindowsSaveUI

    def blocked(reason: str) -> dict[str, Any]:
        return {"status": "BLOCKED", "reason": reason}

    if sys.platform != "win32":
        return blocked("WINDOWS_ONLY")
    target = Path(str(opened.get("working_als") or ""))
    launch = (opened.get("readiness") or {}).get("launch") or {}
    owner = launch.get("process_lifecycle") or {}
    profile = (opened.get("readiness") or {}).get("environment") or {}
    executable = (profile.get("ableton") or {}).get("exe_path")
    pid = opened.get("process_pid")
    if (
        opened.get("status") != "OPENED_EMPTY"
        or not isinstance(pid, int) or pid <= 0
        or launch.get("launch") != "started"
        or owner.get("owned") is not True
        or owner.get("pid") != pid
        or not isinstance(executable, str) or not executable
        or not target.is_file()
    ):
        return blocked("OWNED_LIVE_PROCESS_REQUIRED")
    manifest = target.parent / MANIFEST_NAME
    try:
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            recorded.get("milestone") != "NEW_PROJECT_TEMPLATE_V1"
            or recorded.get("ORIGINAL_UNTOUCHED") is not True
            or recorded.get("copy_scope") != "empty_template"
            or not _same_path(recorded.get("working_als"), target)
            or not _same_path(recorded.get("working_root"), target.parent)
        ):
            return blocked("WORKING_COPY_MANIFEST_INVALID")
        initial = target.stat()
        initial_digest = _digest(target)
    except (OSError, ValueError, json.JSONDecodeError):
        return blocked("WORKING_COPY_MANIFEST_INVALID")

    bridge = (daw_factory or AbletonTcpAdapter)()

    def bridge_readback() -> int | None:
        try:
            bridge.connect()
            if (
                (bridge.handshake_info or {}).get("mode") == "LEGACY"
                or not (bridge.handshake_info or {}).get("protocol_version")
                or not _same_path(bridge.get_session_path().get("path"), target)
            ):
                return None
            count = bridge.get_session_info().get("track_count")
            return count if isinstance(count, int) and count >= 0 else None
        except Exception:  # noqa: BLE001
            return None
        finally:
            bridge.disconnect()

    track_count = bridge_readback()
    if track_count is None:
        return blocked("BRIDGE_PATH_OR_TRACKS_UNVERIFIED")
    try:
        driver = ui if ui is not None else WindowsSaveUI()
        before = driver.observe(pid)
        token = before.get("process_token")
        first_windows = before.get("windows") or []
        hwnd = first_windows[0].get("hwnd") if len(first_windows) == 1 else None
        title = re.compile(rf"^\*?{re.escape(target.stem)}\s+-\s+Ableton Live(?:\s|$)", re.I)

        def window_ok(state: dict[str, Any], *, focused: bool) -> bool:
            windows = state.get("windows") or []
            if (
                state.get("alive") is not True
                or not isinstance(token, int) or token <= 0
                or state.get("process_token") != token
                or ntpath.normcase(ntpath.normpath(str(state.get("executable") or "")))
                != ntpath.normcase(ntpath.normpath(executable))
                or len(windows) != 1
            ):
                return False
            window = windows[0]
            return bool(
                isinstance(window.get("hwnd"), int) and window["hwnd"] > 0
                and window["hwnd"] == hwnd
                and window.get("enabled") is True
                and title.match(str(window.get("title") or ""))
                and (not focused or (
                    state.get("foreground_hwnd") == window["hwnd"]
                    and state.get("foreground_pid") == pid
                ))
            )

        if not window_ok(before, focused=False):
            return blocked("WINDOW_OR_MODAL_AMBIGUOUS")
        if not driver.focus(hwnd) or not window_ok(driver.observe(pid), focused=True):
            return blocked("FOREGROUND_NOT_VERIFIED")
        if not window_ok(driver.observe(pid), focused=True) or bridge_readback() != track_count:
            return blocked("SAVE_PRECONDITIONS_CHANGED")
        if not driver.send_save(hwnd, pid):
            return blocked("SAVE_KEYSTROKE_NOT_SENT")
        deadline = clock() + timeout_s
        saved_digest = ""
        while clock() < deadline:
            if not window_ok(driver.observe(pid), focused=True):
                return blocked("WINDOW_OR_MODAL_CHANGED_AFTER_SAVE")
            current = target.stat()
            digest = _digest(target)
            if current.st_mtime_ns != initial.st_mtime_ns and digest != initial_digest:
                saved_digest = digest
                break
            sleep(0.1)
        if not saved_digest:
            return blocked("SAVE_NOT_OBSERVED_ON_DISK")
        if bridge_readback() != track_count or not window_ok(driver.observe(pid), focused=True):
            return blocked("SAVE_IDENTITY_OR_WINDOW_CHANGED")
        if not driver.close_window(hwnd):
            return blocked("OWNED_WINDOW_CLOSE_FAILED")
        deadline = clock() + timeout_s
        while clock() < deadline:
            state = driver.observe(pid)
            if not state.get("alive") and state.get("process_token") == token:
                break
            if state.get("process_token") != token:
                return blocked("PROCESS_IDENTITY_CHANGED_DURING_CLOSE")
            sleep(0.1)
        else:
            return blocked("OWNED_WINDOW_DID_NOT_CLOSE")
        result = reopen_and_verify_saved_project(
            opened, readiness=readiness, daw_factory=daw_factory,
        )
        if result.get("status") != "SAVED_REOPENED":
            return result
        if result.get("sha256") != saved_digest:
            return blocked("DISK_CHANGED_AFTER_REOPEN")
        reopened_count = (result.get("readiness") or {}).get("session", {}).get("track_count")
        # Directly probe the reopened Live set, not just the returned readiness
        # metadata (the helper already compares those two readbacks).
        reopened_bridge = (daw_factory or AbletonTcpAdapter)()
        try:
            reopened_bridge.connect()
            reopened_path = reopened_bridge.get_session_path().get("path")
            reopened_count = reopened_bridge.get_session_info().get("track_count")
        finally:
            reopened_bridge.disconnect()
        if not _same_path(reopened_path, target) or reopened_count != track_count:
            return blocked("REOPEN_TRACKS_OR_PATH_MISMATCH")
        return {**result, "track_count": reopened_count, "save_method": "OWNED_WINDOWS_UI"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reason": "UI_SAVE_VERIFICATION_FAILED", "detail": str(exc)}
