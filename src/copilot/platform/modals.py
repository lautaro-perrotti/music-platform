"""Bounded, platform-owned Ableton modal automation.

Only registered buttons are ever invoked.  Unknown UI is returned as
``UNKNOWN_MODAL`` and is never given a default Enter/accept action.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import time
from typing import Any


TRIAL_RE = re.compile(
    r"(?:time\s+remaining|tiempo\s+restante)\s*:\s*(\d+)",
    re.I,
)


def classify_modal_text(text: str) -> dict[str, Any]:
    """Classify text without performing UI actions."""
    blob = (text or "").casefold()
    trial_match = TRIAL_RE.search(text or "")
    if trial_match or (
        ("saving and exporting" in blob or "guardar y exportar" in blob)
        and ("trial" in blob or "tiempo restante" in blob)
    ):
        return {
            "kind": "TRIAL_STATUS_ACKNOWLEDGEMENT",
            "safe_action": "acknowledge",
            "license_mode": "TRIAL",
            "trial_days_remaining": int(trial_match.group(1)) if trial_match else None,
        }
    if "save" in blob and "working copy" in blob or "guardar" in blob and "copia" in blob:
        return {"kind": "SAVE_WORKING_COPY", "safe_action": "policy_required"}
    if "recover" in blob or "recuperar" in blob:
        return {"kind": "RECOVERY_DIALOG", "safe_action": "identity_required"}
    if "missing media" in blob or "medios faltantes" in blob:
        return {"kind": "MISSING_MEDIA_WARNING", "safe_action": "registered_only"}
    if text.strip():
        return {"kind": "UNKNOWN_MODAL", "safe_action": "fail_closed"}
    return {"kind": "NONE", "safe_action": "none"}


class PlatformModalDriver:
    def window_handles(self) -> list[int]:
        return []

    def find_buttons(self, hwnds: list[int]) -> dict[str, Any]:
        return {"buttons": [], "text": ""}

    def invoke_button(self, hwnds: list[int], expected: set[str]) -> str | None:
        return None

    def invoke_button_verified(
        self, hwnds: list[int], expected: set[str], *, trial_fallback: bool = False
    ) -> dict[str, Any]:
        clicked = self.invoke_button(hwnds, expected)
        return {"clicked": clicked, "fallback_used": False}


class WindowsModalDriver(PlatformModalDriver):
    """Windows UI Automation implementation; no Windows code escapes here."""

    def window_handles(self) -> list[int]:
        import ctypes
        from ctypes import POINTER, WINFUNCTYPE, byref, wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        callback_type = WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        get_pid = user32.GetWindowThreadProcessId
        get_pid.argtypes = [wintypes.HWND, POINTER(wintypes.DWORD)]
        is_visible = user32.IsWindowVisible
        query_path = kernel32.QueryFullProcessImageNameW
        query_path.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, POINTER(wintypes.DWORD)]
        hwnds: list[int] = []

        def process_path(pid: int) -> str:
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return ""
            try:
                buffer = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(32768)
                return buffer.value if query_path(handle, 0, buffer, byref(size)) else ""
            finally:
                kernel32.CloseHandle(handle)

        def visit(hwnd: int, _lparam: int) -> bool:
            if not is_visible(hwnd):
                return True
            pid = wintypes.DWORD()
            get_pid(hwnd, byref(pid))
            if "Ableton Live" in process_path(int(pid.value)):
                hwnds.append(int(hwnd))
            return True

        callback = callback_type(visit)
        user32.EnumWindows(callback, 0)
        return hwnds

    def find_buttons(self, hwnds: list[int]) -> dict[str, Any]:
        return _run_uia_script(_uia_from_handles(hwnds, click=()))

    def invoke_button(self, hwnds: list[int], expected: set[str]) -> str | None:
        result = self.invoke_button_verified(hwnds, expected, trial_fallback=False)
        clicked = result.get("clicked")
        return str(clicked) if clicked else None

    def invoke_button_verified(
        self, hwnds: list[int], expected: set[str], *, trial_fallback: bool = False
    ) -> dict[str, Any]:
        if not hwnds or not expected:
            return {"clicked": None, "fallback_used": False}
        payload = _run_uia_script(
            _uia_from_handles(
                hwnds,
                click=tuple(sorted(expected)),
                trial_fallback=trial_fallback,
            )
        )
        return {
            "clicked": payload.get("clicked") or None,
            "fallback_used": bool(payload.get("fallback_used")),
        }


class MacOSModalDriver(PlatformModalDriver):
    """Safe default until an accessibility permission is explicitly granted."""


class LinuxModalDriver(PlatformModalDriver):
    """Safe default; no arbitrary desktop automation is attempted."""


def modal_driver_for_system(system: str | None = None) -> PlatformModalDriver:
    name = system or platform.system()
    if name == "Windows":
        return WindowsModalDriver()
    if name == "Darwin":
        return MacOSModalDriver()
    return LinuxModalDriver()


class KnownModalHandler:
    """Bounded modal policy shared by launchers and certification flows."""

    def __init__(
        self, driver: PlatformModalDriver | None = None, *, verify_timeout_s: float = 3.0
    ) -> None:
        self.driver = driver or modal_driver_for_system()
        self.verify_timeout_s = verify_timeout_s

    def inspect(self) -> dict[str, Any]:
        handles = self.driver.window_handles()
        if not handles:
            return {"status": "NO_MODAL", "kind": "NONE", "hwnds": []}
        payload = self.driver.find_buttons(handles)
        # The main Live window exposes the browser/arrangement text tree too.
        # Without an actionable control it is content, not a blocking modal.
        if not payload.get("buttons"):
            return {"status": "NO_MODAL", "kind": "NONE", "hwnds": handles}
        text = str(payload.get("text") or "")
        classification = classify_modal_text(text)
        return {"status": "MODAL_PRESENT", "hwnds": handles, **classification, "raw": payload}

    def acknowledge_trial(self, observation: dict[str, Any]) -> dict[str, Any]:
        if observation.get("kind") != "TRIAL_STATUS_ACKNOWLEDGEMENT":
            return {**observation, "status": "NOT_APPLICABLE"}
        action = self.driver.invoke_button_verified(
            observation.get("hwnds") or [],
            {"OK", "Ok", "Aceptar"},
            trial_fallback=True,
        )
        deadline = time.monotonic() + self.verify_timeout_s
        remaining: dict[str, Any] = {}
        while time.monotonic() < deadline:
            handles = self.driver.window_handles()
            if not handles:
                remaining = {"kind": "NONE"}
                break
            payload = self.driver.find_buttons(handles)
            remaining = classify_modal_text(str(payload.get("text") or ""))
            if remaining.get("kind") != "TRIAL_STATUS_ACKNOWLEDGEMENT":
                break
            time.sleep(0.1)
        dismissed = remaining.get("kind") != "TRIAL_STATUS_ACKNOWLEDGEMENT"
        return {
            **observation,
            "status": "ACKNOWLEDGED" if dismissed else "MODAL_ACK_FAILED",
            "clicked": action.get("clicked"),
            "fallback_used": bool(action.get("fallback_used")),
            "postcondition": "KNOWN_MODAL_ABSENT" if dismissed else "KNOWN_MODAL_PRESENT",
        }


def _uia_from_handles(
    hwnds: list[int], *, click: tuple[str, ...], trial_fallback: bool = False
) -> str:
    hwnd_list = ",".join(str(int(item)) for item in hwnds)
    want = ", ".join("'" + item.replace("'", "''") + "'" for item in click)
    click_block = ""
    if click:
        click_block = (
            f"$want = @({want})\n"
        "        if ($btn -and ($want -contains $btn.Current.Name)) {\n"
            "          try {\n"
            "            $inv = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)\n"
            "            $inv.Invoke()\n"
            "            $clicked = $btn.Current.Name\n"
            "          } catch {}\n"
        "        }\n"
        )
        if trial_fallback:
            click_block += (
                "        if ($clicked -and (($texts -join '`n') -match '(?i)(time\\s+remaining|tiempo\\s+restante)')) {\n"
                "          Add-Type @'\n"
                "using System;\n"
                "using System.Runtime.InteropServices;\n"
                "public static class CopilotModalNative {\n"
                "  [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd);\n"
                "}\n"
                "'@\n"
                "          [CopilotModalNative]::SetForegroundWindow([IntPtr]$h) | Out-Null\n"
                "          Add-Type -AssemblyName System.Windows.Forms\n"
                "          [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')\n"
                "          $fallback_used = $true\n"
                "        }\n"
            )
    script = f"""
Add-Type -AssemblyName UIAutomationClient
$hwnds = @({hwnd_list})
$btnCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Button)
$names = @('No','S\u00ed','Si','Yes','Aceptar','OK','Ok')
$found = @()
$textCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Text)
$texts = @()
$clicked = ''
$fallback_used = $false
foreach ($h in $hwnds) {{
  try {{ $el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$h) }} catch {{ continue }}
  if (-not $el) {{ continue }}
  $textEls = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $textCond)
  foreach ($textEl in $textEls) {{
    if ($textEl.Current.Name) {{ $texts += [string]$textEl.Current.Name }}
  }}
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
$uniqTexts = @($texts | Select-Object -Unique)
$result = @{{ buttons = $uniq; clicked = $clicked; fallback_used = $fallback_used; text = ($uniqTexts -join "`n") }}
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
