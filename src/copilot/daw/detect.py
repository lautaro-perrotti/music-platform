from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


@dataclass
class AbletonDetection:
    found: bool
    version: str | None
    exe_path: str | None
    prefs_root: str | None
    user_remote_scripts: str | None
    process_running: bool
    port_open: bool
    evidence: list[str]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["connection_status"] = live_block_status(self)
        return payload


def live_block_status(detection: AbletonDetection) -> str:
    if detection.port_open:
        return "CONNECTED"
    if detection.found:
        return "MANUAL_CONFIGURATION_REQUIRED"
    return "BLOCKED_BY_ENVIRONMENT"


def detect_ableton(port: int = 9877, *, include_start_menu: bool = True) -> AbletonDetection:
    evidence: list[str] = []
    exe_path: str | None = None
    version: str | None = None
    prefs_root: str | None = None

    for display, loc, ver in _registry_uninstall():
        evidence.append(f"registry uninstall: {display} loc={loc} ver={ver}")
        if loc and not exe_path:
            candidate = _first_live_exe(Path(loc))
            if candidate:
                exe_path = str(candidate)
                version = ver or version

    if winreg is not None:
        for key_path in (
            r"SOFTWARE\Ableton",
            r"SOFTWARE\WOW6432Node\Ableton",
        ):
            hits = _registry_key_values(winreg.HKEY_LOCAL_MACHINE, key_path)
            hits += _registry_key_values(winreg.HKEY_CURRENT_USER, key_path)
            for item in hits:
                evidence.append(f"registry {item}")

    for root in _candidate_install_roots():
        if root.exists():
            evidence.append(f"exists {root}")
            exe = _first_live_exe(root)
            if exe and not exe_path:
                exe_path = str(exe)
        else:
            evidence.append(f"missing {root}")

    if include_start_menu and os.name == "nt":
        for link in _start_menu_ableton_links():
            evidence.append(f"start menu {link}")

    prefs = _latest_prefs_root()
    if prefs is not None:
        prefs_root = str(prefs)
        evidence.append(f"prefs {prefs}")
        folder_version = _version_from_prefs_name(prefs.name)
        if folder_version:
            version = folder_version

    scripts = None
    library_scripts = _user_library_remote_scripts()
    if library_scripts is not None:
        scripts = str(library_scripts)
        evidence.append(
            f"user library remote scripts "
            f"{'exists' if library_scripts.exists() else 'absent'}: {library_scripts}"
        )
    elif prefs is not None:
        scripts_dir = _user_remote_scripts_dir(prefs)
        scripts = str(scripts_dir)
        evidence.append(
            f"prefs user remote scripts "
            f"{'exists' if scripts_dir.exists() else 'absent'}: {scripts_dir}"
        )

    process_running = _live_process_running()
    if process_running:
        evidence.append("Ableton process running")
    port_open = _port_open("127.0.0.1", port)
    evidence.append(f"port {port} {'open' if port_open else 'closed'}")

    found = exe_path is not None or prefs_root is not None or process_running
    return AbletonDetection(
        found=found,
        version=version,
        exe_path=exe_path,
        prefs_root=prefs_root,
        user_remote_scripts=scripts,
        process_running=process_running,
        port_open=port_open,
        evidence=evidence,
    )


def default_user_library_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / "Documents" / "Ableton" / "User Library",
        home / "Music" / "Ableton" / "User Library",
    ]


def prefs_search_roots() -> list[Path]:
    home = Path.home()
    return [
        home / "AppData" / "Roaming" / "Ableton",
        home / "Library" / "Preferences" / "Ableton",
    ]


def _candidate_install_roots() -> list[Path]:
    home = Path.home()
    if os.name == "nt":
        return [
            Path(r"C:\Program Files\Ableton"),
            Path(r"C:\Program Files (x86)\Ableton"),
            Path(r"C:\ProgramData\Ableton"),
            home / "AppData" / "Local" / "Programs" / "Ableton",
            Path(r"D:\Ableton"),
            Path(r"D:\Program Files\Ableton"),
        ]
    return [
        Path("/Applications"),
        home / "Applications",
    ]


