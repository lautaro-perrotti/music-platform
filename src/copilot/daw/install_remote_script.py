from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from copilot.daw.detect import detect_ableton

VENDOR_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "vendor"
    / "abletonmcp_remote_script"
    / "AbletonMCP"
    / "__init__.py"
)
SCRIPT_PORT = 9877
SCRIPT_PROTOCOL = "tcp-json"
SCRIPT_HOST = "127.0.0.1"


def install_remote_script() -> dict:
    detection = detect_ableton(SCRIPT_PORT)
    if not VENDOR_SCRIPT.exists():
        return {
            "status": "FAILED",
            "error": f"Vendored Remote Script missing: {VENDOR_SCRIPT}",
            "detection": detection.to_dict(),
        }
    digest = hashlib.sha256(VENDOR_SCRIPT.read_bytes()).hexdigest()
    if not detection.found or not detection.user_remote_scripts:
        return {
            "status": "BLOCKED_BY_ENVIRONMENT",
            "error": "Ableton Live installation not found; refusing to invent Live prefs",
            "source_script": str(VENDOR_SCRIPT),
            "sha256": digest,
            "port": SCRIPT_PORT,
            "host": SCRIPT_HOST,
            "protocol": SCRIPT_PROTOCOL,
            "detection": detection.to_dict(),
            "required_after_install": [
                "Install Ableton Live 11 or 12",
                "python -m copilot.cli install-script",
                "Live → Settings → Link, Tempo & MIDI → Control Surface = AbletonMCP",
                "Input/Output = None",
                "python -m copilot.cli probe",
            ],
        }
    dest_dir = Path(detection.user_remote_scripts) / "AbletonMCP"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "__init__.py"
    shutil.copy2(VENDOR_SCRIPT, dest)
    prefs_scripts = Path(detection.prefs_root) / "Preferences" / "User Remote Scripts" / "AbletonMCP"
    if detection.prefs_root and prefs_scripts.parent.exists():
        prefs_scripts.mkdir(parents=True, exist_ok=True)
        shutil.copy2(VENDOR_SCRIPT, prefs_scripts / "__init__.py")
    if not dest.is_file():
        return {
            "status": "FAILED",
            "error": f"Copy reported success but file missing: {dest}",
            "detection": detection.to_dict(),
        }
    installed_digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if installed_digest != digest:
        return {
            "status": "FAILED",
            "error": "Installed Remote Script checksum mismatch",
            "source_sha256": digest,
            "installed_sha256": installed_digest,
            "install_path": str(dest),
            "detection": detection.to_dict(),
        }
    return {
        "status": "INSTALLED",
        "install_path": str(dest),
        "ableton_version": detection.version,
        "remote_script": "jpoindexter/ableton-mcp AbletonMCP_Remote_Script",
        "sha256": installed_digest,
        "source_sha256": digest,
        "file_exists": True,
        "checksum_match": True,
        "host": SCRIPT_HOST,
        "port": SCRIPT_PORT,
        "protocol": SCRIPT_PROTOCOL,
        "required_live_setting": "Control Surface = AbletonMCP; Input/Output = None",
        "detection": detection.to_dict(),
    }
