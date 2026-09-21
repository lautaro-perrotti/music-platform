"""Platform drivers for Ableton lifecycle operations.

The driver is deliberately small. Discovery remains compatible with the
existing detection contract; process launch/termination and platform control
tools live here so the rest of the runtime stays OS-agnostic.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copilot.daw.ableton_tcp import DEFAULT_HOST, DEFAULT_PORT
from copilot.platform.detection import AbletonDetection, detect_ableton


@dataclass(frozen=True)
class AbletonEnvironment:
    system: str
    architecture: str
    executable: str | None
    preferences_root: str | None
    remote_scripts_root: str | None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "architecture": self.architecture,
            "executable": self.executable,
            "preferences_root": self.preferences_root,
            "remote_scripts_root": self.remote_scripts_root,
            "host": self.host,
            "port": self.port,
        }


class PlatformAbletonDriver:
    """Common Ableton lifecycle surface implemented by each host driver."""

    system = ""

    def discover(self, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> AbletonDetection:
        return detect_ableton(port, include_start_menu=False)

    def environment(self, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> AbletonEnvironment:
        detection = self.discover(host=host, port=port)
        return AbletonEnvironment(
            system=self.system or platform.system(),
            architecture=platform.machine(),
            executable=detection.exe_path,
            preferences_root=detection.prefs_root,
            remote_scripts_root=detection.user_remote_scripts,
            host=host,
            port=port,
        )

    def available_process_controls(self) -> tuple[str, ...]:
        return tuple(
            command
            for command in self.process_controls
            if shutil.which(command)
        )

    @property
    def process_controls(self) -> tuple[str, ...]:
        return ()

    def launch(self, executable: str, working_als: Path) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            self.launch_command(executable, working_als),
            cwd=str(working_als.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def launch_command(self, executable: str, working_als: Path) -> list[str]:
        return [executable, str(working_als)]

    def terminate(self, executable: str) -> None:
        command = self.terminate_command(executable)
        if command:
            subprocess.run(command, capture_output=True, text=True, check=False)

    def terminate_command(self, executable: str) -> list[str]:
        return []


class WindowsAbletonDriver(PlatformAbletonDriver):
    system = "Windows"

    @property
    def process_controls(self) -> tuple[str, ...]:
        return ("taskkill",)

    def terminate_command(self, executable: str) -> list[str]:
        return ["taskkill", "/IM", Path(executable).name, "/F"]


class MacOSAbletonDriver(PlatformAbletonDriver):
    system = "Darwin"

    @property
    def process_controls(self) -> tuple[str, ...]:
        return ("pkill", "killall", "open")

    def launch_command(self, executable: str, working_als: Path) -> list[str]:
        if executable.endswith(".app"):
            return ["open", "-a", executable, "--args", str(working_als)]
        return super().launch_command(executable, working_als)

    def terminate_command(self, executable: str) -> list[str]:
        return ["pkill", "-f", executable]


class LinuxAbletonDriver(PlatformAbletonDriver):
    system = "Linux"

    @property
    def process_controls(self) -> tuple[str, ...]:
        return ("pkill", "killall")

    def terminate_command(self, executable: str) -> list[str]:
        return ["pkill", "-f", executable]


def driver_for_system(system: str | None = None) -> PlatformAbletonDriver:
    name = system or platform.system()
    if name == "Windows":
        return WindowsAbletonDriver()
    if name == "Darwin":
        return MacOSAbletonDriver()
    return LinuxAbletonDriver()
