"""SECOND_MACHINE_INSTALLER_V1 — Windows local runtime. No secrets. No musical writes."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from copilot.daw.detect import detect_ableton
from copilot.daw.install_remote_script import (
    SCRIPT_FOLDER,
    SCRIPT_HOST,
    SCRIPT_PORT,
    VENDOR_SCRIPT,
    install_remote_script,
    uninstall_remote_script,
)
from copilot.human_eval.store import now_iso
from copilot.importing.m4l_runtime_v1 import (
    DEVICE_NAME,
    EXPECTED_TAP_PROTOCOL,
    canonical_tap_asset,
    discover_user_library,
    ensure_m4l_runtime,
    runtime_paths,
)
from copilot.reasoning.provider import configured_http_provider

MILESTONE = "SECOND_MACHINE_INSTALLER_V1"
ARTIFACT = "second_machine_installer_v1.json"
STATUS = "IMPLEMENTED"
SUPPORTED_PYTHON = (3, 12)
CONFIG_EXAMPLE_REL = Path("config") / "copilot.env.example"
LOCAL_ENV_REL = Path(".env")
CREDENTIAL_KEYS = ("COPILOT_REASONING_API_KEY", "OPENAI_API_KEY")
MODEL_KEYS = (
    "COPILOT_REASONING_BASE_URL",
    "COPILOT_REASONING_MODEL",
    "COPILOT_REASONING_EFFORT",
)
CONTROL_SURFACE_STEPS = [
    "Start Ableton Live.",
    "If Live was already open during install, quit and reopen it so the Remote Script is scanned.",
    "Settings → Link, Tempo & MIDI → Control Surface = AbletonMCP.",
    "Input = None, Output = None.",
    r".venv\Scripts\python.exe -m copilot.cli doctor",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _detect() -> Any:
    return detect_ableton(include_start_menu=False)


def discover_environment(*, repo: Path | None = None) -> dict[str, Any]:
    root = Path(repo or repo_root())
    win = sys.getwindowsversion() if os.name == "nt" else None
    detection = _detect()
    library = discover_user_library(detection.prefs_root)
    documents = Path.home() / "Documents"
    prefs_versions = _prefs_versions()
    writable = {
        "repository": os.access(root, os.W_OK),
        "documents": os.access(documents, os.W_OK) if documents.exists() else False,
    }
    library_path = library.get("user_library")
    if library_path:
        writable["user_library"] = os.access(library_path, os.W_OK)
    scripts_parent = None
    if library.get("status") == "VERIFIED" and library_path:
        scripts_parent = str(Path(str(library_path)) / "Remote Scripts")
    elif detection.user_remote_scripts:
        scripts_parent = detection.user_remote_scripts
    if scripts_parent:
        parent = Path(scripts_parent)
        writable["remote_scripts_parent"] = os.access(
            parent if parent.exists() else parent.parent, os.W_OK
        )
    return {
        "windows_version": {
            "platform": platform.platform(),
            "release": platform.release(),
            "version": platform.version(),
            "major": None if win is None else win.major,
            "minor": None if win is None else win.minor,
            "build": None if win is None else win.build,
        },
        "architecture": platform.machine(),
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "version_info": list(sys.version_info[:3]),
            "compatible": sys.version_info[:2] >= SUPPORTED_PYTHON,
            "supported_target": "3.12",
        },
        "ableton": detection.to_dict(),
        "ableton_exe_path": detection.exe_path,
        "ableton_prefs_root": detection.prefs_root,
        "ableton_prefs_versions": prefs_versions,
        "documents_path": str(documents),
        "user_library": library,
        "repository_path": str(root.resolve()),
        "permissions": writable,
        "remote_scripts_parent": scripts_parent,
    }


def inspect_control_surface(*, detection: Any | None = None) -> dict[str, Any]:
    """TCP exists only after AbletonMCP ControlSurface is instantiated."""
    found = detection if detection is not None else _detect()
    mentioned = _prefs_mention_abletonmcp(found.prefs_root)
    if found.port_open:
        status = "LOADED"
        restart = False
        required = False
    elif mentioned:
        status = "ABLETON_RESTART_REQUIRED"
        restart = True
        required = False
    else:
        status = "ABLETON_CONTROL_SURFACE_CONFIGURATION_REQUIRED"
        restart = bool(found.process_running)
        required = True
    return {
        "loads_without_control_surface": False,
        "reason": (
            "vendor/abletonmcp_remote_script/AbletonMCP/__init__.py subclasses "
            "ControlSurface and starts the TCP listener in create_instance/__init__. "
            "Copying the script into User Library/Remote Scripts only makes AbletonMCP "
            "appear in the Control Surface dropdown. V1 does not click Live preferences."
        ),
        "status": status,
        "ABLETON_CONTROL_SURFACE_CONFIGURATION_REQUIRED": required,
        "ABLETON_RESTART_REQUIRED": restart,
        "prefs_mention_abletonmcp": mentioned,
        "port_open": found.port_open,
        "process_running": found.process_running,
        "steps": list(CONTROL_SURFACE_STEPS),
    }


def ensure_config_template(*, repo: Path | None = None) -> dict[str, Any]:
    root = Path(repo or repo_root())
    example = root / CONFIG_EXAMPLE_REL
    local = root / LOCAL_ENV_REL
    if not example.is_file():
        return {
            "status": "BLOCKED",
            "config": "BLOCKED",
            "reason": "CONFIG_EXAMPLE_MISSING",
            "example": str(example),
        }
    example_text = example.read_text(encoding="utf-8")
    if local.is_file():
        return {
            "status": "PRESERVED",
            "config": "PRESERVED",
            "example": str(example),
            "local": str(local),
            "created": False,
        }
    local.write_text(example_text, encoding="utf-8")
    return {
        "status": "CREATED",
        "config": "CREATED",
        "example": str(example),
        "local": str(local),
        "created": True,
        "copied_from_developer_machine": False,
    }


def credential_status(*, repo: Path | None = None) -> dict[str, Any]:
    root = Path(repo or repo_root())
    present = [name for name in CREDENTIAL_KEYS if _key_configured(name, root)]
    provider = configured_http_provider()
    required = not present and provider is None
    return {
        "MODEL_CREDENTIALS_REQUIRED": required,
        "expected_keys": list(CREDENTIAL_KEYS),
        "expected_model_fields": list(MODEL_KEYS),
        "keys_present": present,
        "provider_configured": provider is not None,
        "values_redacted": True,
        "blocks_install": False,
        "exact_setup": (
            "Set user environment variable COPILOT_REASONING_API_KEY "
            "(or OPENAI_API_KEY), optional COPILOT_REASONING_MODEL=gpt-6-astra, "
            "COPILOT_REASONING_BASE_URL=https://api.openai.com/v1. "
            "Do not paste keys into the installer report."
        ),
    }


def verify_imports() -> dict[str, Any]:
    names = ("copilot", "pydantic", "numpy", "soundfile", "scipy", "pyloudnorm")
    missing: list[str] = []
    loaded: list[str] = []
    for name in names:
        try:
            __import__(name)
        except Exception:
            missing.append(name)
        else:
            loaded.append(name)
    return {
        "ok": not missing,
        "loaded": loaded,
        "missing": missing,
        "executable": sys.executable,
    }


def static_doctor(*, repo: Path | None = None) -> dict[str, Any]:
    root = Path(repo or repo_root())
    env = discover_environment(repo=root)
    imports = verify_imports()
    library = env.get("user_library") or {}
    asset = canonical_tap_asset()
    remote = _remote_script_on_disk(env)
    m4l = _m4l_on_disk(library, asset)
    config = root / CONFIG_EXAMPLE_REL
    checks = {
        "python_compatible": bool((env.get("python") or {}).get("compatible")),
        "imports": imports["ok"],
        "repository_writable": bool((env.get("permissions") or {}).get("repository")),
        "ableton_found": bool((env.get("ableton") or {}).get("found")),
        "user_library_resolved": library.get("status") == "VERIFIED",
        "remote_script_source": VENDOR_SCRIPT.is_file(),
        "remote_script_installed": remote.get("ok"),
        "m4l_source": asset.get("status") == "VERIFIED",
        "m4l_installed": m4l.get("ok"),
        "tap_protocol_expected": EXPECTED_TAP_PROTOCOL,
        "config_example": config.is_file(),
        "live_not_required": True,
    }
    failures = [name for name, ok in checks.items() if name != "live_not_required" and not ok]
    return {
        "milestone": "INSTALL_STATIC_DOCTOR_V1",
        "status": "READY" if not failures else "BLOCKED",
        "failures": failures,
        "checks": checks,
        "remote_script": remote,
        "m4l": m4l,
        "LIVE_SESSION_READINESS_V1": "VERIFIED / FROZEN — not probed here",
        "NO WRITE": True,
    }


def run_installer(
    *,
    repo: Path | None = None,
    evidence: Path | None = None,
    python_env: dict[str, Any] | None = None,
    remote_dest_parent: Path | None = None,
    user_library: Path | None = None,
) -> dict[str, Any]:
    root = Path(repo or repo_root())
    evidence_dir = Path(evidence or (root / "logs"))
    env = discover_environment(repo=root)
    imports = verify_imports()
    config = ensure_config_template(repo=root)
    creds = credential_status(repo=root)
    remote = install_remote_script(dest_parent=remote_dest_parent)
    library = env.get("user_library") or {}
    m4l: dict[str, Any]
    if user_library is not None:
        m4l = ensure_m4l_runtime(user_library=user_library, evidence=evidence_dir)
    elif library.get("status") == "VERIFIED" and library.get("user_library"):
        m4l = ensure_m4l_runtime(
            user_library=library["user_library"],
            evidence=evidence_dir,
        )
    else:
        m4l = ensure_m4l_runtime(evidence=evidence_dir)
    control = inspect_control_surface()
    static = static_doctor(repo=root)
    python_report = python_env or _python_env_from_process(env)
    deps = {
        "status": python_report.get("deps_status")
        or ("ALREADY_CURRENT" if imports["ok"] else "MISSING"),
        **imports,
    }
    remote_status = str(remote.get("REMOTE_SCRIPT") or remote.get("status") or "BLOCKED")
    m4l_status = str(m4l.get("status") or "BLOCKED")
    blockers: list[str] = []
    if remote_status in {"BLOCKED", "BLOCKED_BY_ENVIRONMENT", "FAILED"}:
        blockers.append(str(remote.get("error") or remote_status))
    if m4l.get("M4L_RUNTIME_PROVISIONING_V1") == "BLOCKED" or m4l_status == "BLOCKED":
        blockers.append(str(m4l.get("reason") or m4l_status))
    if not env["python"]["compatible"]:
        blockers.append("PYTHON_INSTALL_REQUIRED")
    if not imports["ok"]:
        blockers.append("DEPENDENCIES_MISSING")
    status = "VERIFIED" if not blockers else "BLOCKED"
    report = {
        "milestone": MILESTONE,
        "ts": now_iso(),
        "status": status,
        MILESTONE: status,
        "WINDOWS DISCOVERY": "VERIFIED",
        "PYTHON": python_report.get("status"),
        "VENV": python_report.get("venv_status") or python_report.get("status"),
        "DEPENDENCIES": deps.get("status"),
        "ABLETON DETECTION": "VERIFIED" if env["ableton"].get("found") else "BLOCKED",
        "REMOTE_SCRIPT": remote_status,
        "USER LIBRARY": library.get("status"),
        "M4L RUNTIME": m4l_status if m4l.get("M4L_RUNTIME_PROVISIONING_V1") != "BLOCKED" else "BLOCKED",
        "CONFIG": config.get("config"),
        "SECRETS NOT COPIED": True,
        "MODEL_CREDENTIALS_REQUIRED": creds["MODEL_CREDENTIALS_REQUIRED"],
        "ABLETON_CONTROL_SURFACE_CONFIGURATION_REQUIRED": control[
            "ABLETON_CONTROL_SURFACE_CONFIGURATION_REQUIRED"
        ],
        "discovery": env,
        "python_env": python_report,
        "dependencies": deps,
        "config": _redact(config),
        "credentials": creds,
        "remote_script": _without_detection_noise(remote),
        "m4l": {
            "status": m4l.get("status"),
            "M4L_RUNTIME_PROVISIONING_V1": m4l.get("M4L_RUNTIME_PROVISIONING_V1"),
            "reason": m4l.get("reason"),
            "installed": m4l.get("installed"),
            "sha256": m4l.get("sha256"),
            "library": m4l.get("library"),
            "device_name": DEVICE_NAME,
            "tap_protocol_expected": EXPECTED_TAP_PROTOCOL,
        },
        "control_surface": control,
        "static_doctor": static,
        "blockers": blockers,
        "blocker": blockers[0] if blockers else None,
        "NO MUSICAL WRITE": True,
        "NO STATE TRUST CHANGE": True,
        "NO CAPTURE CHANGE": True,
        "NO REASONING CHANGE": True,
        "next": CONTROL_SURFACE_STEPS if control["ABLETON_CONTROL_SURFACE_CONFIGURATION_REQUIRED"] or control["ABLETON_RESTART_REQUIRED"] else [
            r".venv\Scripts\python.exe -m copilot.cli doctor",
        ],
        "SECOND_MACHINE_PORTABILITY_V1": "PENDING_FRIEND_MACHINE",
    }
    _persist(report, evidence_dir)
    return report


def uninstall_copilot_owned(
    *,
    repo: Path | None = None,
    remove_venv: bool = False,
    remove_config: bool = False,
    remote_dest_parent: Path | None = None,
    user_library: Path | None = None,
) -> dict[str, Any]:
    root = Path(repo or repo_root())
    remote = uninstall_remote_script(dest_parent=remote_dest_parent)
    library = (
        {
            "status": "VERIFIED",
            "user_library": str(user_library),
            "rule": "explicit",
        }
        if user_library is not None
        else discover_user_library()
    )
    m4l_removed: list[str] = []
    m4l_skipped: list[str] = []
    if library.get("status") == "VERIFIED" and library.get("user_library"):
        paths = runtime_paths(library["user_library"])
        folder = paths["folder"]
        manifest = paths["manifest"]
        owned = False
        if manifest.is_file():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                owned = bool(payload.get("COPILOT_OWNED")) and payload.get("device_name") == DEVICE_NAME
            except (OSError, json.JSONDecodeError):
                owned = False
        if owned:
            for path in (paths["device"], manifest):
                if path.is_file():
                    path.unlink()
                    m4l_removed.append(str(path))
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
                m4l_removed.append(str(folder))
        elif folder.exists():
            m4l_skipped.append(str(folder))
    venv = root / ".venv"
    venv_status = "PRESERVED"
    if remove_venv and venv.exists():
        import shutil

        shutil.rmtree(venv)
        venv_status = "REMOVED"
    config_status = "PRESERVED"
    local = root / LOCAL_ENV_REL
    if remove_config and local.is_file():
        local.unlink()
        config_status = "REMOVED"
    return {
        "milestone": MILESTONE,
        "status": "VERIFIED",
        "remote_script": remote,
        "m4l_removed": m4l_removed,
        "m4l_skipped": m4l_skipped,
        "venv": venv_status,
        "config": config_status,
        "removed_user_projects": False,
        "removed_ableton_preferences": False,
        "removed_unowned_user_library": False,
    }


def _python_env_from_process(env: dict[str, Any]) -> dict[str, Any]:
    compatible = bool((env.get("python") or {}).get("compatible"))
    return {
        "status": os.environ.get("COPILOT_INSTALLER_PYTHON_STATUS")
        or ("ALREADY_CURRENT" if compatible else "PYTHON_INSTALL_REQUIRED"),
        "venv_status": os.environ.get("COPILOT_INSTALLER_VENV_STATUS") or "UNKNOWN",
        "deps_status": os.environ.get("COPILOT_INSTALLER_DEPS_STATUS") or None,
        "version": env["python"]["version"],
        "executable": env["python"]["executable"],
        "winget_used": os.environ.get("COPILOT_INSTALLER_WINGET_USED") == "1",
    }


def _prefs_versions() -> list[str]:
    roaming = Path.home() / "AppData" / "Roaming" / "Ableton"
    if not roaming.is_dir():
        return []
    names = []
    for child in roaming.iterdir():
        if child.is_dir() and child.name.startswith("Live ") and child.name != "Live Reports":
            names.append(child.name)
    return sorted(names)


def _prefs_mention_abletonmcp(prefs_root: str | None) -> bool:
    if not prefs_root:
        return False
    root = Path(prefs_root)
    if not root.is_dir():
        return False
    names = (
        "Preferences.cfg",
        "Library.cfg",
        "UserConfiguration.txt",
        "MIDI.cfg",
    )
    candidates: list[Path] = []
    for name in names:
        candidates.append(root / name)
        candidates.append(root / "Preferences" / name)
    needle = b"AbletonMCP"
    for path in candidates:
        if not path.is_file() or path.stat().st_size > 8_000_000:
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if needle in blob:
            return True
    return False


def _key_configured(name: str, repo: Path) -> bool:
    if os.environ.get(name):
        return True
    local = repo / LOCAL_ENV_REL
    if local.is_file():
        for line in local.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name and value.strip():
                return True
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as hive:
            value, _ = winreg.QueryValueEx(hive, name)
        return bool(str(value).strip())
    except OSError:
        return False


def _remote_script_on_disk(env: dict[str, Any]) -> dict[str, Any]:
    parent = env.get("remote_scripts_parent")
    if not parent:
        return {"ok": False, "reason": "destination_unresolved"}
    dest = Path(str(parent)) / SCRIPT_FOLDER / "__init__.py"
    if not dest.is_file():
        return {"ok": False, "reason": "missing", "path": str(dest)}
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    expected = hashlib.sha256(VENDOR_SCRIPT.read_bytes()).hexdigest() if VENDOR_SCRIPT.is_file() else ""
    return {
        "ok": digest == expected,
        "path": str(dest),
        "sha256": digest,
        "checksum_match": digest == expected,
    }


def _m4l_on_disk(library: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    if library.get("status") != "VERIFIED" or not library.get("user_library"):
        return {"ok": False, "reason": "USER_LIBRARY_UNRESOLVED"}
    paths = runtime_paths(library["user_library"])
    dest = paths["device"]
    if not dest.is_file():
        return {"ok": False, "reason": "missing", "path": str(dest)}
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    expected = str(asset.get("sha256") or "")
    return {
        "ok": bool(expected) and digest == expected,
        "path": str(dest),
        "sha256": digest,
        "checksum_match": digest == expected,
    }


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(payload, default=str)
    marker = "sk" + "-"
    if marker in blob or "sk_" in blob:
        raise ValueError("installer report leaked a secret marker")
    return payload


def _without_detection_noise(remote: dict[str, Any]) -> dict[str, Any]:
    copy = dict(remote)
    detection = copy.get("detection")
    if isinstance(detection, dict):
        copy["detection"] = {
            "found": detection.get("found"),
            "version": detection.get("version"),
            "exe_path": detection.get("exe_path"),
            "prefs_root": detection.get("prefs_root"),
            "user_remote_scripts": detection.get("user_remote_scripts"),
            "process_running": detection.get("process_running"),
            "port_open": detection.get("port_open"),
            "connection_status": detection.get("connection_status"),
        }
    return copy


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report["artifact"] = str(path)
    return path


def main() -> int:
    report = run_installer()
    summary = {
        "milestone": MILESTONE,
        "status": report.get("status"),
        "PYTHON": report.get("PYTHON"),
        "VENV": report.get("VENV"),
        "DEPENDENCIES": report.get("DEPENDENCIES"),
        "REMOTE_SCRIPT": report.get("REMOTE_SCRIPT"),
        "M4L RUNTIME": report.get("M4L RUNTIME"),
        "CONFIG": report.get("CONFIG"),
        "MODEL_CREDENTIALS_REQUIRED": report.get("MODEL_CREDENTIALS_REQUIRED"),
        "ABLETON_CONTROL_SURFACE_CONFIGURATION_REQUIRED": report.get(
            "ABLETON_CONTROL_SURFACE_CONFIGURATION_REQUIRED"
        ),
        "blocker": report.get("blocker"),
        "SECOND_MACHINE_PORTABILITY_V1": report.get("SECOND_MACHINE_PORTABILITY_V1"),
        "next": report.get("next"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("status") == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
