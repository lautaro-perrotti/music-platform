"""Preserve Ableton crash recovery. Never recover the wrong Live set."""

from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from copilot.platform.modals import modal_driver_for_system

MILESTONE = "CRASH_RECOVERY_V1"
KEEP_ROOT = Path.home() / "CopilotProjects" / "_crash_recovery_keep"
RECOVERED_SET_RE = re.compile(
    r'Live Set\s+"([^"]+)"|Live Set\s+\u201c([^\u201d]+)\u201d',
    re.IGNORECASE,
)
FATAL_MARKERS = (
    "serious program error",
    "live will shut down",
    "unhandled exception",
    "report a crash",
    "error grave",
    "se cerrara despues",
)
RECOVER_MARKERS = (
    "cerrado inesperadamente",
    "closed unexpectedly",
    "recuperar tu trabajo",
    "recover your work",
)
ACCEPT_BUTTONS = frozenset({"Aceptar", "OK", "Ok"})
DISCARD_BUTTONS = frozenset({"No"})
RECOVER_BUTTONS = frozenset({"S\u00ed", "Si", "Yes"})


def recovered_set_name(dialog_text: str) -> str:
    match = RECOVERED_SET_RE.search(dialog_text or "")
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").strip()


def recovery_action(dialog_text: str, expected_als: str | Path) -> str:
    """Recover only the set we launched; discard a different or unnamed set."""
    name = recovered_set_name(dialog_text)
    if not name:
        return "unknown"
    expected = Path(expected_als)
    stem = Path(name).stem.casefold()
    if stem == expected.stem.casefold() or name.casefold() == expected.name.casefold():
        return "recover"
    return "discard"


def classify_live_dialog(text: str) -> str:
    blob = _fold(text)
    if any(_fold(marker) in blob for marker in FATAL_MARKERS):
        return "FATAL_ERROR"
    if any(_fold(marker) in blob for marker in RECOVER_MARKERS):
        return "RECOVER_WORK"
    return "NONE"


def recover_click_target(
    dialog_text: str,
    expected_als: str | Path,
    *,
    recover_policy: str = "match_expected",
) -> str:
    """Return only a registered action: accept, discard, recover, or empty."""
    kind = classify_live_dialog(dialog_text)
    if kind == "FATAL_ERROR":
        return "accept"
    if kind != "RECOVER_WORK":
        return ""
    if recover_policy == "open_command_line":
        return "discard"
    action = recovery_action(dialog_text, expected_als)
    return "discard" if action in {"discard", "unknown"} else "recover"


def preserve_crash_recovery(
    prefs_root: str | Path | None,
    *,
    quarantine: bool = False,
) -> dict[str, Any]:
    if not prefs_root:
        return {"milestone": MILESTONE, "status": "NO_PREFS", "preserved": False}
    crash = Path(prefs_root) / "Preferences" / "Crash"
    if not crash.is_dir():
        return {"milestone": MILESTONE, "status": "NO_CRASH_FOLDER", "preserved": False}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = KEEP_ROOT / stamp
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(crash, dest)
    quarantined = False
    if quarantine:
        shutil.rmtree(crash, ignore_errors=True)
        quarantined = not crash.exists()
    return {
        "milestone": MILESTONE,
        "status": "PRESERVED",
        "preserved": True,
        "quarantined": quarantined,
        "source": str(crash),
        "backup": str(dest),
        "ORIGINAL_SET_ON_DISK_UNTOUCHED": True,
    }


def inspect_recovery_metadata(
    prefs_root: str | Path | None,
    expected_als: str | Path | None = None,
) -> dict[str, Any]:
    """Classify Live's global recovery pointer without changing it."""
    if not prefs_root:
        return {"status": "NO_PREFS", "classification": "UNKNOWN_RECOVERY_STATE"}
    path = Path(prefs_root) / "Preferences" / "CrashRecoveryInfo.cfg"
    if not path.is_file():
        return {
            "status": "NO_METADATA",
            "classification": "STALE_RECOVERY_METADATA",
            "path": str(path),
        }
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {
            "status": "READ_FAILED",
            "classification": "UNKNOWN_RECOVERY_STATE",
            "path": str(path),
            "error": str(exc),
        }
    decoded = raw.decode("utf-16le", errors="ignore") + raw.decode(
        "utf-8", errors="ignore"
    )
    normalized = decoded.replace("\x00", "").replace("\\", "/").casefold()
    expected = str(expected_als or "").replace("\\", "/").casefold()
    if expected and expected in normalized:
        classification = "RECOVERY_OF_CONTROLLED_WORKING_COPY"
    elif "copilotprojects/" in normalized and ".als" in normalized:
        classification = "RECOVERY_OF_CONTROLLED_WORKING_COPY"
    elif ".als" in normalized:
        classification = "RECOVERY_OF_ORIGINAL_PROJECT"
    else:
        classification = "UNKNOWN_RECOVERY_STATE"
    return {
        "status": "PRESENT",
        "classification": classification,
        "path": str(path),
        "expected_match": bool(expected and expected in normalized),
    }


