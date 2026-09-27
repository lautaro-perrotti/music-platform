"""Persistence state for the Studio/Live boundary.

SafeWrite proves that a mutation was applied and read back in Live.  It does
not prove that the working copy on disk was saved, and it never decides the
human musical verdict.  This module keeps those facts separate without
creating another Live transaction system.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from copilot.audio.working_copy_policy_v1 import evaluate_working_copy
from copilot.daw.state_tokens import attach_tokens
from copilot.schemas.session import SessionState


PERSISTENCE_IN_SYNC = "IN_SYNC"
PERSISTENCE_LIVE_DIRTY = "LIVE_DIRTY"
PERSISTENCE_CANDIDATE_PENDING = "CANDIDATE_PENDING"
PERSISTENCE_DISK_SAVE_REQUIRED = "DISK_SAVE_REQUIRED"
PERSISTENCE_SAVE_IN_PROGRESS = "SAVE_IN_PROGRESS"
PERSISTENCE_CHECKPOINTED = "CHECKPOINTED"
PERSISTENCE_SAVE_FAILED = "SAVE_FAILED"
PERSISTENCE_UNKNOWN = "UNKNOWN"

MUSICAL_DECISION_PENDING = "PENDING"
MUSICAL_DECISION_ACCEPTED = "MUSICAL_ACCEPTED"
MUSICAL_DECISION_KEPT = "KEPT"
MUSICAL_DECISION_DISCARDED = "DISCARDED"


def disk_evidence(path: str | Path | None) -> dict[str, Any]:
    """Return immutable, read-only evidence for one .als path."""
    raw = str(path or "").strip()
    if not raw:
        return {"path": "", "exists": False, "reason": "PROJECT_PATH_MISSING"}
    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate.absolute()
    if not resolved.is_file():
        return {"path": str(resolved), "exists": False, "reason": "FILE_NOT_FOUND"}
    try:
        stat = resolved.stat()
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "path": str(resolved),
            "exists": True,
            "bytes": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": digest.hexdigest(),
            "writable": bool(os.access(resolved, os.W_OK)),
        }
    except OSError as exc:
        return {"path": str(resolved), "exists": False, "reason": f"STAT_FAILED:{exc}"}


def live_state_evidence(session: SessionState | None) -> dict[str, Any]:
    if session is None:
        return {}
    return {
        "project_identity": session.project_identity,
        "project_token": session.project_token,
        "audible_token": session.audible_token,
        "state_hash": session.state_hash,
        "revision": session.revision,
        "session_incarnation_id": session.session_incarnation_id,
    }


def candidate_persistence_state(
    before: SessionState,
    after: SessionState,
) -> dict[str, Any]:
    """Describe a verified Live candidate without claiming it was saved."""
    return {
        "status": PERSISTENCE_CANDIDATE_PENDING,
        "musical_decision": MUSICAL_DECISION_PENDING,
        "project_path": after.project_path or before.project_path or "",
        "project_identity": after.project_identity or before.project_identity,
        "disk_before": disk_evidence(before.project_path or after.project_path),
        "disk_after": disk_evidence(after.project_path or before.project_path),
        "live_before": live_state_evidence(before),
        "live_after": live_state_evidence(after),
        "save_attempted": False,
        "save_verified": False,
    }


def _same_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return left.replace("\\", "/").rstrip("/").casefold() == right.replace("\\", "/").rstrip("/").casefold()


def persist_working_copy(
    daw: Any,
    session: SessionState,
    *,
    expected_project_identity: str,
) -> dict[str, Any]:
    """Save and verify the controlled working copy through the existing adapter.

    This function deliberately refuses to infer support from the presence of a
    ``save_session`` Python method.  A TCP adapter must advertise the typed
    ``session.save`` capability before a save can be attempted.
    """
    path = str(session.project_path or "")
    policy = evaluate_working_copy(path, session.project_name)
    base = {
        "status": PERSISTENCE_SAVE_FAILED,
        "musical_decision": MUSICAL_DECISION_PENDING,
        "project_path": path,
        "project_identity": session.project_identity,
        "disk_before": disk_evidence(path),
        "live_before": live_state_evidence(session),
        "save_attempted": False,
        "save_verified": False,
    }
    if not policy.get("autonomous_writes_ok") or not policy.get("operate"):
        return {**base, "reason": "WORKING_COPY_REQUIRED", "policy": policy}
    if not path or not Path(path).suffix.casefold() == ".als":
        return {**base, "reason": "DURABLE_PROJECT_PATH_REQUIRED", "policy": policy}
    if expected_project_identity and session.project_identity != expected_project_identity:
        return {**base, "reason": "PROJECT_MISMATCH", "policy": policy}

    capabilities = getattr(daw, "capabilities", None)
    if capabilities is not None and "session.save" not in set(capabilities):
        return {
            **base,
            "reason": "SAVE_CAPABILITY_UNAVAILABLE",
            "required_capability": "session.save",
            "advertised_capabilities": sorted(str(item) for item in capabilities),
            "policy": policy,
        }

    try:
        save_result = daw.save_session()
    except Exception as exc:  # noqa: BLE001 - persist as a typed terminal state
        return {**base, "reason": f"SAVE_FAILED:{exc}", "save_attempted": True, "policy": policy}
    if save_result.get("saved") is not True:
        return {
            **base,
            "reason": "SAVE_ACK_MISSING",
            "save_attempted": True,
            "save_result": save_result,
            "policy": policy,
        }

    try:
        path_info = daw.get_session_path() or {}
        live_path = str(path_info.get("path") or "")
        after = daw.snapshot()
        if not after.project_identity:
            attach_tokens(after, path=live_path or after.project_path, name=after.project_name or path_info.get("name"))
    except Exception as exc:  # noqa: BLE001
        return {
            **base,
            "reason": f"SAVE_READBACK_FAILED:{exc}",
            "save_attempted": True,
            "save_result": save_result,
            "policy": policy,
        }
    disk_after = disk_evidence(live_path or path)
    identity_ok = after.project_identity == expected_project_identity
    path_ok = _same_path(live_path or path, path)
    file_ok = bool(disk_after.get("exists") and int(disk_after.get("bytes") or 0) > 0)
    if not identity_ok or not path_ok or not file_ok:
        return {
            **base,
            "reason": "SAVE_READBACK_MISMATCH",
            "save_attempted": True,
            "save_result": save_result,
            "disk_after": disk_after,
            "live_after": live_state_evidence(after),
            "identity_ok": identity_ok,
            "path_ok": path_ok,
            "file_ok": file_ok,
            "policy": policy,
        }
    return {
        **base,
        "status": PERSISTENCE_IN_SYNC,
        "musical_decision": MUSICAL_DECISION_KEPT,
        "save_attempted": True,
        "save_verified": True,
        "save_result": save_result,
        "disk_after": disk_after,
        "live_after": live_state_evidence(after),
        "disk_changed": base["disk_before"] != disk_after,
        "identity_ok": True,
        "path_ok": True,
        "file_ok": True,
        "policy": policy,
    }


def reconciled_after_rollback(
    session: SessionState | None,
    disk_before: dict[str, Any] | None,
) -> dict[str, Any]:
    """Classify post-rollback state without saving or inventing disk equality."""
    current = disk_evidence(session.project_path if session else None)
    disk_same = bool(disk_before and current == disk_before)
    status = PERSISTENCE_IN_SYNC if disk_same else PERSISTENCE_LIVE_DIRTY
    if not disk_before or not current.get("exists"):
        status = PERSISTENCE_UNKNOWN
    return {
        "status": status,
        "musical_decision": MUSICAL_DECISION_DISCARDED,
        "disk_before": disk_before or {},
        "disk_after": current,
        "live_after": live_state_evidence(session),
        "save_attempted": False,
        "save_verified": False,
    }
