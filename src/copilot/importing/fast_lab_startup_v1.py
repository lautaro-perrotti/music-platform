"""Start one disposable Live lab while preserving the user's recovery data."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from copilot.daw.ableton_tcp import DEFAULT_HOST, DEFAULT_PORT
from copilot.daw.detect import detect_ableton
from copilot.daw.session_ready_v1 import SESSION_READY, probe_session_ready, tcp_connectable
from copilot.importing.ableton_launcher_v1 import paths_match
from copilot.importing.working_copy_manager_v1 import is_copilot_working_copy
from copilot.platform.ableton import driver_for_system

FLAG = b"-NoRestoreDocumentDialog"


def _replace_bytes(path: Path, content: bytes) -> None:
    staged = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        staged.write_bytes(content)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _flagged_options(original: bytes | None) -> bytes:
    if original is None:
        return FLAG + b"\r\n"
    if any(line.strip() == FLAG for line in original.splitlines()):
        return original
    separator = b"\r\n" if b"\r\n" in original else b"\n"
    prefix = b"" if not original or original.endswith((b"\r", b"\n")) else separator
    return original + prefix + FLAG + separator


def launch_disposable_fast_lab(
    working_als: str | Path,
    *,
    deadline_s: float = 180.0,
    force: bool = False,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> dict:
    """Launch exactly once, without touching Crash/ or recovery metadata."""
    target = Path(working_als).resolve()
    if not is_copilot_working_copy(target):
        return {"status": "WORKING_COPY_REQUIRED", "working_als": str(target)}
    detection = detect_ableton(port, include_start_menu=False)
    if detection.process_running or detection.port_open:
        return {"status": "LIVE_SESSION_CONFLICT", "working_als": str(target)}
    if not detection.exe_path or not detection.prefs_root:
        return {"status": "ABLETON_NOT_FOUND", "working_als": str(target)}

    options = Path(detection.prefs_root) / "Preferences" / "Options.txt"
    original = options.read_bytes() if options.exists() else None
    changed = original is None or _flagged_options(original) != original
    report: dict = {
        "status": "STARTING",
        "working_als": str(target),
        "options_path": str(options),
        "options_preexisting": original is not None,
        "options_flag_added": changed,
        "recovery_metadata_explicitly_mutated": False,
        "restarts": 0,
        "hello_attempts": 0,
        "port_opened": False,
    }
    try:
        if changed:
            _replace_bytes(options, _flagged_options(original))
        if FLAG not in options.read_bytes():
            raise RuntimeError("STARTUP_FLAG_NOT_APPLIED")
        process = driver_for_system().launch(detection.exe_path, target)
        report["pid"] = process.pid
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            if process.poll() is not None:
                report.update(status="LIVE_EXITED", exit_code=process.returncode)
                break
            if not tcp_connectable(host, port, timeout=0.25):
                time.sleep(0.5)
                continue
            report["port_opened"] = True
            report["hello_attempts"] += 1
            probe = probe_session_ready(host, port)
            report["last_probe"] = probe.to_dict()
            if probe.status == SESSION_READY:
                if not paths_match(probe.project_path, target):
                    report["status"] = "OPENED_PROJECT_MISMATCH"
                else:
                    report.update(
                        status=SESSION_READY,
                        project_identity=probe.project_identity,
                        project_path=probe.project_path,
                    )
                break
            time.sleep(1.0)
        else:
            report["status"] = "STARTUP_TIMEOUT"
    finally:
        if original is None:
            options.unlink(missing_ok=True)
        elif changed:
            _replace_bytes(options, original)
        report["options_restored_exactly"] = (
            options.read_bytes() == original if original is not None else not options.exists()
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("working_als", type=Path)
    parser.add_argument("--deadline-s", type=float, default=180.0)
    args = parser.parse_args()
    from copilot.runtime.environment_autonomy_v1 import ensure_ableton_ready

    result = ensure_ableton_ready(
        working_als=args.working_als,
        deadline_s=args.deadline_s,
        launcher=launch_disposable_fast_lab,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
