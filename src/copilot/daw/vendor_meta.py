from __future__ import annotations

import hashlib
import json
from pathlib import Path

VENDOR_ROOT = Path(__file__).resolve().parents[3] / "vendor" / "abletonmcp_remote_script"
VENDOR_SCRIPT = VENDOR_ROOT / "AbletonMCP" / "__init__.py"
VENDOR_META = VENDOR_ROOT / "VENDOR_META.json"

REQUIRED_COMMANDS = (
    "health_check",
    "protocol_hello",
    "get_session_info",
    "get_playback_position",
    "get_track_info",
    "get_tracks_info",
    "get_capture_topology",
    "create_midi_track",
    "set_track_name",
    "create_clip",
    "add_notes_to_clip",
    "get_clip_notes",
    "delete_track",
    "delete_clip",
    "set_device_parameter",
)


def script_checksum() -> str:
    return hashlib.sha256(VENDOR_SCRIPT.read_bytes()).hexdigest()


def load_meta() -> dict:
    return json.loads(VENDOR_META.read_text(encoding="utf-8"))
