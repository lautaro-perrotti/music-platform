from __future__ import annotations

import re
from pathlib import Path

from copilot.platform.ableton import (
    MacOSAbletonDriver,
    WindowsAbletonDriver,
    driver_for_system,
)
from copilot.platform.modals import KnownModalHandler, classify_modal_text
from copilot.importing.crash_recovery_v1 import classify_live_dialog


SRC_ROOT = Path(__file__).parents[1] / "src" / "copilot"
PLATFORM_ROOT = SRC_ROOT / "platform"


def test_platform_driver_selection_and_commands() -> None:
    windows = driver_for_system("Windows")
    mac = driver_for_system("Darwin")
    assert isinstance(windows, WindowsAbletonDriver)
    assert isinstance(mac, MacOSAbletonDriver)
    assert windows.terminate_command(r"C:\Program Files\Ableton Live\Live.exe") == [
        "taskkill",
        "/IM",
        "Live.exe",
        "/F",
    ]
    working = Path("working.als")
    assert mac.launch_command("/Applications/Ableton Live 12.app", working) == [
        "open",
        "-a",
        "/Applications/Ableton Live 12.app",
        "--args",
        str(working),
    ]


def test_modal_classifier_reports_trial_and_unknown_fail_closed() -> None:
    trial = classify_modal_text(
        "Guardar y exportar se activaron con éxito. Tiempo restante: 23 días."
    )
    assert trial["kind"] == "TRIAL_STATUS_ACKNOWLEDGEMENT"
    assert trial["license_mode"] == "TRIAL"
    assert trial["trial_days_remaining"] == 23
    unknown = classify_modal_text("Unexpected unknown window")
    assert unknown == {"kind": "UNKNOWN_MODAL", "safe_action": "fail_closed"}
    assert classify_live_dialog("Guardar y exportar se activaron con éxito. Tiempo restante: 23 días.") == "TRIAL_STATUS_ACKNOWLEDGEMENT"


def test_unknown_modal_never_invokes_a_default_button() -> None:
    class FakeDriver:
        def window_handles(self):
            return [1]

        def find_buttons(self, hwnds):
            return {"text": "Unexpected unknown window", "buttons": ["Enter"]}

        def invoke_button(self, hwnds, expected):
            raise AssertionError("unknown modal must not be clicked")

    observation = KnownModalHandler(FakeDriver()).inspect()
    assert observation["kind"] == "UNKNOWN_MODAL"
    assert KnownModalHandler(FakeDriver()).acknowledge_trial(observation)["status"] == "NOT_APPLICABLE"


class _TrialDriver:
    def __init__(self, *, dismisses: bool, fallback: bool = False) -> None:
        self.dismisses = dismisses
        self.fallback = fallback
        self.visible = True
        self.invocations: list[set[str]] = []

    def window_handles(self):
        return [7] if self.visible else []

    def find_buttons(self, hwnds):
        return {
            "text": (
                "Guardar y exportar se activaron con éxito. "
                "Tiempo restante: 23 días."
                if self.visible
                else ""
            ),
            "buttons": ["OK", "Comprar ahora"] if self.visible else [],
        }

    def invoke_button_verified(self, hwnds, expected, *, trial_fallback=False):
        self.invocations.append(set(expected))
        if self.dismisses:
            self.visible = False
        return {"clicked": "OK", "fallback_used": self.fallback and trial_fallback}


def test_trial_modal_requires_verified_postcondition() -> None:
    driver = _TrialDriver(dismisses=True, fallback=True)
    result = KnownModalHandler(driver, verify_timeout_s=0.01).acknowledge_trial(
        {
            "kind": "TRIAL_STATUS_ACKNOWLEDGEMENT",
            "hwnds": [7],
        }
    )
    assert result["status"] == "ACKNOWLEDGED"
    assert result["postcondition"] == "KNOWN_MODAL_ABSENT"
    assert result["fallback_used"] is True
    assert {"OK", "Ok", "Aceptar"} in driver.invocations


def test_trial_modal_failure_is_terminal_without_restart_signal() -> None:
    driver = _TrialDriver(dismisses=False)
    result = KnownModalHandler(driver, verify_timeout_s=0.01).acknowledge_trial(
        {
            "kind": "TRIAL_STATUS_ACKNOWLEDGEMENT",
            "hwnds": [7],
        }
    )
    assert result["status"] == "MODAL_ACK_FAILED"
    assert result["postcondition"] == "KNOWN_MODAL_PRESENT"
    assert result["clicked"] == "OK"


def test_source_has_no_os_specific_implementation_outside_platform_package() -> None:
    forbidden = (
        re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]|/(?:Users|home|Applications)/)"),
        re.compile(r"(?i)\b(?:powershell|taskkill|osascript|winreg)\b"),
        re.compile(r"(?m)^\s*(?:import platform|from platform)\b"),
        re.compile(r"\bos\.name\b|\bsys\.platform\b"),
        re.compile(r"(?i)(?:/Volumes/|C:\\Users\\|D:\\MusicCopilot)")
    )
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if PLATFORM_ROOT in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            if pattern.search(text):
                violations.append(f"{path.relative_to(SRC_ROOT)}: {pattern.pattern}")
    assert violations == []
