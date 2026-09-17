from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from copilot.daw.detect import detect_ableton
from copilot.importing.m4l_runtime_v1 import discover_user_library

VENDOR_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "vendor"
    / "abletonmcp_remote_script"
    / "AbletonMCP"
    / "__init__.py"
)
SCRIPT_FOLDER = "AbletonMCP"
MANIFEST_NAME = "copilot_remote_script.json"
SCRIPT_PORT = 9877
SCRIPT_PROTOCOL = "tcp-json"
SCRIPT_HOST = "127.0.0.1"
OWNED_FILES = {"__init__.py", MANIFEST_NAME}


def source_digest() -> str:
    return hashlib.sha256(VENDOR_SCRIPT.read_bytes()).hexdigest()


def resolve_remote_scripts_parent(*, dest_parent: Path | None = None) -> dict[str, Any]:
    if dest_parent is not None:
        return {
            "ok": True,
            "parent": Path(dest_parent),
            "rule": "explicit",
        }
    detection = detect_ableton(SCRIPT_PORT, include_start_menu=False)
    library = discover_user_library(detection.prefs_root)
    if library.get("status") == "VERIFIED" and library.get("user_library"):
        parent = Path(str(library["user_library"])) / "Remote Scripts"
        return {
            "ok": True,
            "parent": parent,
            "rule": f"user_library:{library.get('rule')}",
            "user_library": library.get("user_library"),
            "detection": detection,
        }
    if detection.user_remote_scripts:
        return {
            "ok": True,
            "parent": Path(detection.user_remote_scripts),
            "rule": "detect_user_remote_scripts",
            "detection": detection,
        }
    return {
        "ok": False,
        "parent": None,
        "rule": "unresolved",
        "detection": detection,
        "error": "Ableton Live installation not found; refusing to invent Live prefs",
    }


def install_remote_script(*, dest_parent: Path | None = None) -> dict:
    detection = None if dest_parent is not None else detect_ableton(SCRIPT_PORT, include_start_menu=False)
    if not VENDOR_SCRIPT.exists():
        return {
            "status": "FAILED",
            "REMOTE_SCRIPT": "BLOCKED",
            "error": f"Vendored Remote Script missing: {VENDOR_SCRIPT}",
            "detection": None if detection is None else detection.to_dict(),
        }
    digest = source_digest()
    resolved = resolve_remote_scripts_parent(dest_parent=dest_parent)
    detection_payload = (
        resolved.get("detection").to_dict()
        if hasattr(resolved.get("detection"), "to_dict")
        else (None if detection is None else detection.to_dict())
    )
    if not resolved.get("ok"):
        return {
            "status": "BLOCKED_BY_ENVIRONMENT",
            "REMOTE_SCRIPT": "BLOCKED",
            "error": resolved.get("error"),
            "source_script": str(VENDOR_SCRIPT),
            "sha256": digest,
            "port": SCRIPT_PORT,
            "host": SCRIPT_HOST,
            "protocol": SCRIPT_PROTOCOL,
            "detection": detection_payload,
            "required_after_install": [
                "Install Ableton Live 11 or 12",
                "python -m copilot.cli install-script",
                "Live → Settings → Link, Tempo & MIDI → Control Surface = AbletonMCP",
                "Input/Output = None",
                "python -m copilot.cli probe",
            ],
        }
    dest_dir = Path(resolved["parent"]) / SCRIPT_FOLDER
    dest = dest_dir / "__init__.py"
    dest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dest_dir / MANIFEST_NAME
    if dest.is_file():
        current = hashlib.sha256(dest.read_bytes()).hexdigest()
        owned = _copilot_owned(manifest_path)
        if current == digest:
            status = "ALREADY_CURRENT"
        elif owned:
            _atomic_copy(VENDOR_SCRIPT, dest)
            status = "UPDATED"
        else:
            return {
                "status": "BLOCKED",
                "REMOTE_SCRIPT": "BLOCKED",
                "error": "USER_OWNED_CONFLICT",
                "detail": "Refusing to overwrite an unmanaged AbletonMCP Remote Script.",
                "install_path": str(dest),
                "source_sha256": digest,
                "installed_sha256": current,
                "port": SCRIPT_PORT,
                "host": SCRIPT_HOST,
                "protocol": SCRIPT_PROTOCOL,
                "detection": detection_payload,
            }
    else:
        _atomic_copy(VENDOR_SCRIPT, dest)
        status = "INSTALLED"
    installed_digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if installed_digest != digest:
        return {
            "status": "FAILED",
            "REMOTE_SCRIPT": "BLOCKED",
            "error": "Installed Remote Script checksum mismatch",
            "source_sha256": digest,
            "installed_sha256": installed_digest,
            "install_path": str(dest),
            "detection": detection_payload,
        }
    _write_manifest(manifest_path, digest, dest)
    retired: list[str] = []
    if dest_parent is None and detection is not None:
        retired = _retire_duplicate_copies(detection, dest, digest)
    return {
        "status": status,
        "REMOTE_SCRIPT": status,
        "install_path": str(dest),
        "ableton_version": None if detection is None else detection.version,
        "remote_script": "jpoindexter/ableton-mcp AbletonMCP_Remote_Script",
        "sha256": installed_digest,
        "source_sha256": digest,
        "file_exists": True,
        "checksum_match": True,
        "destination_rule": resolved.get("rule"),
        "COPILOT_OWNED": True,
        "retired_duplicates": retired,
        "host": SCRIPT_HOST,
        "port": SCRIPT_PORT,
        "protocol": SCRIPT_PROTOCOL,
        "required_live_setting": "Control Surface = AbletonMCP; Input/Output = None",
        "loads_without_control_surface": False,
        "control_surface_reason": (
            "AbletonMCP subclasses ControlSurface and starts TCP in create_instance/"
            "__init__. User Library placement only lists it in the Control Surface dropdown."
        ),
        "detection": detection_payload,
    }