def _first_live_bundle(root: Path) -> Path | None:
    if not root.exists():
        return None
    for app in sorted(root.glob("Ableton Live *.app")):
        exe = app / "Contents" / "MacOS" / "Live"
        if exe.is_file():
            return exe
    return None


def _first_live_exe(root: Path) -> Path | None:
    bundle = _first_live_bundle(root)
    if bundle is not None:
        return bundle
    if not root.exists():
        return None
    matches = list(root.glob("**/Ableton Live *.exe"))
    return matches[0] if matches else None


def _latest_prefs_root() -> Path | None:
    candidates: list[Path] = []
    for roaming in prefs_search_roots():
        if not roaming.exists():
            continue
        candidates.extend(
            item
            for item in roaming.iterdir()
            if item.is_dir() and _is_live_prefs_folder(item)
        )
    if not candidates:
        return None
    return max(candidates, key=_prefs_version_key)


def _is_live_prefs_folder(path: Path) -> bool:
    name = path.name
    if not name.startswith("Live "):
        return False
    if name == "Live Reports":
        return False
    return (
        (path / "Preferences.cfg").exists()
        or (path / "Preferences" / "Preferences.cfg").exists()
        or (path / "User Remote Scripts").exists()
        or (path / "Preferences" / "User Remote Scripts").exists()
        or (path / "Library.cfg").exists()
        or (path / "Preferences" / "Library.cfg").exists()
    )


def _version_from_prefs_name(name: str) -> str | None:
    if not name.startswith("Live "):
        return None
    token = name[5:].split()[0]
    if token[0].isdigit():
        return token
    return None


def _prefs_version_key(path: Path) -> tuple[int, ...]:
    version = _version_from_prefs_name(path.name) or "0"
    parts: list[int] = []
    for bit in version.split("."):
        try:
            parts.append(int(bit))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _user_library_remote_scripts() -> Path | None:
    """Live 10.1.13+ loads third-party Python Control Surfaces from User Library."""
    for library in default_user_library_candidates():
        if library.exists():
            return library / "Remote Scripts"
    return None


def _user_remote_scripts_dir(prefs: Path) -> Path:
    nested = prefs / "Preferences" / "User Remote Scripts"
    flat = prefs / "User Remote Scripts"
    if nested.exists():
        return nested
    if flat.exists():
        return flat
    if (prefs / "Preferences").exists():
        return nested
    return flat


def _start_menu_ableton_links() -> list[str]:
    if os.name != "nt":
        return []
    roots = [
        Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs",
        Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]
    links: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for link in root.rglob("*.lnk"):
            name = link.name.lower()
            if "ableton" in name or name.startswith("live "):
                links.append(str(link))
    return links


def _registry_uninstall() -> list[tuple[str, str | None, str | None]]:
    if winreg is None:
        return []
    found: list[tuple[str, str | None, str | None]] = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    sub = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, sub) as item:
                        name = _reg_str(item, "DisplayName")
                        if not name or "ableton" not in name.lower():
                            continue
                        found.append(
                            (
                                name,
                                _reg_str(item, "InstallLocation"),
                                _reg_str(item, "DisplayVersion"),
                            )
                        )
        except OSError:
            continue
    return found


def _registry_key_values(hive: int, path: str) -> list[str]:
    if winreg is None:
        return []
    hits: list[str] = []
    try:
        with winreg.OpenKey(hive, path) as key:
            hits.append(path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                hits.extend(_registry_key_values(hive, path + "\\" + winreg.EnumKey(key, i)))
    except OSError:
        return hits
    return hits


def _reg_str(key, name: str) -> str | None:
    if winreg is None:
        return None
    try:
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value) if value else None


def _live_process_running() -> bool:
    try:
        if os.name == "nt":
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq Ableton Live*"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return "Ableton Live" in out
        out = subprocess.check_output(
            ["pgrep", "-fl", "Ableton Live"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return "Ableton Live" in out


def _port_open(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def write_detection(path: Path, detection: AbletonDetection) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(detection.to_dict(), indent=2), encoding="utf-8")
