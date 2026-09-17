"""Wait for a real Ableton session, then run the canonical read-only envelope."""

from __future__ import annotations

import json
import time
from pathlib import Path

from copilot.cli import main
from copilot.daw.session_ready_v1 import (
    SESSION_READY,
    STALE_PLAN,
    probe_session_ready,
    revalidate_project,
    wait_for_session,
)
from copilot.human_eval.store import now_iso


def write_status(path: Path, **body) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": now_iso(), **body}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, default=str), flush=True)


def run() -> int:
    evidence = Path("logs")
    status_path = evidence / "sprint_waiter.json"
    write_status(
        status_path,
        phase="WAITING_FOR_LIVE",
        writes_queued=0,
        reuse_stale_plans=False,
    )
    ready = wait_for_session(deadline_s=45 * 60)
    write_status(
        status_path,
        phase="LIVE_SESSION_READY" if ready.status == SESSION_READY else "TIMEOUT",
        session=ready.to_dict(),
        writes_queued=0,
    )
    if ready.status != SESSION_READY or not ready.writes_permitted:
        write_status(
            status_path,
            phase="TIMEOUT",
            status="BLOCKED",
            reason=ready.reason or "LIVE_SESSION_NOT_READY",
            session=ready.to_dict(),
            writes_queued=0,
        )
        return 2

    expected_identity = ready.project_identity
    codes: dict[str, int] = {}
    commands = (
        ["doctor"],
        ["project-ready"],
        ["producer-analyze"],
    )
    envelope_restarts = 0
    index = 0
    while index < len(commands):
        current = probe_session_ready()
        check = revalidate_project(expected_identity, current)
        write_status(
            status_path,
            phase="PROJECT_REVALIDATED" if check.get("PROJECT_REVALIDATED") else check["status"],
            session=current.to_dict(),
            revalidate=check,
            writes_queued=0,
        )
        if current.status != SESSION_READY:
            time.sleep(2)
            current = wait_for_session(deadline_s=60, previous_identity=expected_identity)
            if current.status != SESSION_READY:
                write_status(
                    status_path,
                    phase="FAILED",
                    status="BLOCKED",
                    reason=current.reason,
                    session=current.to_dict(),
                    writes_queued=0,
                )
                return 2
            check = revalidate_project(expected_identity, current)
        if check["status"] == STALE_PLAN:
            expected_identity = current.project_identity
            codes = {}
            index = 0
            envelope_restarts += 1
            if envelope_restarts > 3:
                write_status(
                    status_path,
                    phase="FAILED",
                    status="BLOCKED",
                    reason="STALE_PLAN",
                    session=current.to_dict(),
                    writes_queued=0,
                )
                return 2
            continue
        command = commands[index]
        name = command[0]
        last_code = 2
        for attempt in range(1, 4):
            write_status(
                status_path,
                phase=f"RUNNING_{name}",
                attempt=attempt,
                codes=codes,
                project_identity=expected_identity,
                writes_queued=0,
            )
            last_code = main(command)
            if last_code == 0:
                break
            time.sleep(min(8, 2 ** attempt))
        codes[name] = last_code
        write_status(status_path, phase=f"DONE_{name}", codes=codes)
        if last_code != 0:
            write_status(
                status_path,
                phase="FAILED",
                status="BLOCKED",
                codes=codes,
                failed=name,
                writes_queued=0,
            )
            return 2
        index += 1
    overall = 0 if all(code == 0 for code in codes.values()) else 2
    write_status(
        status_path,
        phase="COMPLETE",
        codes=codes,
        status="DONE",
        overall=overall,
        writes_queued=0,
        PROJECT_REVALIDATED=True,
    )
    return overall


if __name__ == "__main__":
    raise SystemExit(run())