def uninstall_remote_script(*, dest_parent: Path | None = None) -> dict[str, Any]:
    resolved = resolve_remote_scripts_parent(dest_parent=dest_parent)
    removed: list[str] = []
    skipped: list[str] = []
    if resolved.get("ok"):
        dest_dir = Path(resolved["parent"]) / SCRIPT_FOLDER
        result = _remove_owned_script_dir(dest_dir)
        removed.extend(result["removed"])
        skipped.extend(result["skipped"])
    if dest_parent is None:
        detection = detect_ableton(SCRIPT_PORT, include_start_menu=False)
        if detection.prefs_root:
            prefs = Path(detection.prefs_root)
            for candidate in (
                prefs / "Preferences" / "User Remote Scripts" / SCRIPT_FOLDER,
                prefs / "User Remote Scripts" / SCRIPT_FOLDER,
            ):
                result = _remove_owned_script_dir(candidate)
                removed.extend(result["removed"])
                skipped.extend(result["skipped"])
    return {
        "status": "REMOVED" if removed and not skipped else ("PRESERVED" if skipped else "ABSENT"),
        "removed": removed,
        "skipped": skipped,
    }


def _copilot_owned(manifest_path: Path) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("COPILOT_OWNED")) and payload.get("folder") == SCRIPT_FOLDER


def _atomic_copy(source: Path, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    shutil.copy2(source, tmp)
    tmp.replace(dest)


def _write_manifest(path: Path, digest: str, dest: Path) -> None:
    payload = {
        "folder": SCRIPT_FOLDER,
        "sha256": digest,
        "installed": str(dest),
        "source": str(VENDOR_SCRIPT.resolve()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "COPILOT_OWNED": True,
        "port": SCRIPT_PORT,
        "host": SCRIPT_HOST,
        "protocol": SCRIPT_PROTOCOL,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _retire_duplicate_copies(detection: Any, kept: Path, digest: str) -> list[str]:
    retired: list[str] = []
    if not getattr(detection, "prefs_root", None):
        return retired
    prefs = Path(detection.prefs_root)
    candidates = [
        prefs / "Preferences" / "User Remote Scripts" / SCRIPT_FOLDER / "__init__.py",
        prefs / "User Remote Scripts" / SCRIPT_FOLDER / "__init__.py",
    ]
    kept_resolved = kept.resolve()
    for extra in candidates:
        try:
            if not extra.is_file() or extra.resolve() == kept_resolved:
                continue
        except OSError:
            continue
        current = hashlib.sha256(extra.read_bytes()).hexdigest()
        if current != digest:
            continue
        extra.unlink()
        retired.append(str(extra))
        manifest = extra.with_name(MANIFEST_NAME)
        if manifest.is_file():
            manifest.unlink()
            retired.append(str(manifest))
        parent = extra.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            retired.append(str(parent))
    return retired


def _remove_owned_script_dir(dest_dir: Path) -> dict[str, list[str]]:
    removed: list[str] = []
    skipped: list[str] = []
    if not dest_dir.is_dir():
        return {"removed": removed, "skipped": skipped}
    dest = dest_dir / "__init__.py"
    manifest = dest_dir / MANIFEST_NAME
    owned = _copilot_owned(manifest)
    matches = dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == source_digest()
    if not owned and not matches:
        skipped.append(str(dest_dir))
        return {"removed": removed, "skipped": skipped}
    unknown = [
        child.name
        for child in dest_dir.iterdir()
        if child.name not in OWNED_FILES and child.suffix != ".part"
    ]
    for name in ("__init__.py", MANIFEST_NAME, "__init__.py.part"):
        path = dest_dir / name
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    if unknown:
        skipped.append(str(dest_dir))
        return {"removed": removed, "skipped": skipped}
    try:
        dest_dir.rmdir()
        removed.append(str(dest_dir))
    except OSError:
        skipped.append(str(dest_dir))
    return {"removed": removed, "skipped": skipped}
