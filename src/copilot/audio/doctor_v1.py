"""Read-only copilot doctor. No secrets. No writes."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from copilot.audio.cross_project_bootstrap_v1 import (
    classify_project,
    discover_topology,
    missing_topology,
    plan_bootstrap,
)
from copilot.audio.fullmix import ANALYZER_ID as FULLMIX_ID
from copilot.audio.fullmix import analyzer_fingerprint as fullmix_fingerprint
from copilot.audio.live_capture import DEVICE_SOURCE, USER_LIBRARY_TAP, master_tap_position
from copilot.audio.lowend_features import ANALYZER_ID as LOWEND_ID, analyzer_fingerprint
from copilot.audio.tap_trust import inventory_taps
from copilot.audio.terminal_state_v1 import verify_terminal_state
from copilot.audio.capability_matrix_v1 import capability_matrix
from copilot.daw.detect import detect_ableton, live_block_status
from copilot.daw.session_ready_v1 import SESSION_READY, SessionReadyProbe
from copilot.human_eval.store import now_iso
from copilot.reasoning.provider import configured_http_provider

MILESTONE = "COPILOT_DOCTOR_V1"
ARTIFACT = "doctor_v1.json"
ACTION_VOCABULARY = ("SET_TRACK_VOLUME",)
MIN_FREE_BYTES = 256 * 1024 * 1024


def _env_configured(name: str) -> bool:
    if os.environ.get(name):
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


def doctor(
    *,
    evidence: Path,
    daw: Any | None = None,
    session_probe: SessionReadyProbe | None = None,
) -> dict[str, Any]:
    detection = detect_ableton()
    capture_root = Path("logs")
    try:
        usage = shutil.disk_usage(capture_root if capture_root.exists() else Path.cwd())
        disk = {
            "writable": os.access(capture_root if capture_root.exists() else Path.cwd(), os.W_OK),
            "free_bytes": usage.free,
            "free_enough": usage.free >= MIN_FREE_BYTES,
        }
    except OSError as exc:
        disk = {"writable": False, "error": str(exc), "free_enough": False}

    astra = configured_http_provider()
    session_status = (
        session_probe.status if session_probe is not None else "SESSION_NOT_PROBED"
    )
    # Port presence is not operational readiness.
    reachable = False
    if session_probe is not None:
        reachable = session_probe.status == SESSION_READY
    checks: dict[str, Any] = {
        "ableton_reachable": reachable,
        "ableton_found": detection.found,
        "ableton_version": detection.version,
        "connection_status": live_block_status(detection),
        "session_status": session_status,
        "zombie_port": bool(session_probe.zombie_port) if session_probe is not None else False,
        "writes_permitted": bool(session_probe.writes_permitted) if session_probe is not None else False,
        "remote_script_port_open": detection.port_open,
        "max_for_live_device_source": DEVICE_SOURCE.is_file(),
        "max_for_live_user_library": USER_LIBRARY_TAP.is_file(),
        "python_runtime": {
            "ok": True,
            "executable": sys.executable,
        },
        "capture_path": str(capture_root.resolve()),
        "disk": disk,
        "astra_configured": astra is not None,
        "astra_provider": None if astra is None else astra.identity,
        "canonical_action_vocabulary": list(ACTION_VOCABULARY),
        "frozen_analyzers": {
            "lowend": {"id": LOWEND_ID, "sha256": analyzer_fingerprint()},
            "fullmix": {"id": FULLMIX_ID, "sha256": fullmix_fingerprint()},
        },
        "secrets_exposed": False,
        "api_key_names_present": [
            name
            for name in ("COPILOT_REASONING_API_KEY", "OPENAI_API_KEY")
            if _env_configured(name)
        ],
    }
    # Never include key material.
    project: dict[str, Any] = {}
    ready: dict[str, Any] = {}
    if daw is not None:
        try:
            session = daw.snapshot(include_notes=False)
            inventory = inventory_taps(daw)
            discovery = discover_topology(
                session=session,
                inventory=inventory,
                master_pos=master_tap_position(daw),
            )
            project = classify_project(session.project_path, session.project_name)
            project["project_identity"] = session.project_identity or session.project_token
            project["track_count"] = len(session.tracks)
            missing = missing_topology(discovery)
            plan = plan_bootstrap(discovery)
            terminal = verify_terminal_state(
                transport_playing=session.transport.playing,
                taps=inventory,
            )
            ready = {
                "project_ready_estimate": (
                    "VERIFIED"
                    if not missing and plan.get("status") == "NO_CHANGES_REQUIRED" and terminal.get("ok")
                    else "BLOCKED"
                ),
                "bootstrap_plan_status": plan.get("status"),
                "missing_topology": missing,
                "main_final": bool((discovery.get("main") or {}).get("is_last")),
                "eligible_sources": discovery.get("eligible_source_names"),
                "open_capture_journals": len(terminal.get("open_capture_journals") or []),
                "open_transactions": len(terminal.get("open_transactions") or []),
            }
            checks["remote_script_protocol"] = (daw.handshake_info or {}).get(
                "protocol_version"
            )
            checks["bridge_version"] = (daw.handshake_info or {}).get("bridge_version")
            checks["terminal"] = terminal
            checks["capture_topology"] = {
                "main": discovery.get("main"),
                "hosts": discovery.get("hosts"),
                "slot_collisions": discovery.get("slot_collisions"),
            }
            checks["ableton_reachable"] = True
            checks["session_status"] = SESSION_READY
            checks["writes_permitted"] = True
        except Exception as exc:  # noqa: BLE001
            checks["live_session_error"] = str(exc)
            checks["ableton_reachable"] = False
            checks["session_status"] = "SESSION_NOT_READY"
            checks["writes_permitted"] = False
    elif session_probe is not None and session_probe.status == SESSION_READY:
        checks["ableton_reachable"] = True
        checks["project_identity"] = session_probe.project_identity

    failures = []
    if not checks["ableton_reachable"]:
        failures.append("ableton_not_reachable")
    if session_probe is not None and session_probe.status != SESSION_READY:
        failures.append("session_not_ready")
        if session_probe.zombie_port or session_probe.status == "ZOMBIE_PORT":
            failures.append("zombie_port")
    if not checks["max_for_live_device_source"] and not checks["max_for_live_user_library"]:
        failures.append("m4l_device_missing")
    if not disk.get("writable") or not disk.get("free_enough"):
        failures.append("disk")
    if not checks["astra_configured"]:
        failures.append("astra_not_configured")

    report = {
        "milestone": MILESTONE,
        "status": "READY" if not failures else "BLOCKED",
        "failures": failures,
        "checks": checks,
        "project": project,
        "project_ready_hint": ready,
        "capabilities": capability_matrix(),
        "LIVE_SESSION_READINESS_V1": "VERIFIED / FROZEN",
        "NO WRITE": True,
        "ts": now_iso(),
    }
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / ARTIFACT).write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return report
