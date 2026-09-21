"""Host-system services used by the portable runtime."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def host_system() -> str:
    return platform.system() or "Unknown"


def host_architecture() -> str:
    return platform.machine()


def host_shell(system: str | None = None) -> str:
    name = system or host_system()
    return os.environ.get("COMSPEC" if name == "Windows" else "SHELL", "")


def host_platform_info() -> dict[str, str]:
    return {
        "platform": platform.platform(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": host_architecture(),
        "system": host_system(),
    }


def host_python_version() -> str:
    return platform.python_version()


def user_environment_value(name: str) -> str | None:
    """Read a user environment value without leaking registry logic to Core."""
    direct = os.environ.get(name)
    if direct:
        return direct
    if host_system() != "Windows":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as hive:
            value, _ = winreg.QueryValueEx(hive, name)
    except OSError:
        return None
    text = str(value).strip()
    return text or None


def control_surface_steps(system: str | None = None) -> list[str]:
    name = system or host_system()
    python_path = Path(".venv") / ("Scripts" if name == "Windows" else "bin") / ("python.exe" if name == "Windows" else "python")
    command = f"{python_path} -m copilot.cli doctor"
    return [
        "Start Ableton Live.",
        "If Live was already open during install, quit and reopen it so the Remote Script is scanned.",
        "Settings -> Link, Tempo & MIDI -> Control Surface = AbletonMCP.",
        "Input = None, Output = None.",
        command,
    ]


def windows_version() -> dict[str, int | None]:
    if host_system() != "Windows":
        return {"major": None, "minor": None, "build": None}
    info = sys.getwindowsversion()
    return {"major": info.major, "minor": info.minor, "build": info.build}


def default_capture_dir() -> Path:
    return Path(os.environ.get("MUSICCOPILOT_CAPTURE_DIR", Path.home() / "CopilotProjects" / "captures"))