def quarantine_controlled_recovery_metadata(
    prefs_root: str | Path | None,
    expected_als: str | Path | None = None,
) -> dict[str, Any]:
    """Move only identified Copilot recovery metadata to recoverable storage."""
    observed = inspect_recovery_metadata(prefs_root, expected_als)
    if observed.get("classification") != "RECOVERY_OF_CONTROLLED_WORKING_COPY":
        return {**observed, "quarantined": False}
    source = Path(str(observed["path"]))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = KEEP_ROOT / stamp / source.name
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    except OSError as exc:
        return {
            **observed,
            "quarantined": False,
            "status": "QUARANTINE_FAILED",
            "error": str(exc),
        }
    return {
        **observed,
        "quarantined": True,
        "backup": str(destination),
        "ORIGINAL_SET_ON_DISK_UNTOUCHED": True,
    }


def dismiss_unrelated_crash_dialog(expected_als: str | Path) -> dict[str, Any]:
    return dismiss_live_blocking_dialogs(expected_als, recover_policy="match_expected")


def dismiss_live_blocking_dialogs(
    expected_als: str | Path,
    *,
    recover_policy: str = "open_command_line",
) -> dict[str, Any]:
    """Handle only known Live startup dialogs through the platform driver."""
    hwnds = _ableton_live_hwnds()
    if not hwnds:
        return {"status": "NO_DIALOG", "milestone": MILESTONE, "kind": "NONE"}
    found = _find_dialog_buttons(hwnds)
    buttons = list(found.get("buttons") or [])
    kind = str(found.get("kind") or "NONE")
    if kind == "NONE":
        return {"status": "NO_DIALOG", "milestone": MILESTONE, "kind": "NONE", "hwnds": hwnds}
    text = str(found.get("text") or kind)
    if kind == "RECOVER_WORK" and recover_policy == "match_expected":
        target = recover_click_target(text, expected_als, recover_policy=recover_policy)
    elif kind == "RECOVER_WORK":
        target = "discard"
    elif kind == "FATAL_ERROR":
        target = "accept"
    else:
        target = ""
    wanted = _buttons_for_target(target)
    clicked = _invoke_named_button_on_hwnds(hwnds, wanted) if wanted else None
    return {
        "milestone": MILESTONE,
        "status": "DISMISSED" if clicked else "DIALOG_PRESENT",
        "kind": kind,
        "action": target or recovery_action(text, expected_als),
        "dialog": text,
        "clicked": clicked,
        "buttons": buttons,
        "recover_policy": recover_policy,
        "hwnds": hwnds,
    }


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "").casefold()
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _buttons_for_target(target: str) -> set[str]:
    if target == "accept":
        return set(ACCEPT_BUTTONS)
    if target == "discard":
        return set(DISCARD_BUTTONS)
    if target == "recover":
        return set(RECOVER_BUTTONS)
    return set()


def _ableton_live_hwnds() -> list[int]:
    return modal_driver_for_system().window_handles()


def _find_dialog_buttons(hwnds: list[int]) -> dict[str, Any]:
    payload = modal_driver_for_system().find_buttons(hwnds)
    buttons = [str(item) for item in (payload.get("buttons") or [])]
    names = {_fold(item) for item in buttons}
    kind = "NONE"
    if "no" in names:
        kind = "RECOVER_WORK"
    elif names.intersection({"aceptar", "ok"}):
        kind = "FATAL_ERROR"
    return {"kind": kind, "buttons": buttons, "text": str(payload.get("text") or "")}


def _invoke_named_button_on_hwnds(hwnds: list[int], expected: set[str]) -> str | None:
    return modal_driver_for_system().invoke_button(hwnds, expected)
