"""Preserve Ableton crash recovery. Never recover the wrong Live set."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    "se cerrará después",
)
RECOVER_MARKERS = (
    "cerrado inesperadamente",
    "closed unexpectedly",
    "recuperar tu trabajo",
    "recover your work",
)
ACCEPT_BUTTONS = frozenset({"Aceptar", "OK", "Ok"})
DISCARD_BUTTONS = frozenset({"No"})
RECOVER_BUTTONS = frozenset({"Sí", "Si", "Yes"})


def recovered_set_name(dialog_text: str) -> str:
    match = RECOVERED_SET_RE.search(dialog_text or "")
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").strip()


def recovery_action(dialog_text: str, expected_als: str | Path) -> str:
    """recover = same set we launched. discard = different set. unknown = no name."""
    name = recovered_set_name(dialog_text)
    if not name:
        return "unknown"
    expected = Path(expected_als)
    stem = Path(name).stem.lower()
    if stem == expected.stem.lower() or name.lower() == expected.name.lower():
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
    """accept | discard | recover | ''."""
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


def dismiss_unrelated_crash_dialog(expected_als: str | Path) -> dict[str, Any]:
    return dismiss_live_blocking_dialogs(expected_als, recover_policy="match_expected")


def dismiss_live_blocking_dialogs(
    expected_als: str | Path,
    *,
    recover_policy: str = "open_command_line",
) -> dict[str, Any]:
    """Dismiss Live startup dialogs without walking the main Live UI tree.

    Ableton draws these as custom Panes (Ableton Live Window Class), not
    native #32770 message boxes. Query by HWND + FindFirst(Name, Button).

    recover_policy:
      open_command_line — always No on recover-work (launcher opened a specific .als
        after quarantining Crash/). Clicking Sí after a force-kill can AV Live.
      match_expected — recover only when the dialog names the same set.
    """
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
    return (
        (value or "")
        .casefold()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
    )


def _buttons_for_target(target: str) -> set[str]:
    if target == "accept":
        return set(ACCEPT_BUTTONS)
    if target == "discard":
        return set(DISCARD_BUTTONS)
    if target == "recover":
        return set(RECOVER_BUTTONS)
    return set()


def _ableton_live_hwnds() -> list[int]:
    import ctypes
    from ctypes import POINTER, WINFUNCTYPE, byref, wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    WNDENUMPROC = WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    GetWindowThreadProcessId.argtypes = [wintypes.HWND, POINTER(wintypes.DWORD)]
    IsWindowVisible = user32.IsWindowVisible
    QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
    QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        POINTER(wintypes.DWORD),
    ]
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    hwnds: list[int] = []

    def _path(pid: int) -> str:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(32768)
            if QueryFullProcessImageNameW(handle, 0, buf, byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(handle)

    def _enum(hwnd: int, _lparam: int) -> bool:
        if not IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, byref(pid))
        path = _path(int(pid.value))
        if "Ableton Live" in path:
            hwnds.append(int(hwnd))
        return True

    callback = WNDENUMPROC(_enum)
    user32.EnumWindows(callback, 0)
    return hwnds


def _find_dialog_buttons(hwnds: list[int]) -> dict[str, Any]:
    script = _uia_fromhandle_script(hwnds, click=())
    payload = _run_uia_script(script)
    buttons = [str(item) for item in (payload.get("buttons") or [])]
    names = {_fold(item) for item in buttons}
    kind = "NONE"
    if "no" in names:
        kind = "RECOVER_WORK"
    elif names.intersection({"aceptar", "ok"}):
        kind = "FATAL_ERROR"
    return {"kind": kind, "buttons": buttons, "text": str(payload.get("text") or "")}


def _invoke_named_button_on_hwnds(hwnds: list[int], expected: set[str]) -> str | None:
    if not hwnds or not expected:
        return None
    payload = _run_uia_script(_uia_fromhandle_script(hwnds, click=tuple(sorted(expected))))
    clicked = payload.get("clicked")
    return str(clicked) if clicked else None


def _uia_fromhandle_script(hwnds: list[int], *, click: tuple[str, ...]) -> str:
    hwnd_list = ",".join(str(int(item)) for item in hwnds)
    want = ", ".join("'" + item.replace("'", "''") + "'" for item in click)
    click_block = ""
    if click:
        click_block = (
            f"$want = @({want})\n"
            "        if ($btn -and ($want -contains $btn.Current.Name)) {\n"
            "          $inv = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)\n"
            "          $inv.Invoke()\n"
            "          $clicked = $btn.Current.Name\n"
            "        }\n"
        )
    script = f"""
Add-Type -AssemblyName UIAutomationClient
$hwnds = @({hwnd_list})
$btnCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Button)
$names = @('No','Sí','Si','Yes','Aceptar','OK','Ok')
$found = @()
$clicked = ''
foreach ($h in $hwnds) {{
  try {{
    $el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$h)
  }} catch {{ continue }}
  if (-not $el) {{ continue }}
  foreach ($n in $names) {{
    $nameCond = New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::NameProperty, $n)
    $and = New-Object System.Windows.Automation.AndCondition($nameCond, $btnCond)
    $btn = $el.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $and)
    if (-not $btn) {{ continue }}
    $found += [string]$btn.Current.Name
CLICK_PLACEHOLDER
  }}
}}
$uniq = @($found | Select-Object -Unique)
$result = @{{ buttons = $uniq; clicked = $clicked; text = ($uniq -join ' ') }}
$result | ConvertTo-Json -Compress
"""
    return script.replace("CLICK_PLACEHOLDER", click_block)


def _run_uia_script(script: str) -> dict[str, Any]:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"parse_error": raw[:400]}
    return parsed if isinstance(parsed, dict) else {}
