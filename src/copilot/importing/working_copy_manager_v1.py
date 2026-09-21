"""WORKING_COPY_MANAGER_V1 — duplicate an Ableton project; never touch the original."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MILESTONE = "WORKING_COPY_MANAGER_V1"
DEFAULT_WORKSPACE = Path.home() / "CopilotProjects"
MANIFEST_NAME = "copilot_import.json"


def is_copilot_working_copy(path: str | Path) -> bool:
    """Return true only for a manifest-backed, protected working copy."""
    target = Path(path)
    manifest = target.parent / MANIFEST_NAME
    if not target.is_file() or not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    recorded = Path(str(payload.get("working_als") or ""))
    try:
        same_target = recorded.resolve() == target.resolve()
    except OSError:
        same_target = str(recorded) == str(target)
    return same_target and payload.get("ORIGINAL_UNTOUCHED") is True


def is_copilot_source(path: str | Path) -> bool:
    """Return true when a protected working-copy manifest names ``path`` as source."""
    target = Path(path)
    roots = {DEFAULT_WORKSPACE}
    configured = os.environ.get("COPILOT_WORKING_COPY_ROOT")
    if configured:
        roots.add(Path(configured))
    try:
        target_resolved = target.resolve()
    except OSError:
        target_resolved = target
    for root in roots:
        if not root.is_dir():
            continue
        for manifest in root.glob(f"*/{MANIFEST_NAME}"):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                source = Path(str(payload.get("source_als") or ""))
                working = Path(str(payload.get("working_als") or ""))
                if not source.is_file() or not working.is_file():
                    continue
                if source.resolve() == target_resolved and payload.get("ORIGINAL_UNTOUCHED") is True:
                    return True
            except (OSError, json.JSONDecodeError):
                continue
    return False


def default_working_copy_candidate() -> Path:
    root = Path(os.environ.get("COPILOT_WORKING_COPY_ROOT", Path.home() / "CopilotProjects"))
    return root / "working_copy.als"


def find_working_copy(candidate: str | Path | None = None) -> Path | None:
    """Resolve only manifest-backed working copies; never guess a project name."""
    if candidate is not None:
        path = Path(candidate)
        if path.is_file() and is_copilot_working_copy(path):
            return path
        if path.is_dir():
            manifests = sorted(path.glob(f"*/{MANIFEST_NAME}"))
            for manifest in manifests:
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                working = Path(str(payload.get("working_als") or ""))
                if working.is_file() and is_copilot_working_copy(working):
                    return working
    fallback = default_working_copy_candidate()
    if fallback.is_file() and is_copilot_working_copy(fallback):
        return fallback
    root = fallback.parent
    if root.is_dir():
        for manifest in sorted(root.glob(f"*/{MANIFEST_NAME}")):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            working = Path(str(payload.get("working_als") or ""))
            if working.is_file() and is_copilot_working_copy(working):
                return working
    return None


def create_working_copy(
    *,
    source_als: str | Path,
    project_root: str | Path,
    copy_scope: str,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    source_als_path = Path(source_als)
    project_root_path = Path(project_root)
    if not source_als_path.is_file():
        return {
            "milestone": MILESTONE,
            "status": "SOURCE_ALS_MISSING",
            "source_als": str(source_als_path),
        }
    dest_root = Path(workspace) if workspace is not None else DEFAULT_WORKSPACE
    dest_root.mkdir(parents=True, exist_ok=True)
    identity = import_identity(source_als_path, project_root_path)
    existing = _find_reusable(dest_root, identity)
    if existing is not None:
        return existing

    name = _safe_name(project_root_path.name if copy_scope == "project_directory" else source_als_path.stem)
    dest = _unique_dir(dest_root, name)
    if copy_scope == "project_directory":
        shutil.copytree(project_root_path, dest)
        working_als = dest / source_als_path.name
    else:
        dest.mkdir(parents=True, exist_ok=False)
        working_als = dest / source_als_path.name
        shutil.copy2(source_als_path, working_als)

    if not working_als.is_file():
        shutil.rmtree(dest, ignore_errors=True)
        return {
            "milestone": MILESTONE,
            "status": "WORKING_ALS_MISSING",
            "source_als": str(source_als_path),
            "working_root": str(dest),
        }

    manifest = {
        "milestone": MILESTONE,
        "status": "CREATED",
        "source_root": str(project_root_path),
        "source_als": str(source_als_path),
        "working_root": str(dest),
        "working_als": str(working_als),
        "copy_scope": copy_scope,
        "copied_at": datetime.now(timezone.utc).isoformat(),
        "identity": identity,
        "ORIGINAL_UNTOUCHED": True,
    }
    (dest / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def import_identity(source_als: Path, project_root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    digest.update(source_als.read_bytes())
    sample_bytes = 0
    sample_files = 0
    samples = project_root / "Samples"
    if samples.is_dir():
        for item in samples.rglob("*"):
            if item.is_file():
                sample_files += 1
                sample_bytes += item.stat().st_size
    return {
        "als_sha256": digest.hexdigest(),
        "als_size": source_als.stat().st_size,
        "sample_files": sample_files,
        "sample_bytes": sample_bytes,
        "source_als": str(source_als.resolve()),
    }


def _find_reusable(workspace: Path, identity: dict[str, Any]) -> dict[str, Any] | None:
    if not workspace.is_dir():
        return None
    for manifest_path in workspace.glob(f"*/{MANIFEST_NAME}"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stored = payload.get("identity") or {}
        working_als = Path(str(payload.get("working_als") or ""))
        if stored.get("als_sha256") == identity.get("als_sha256") and working_als.is_file():
            payload["status"] = "REUSED"
            payload["ORIGINAL_UNTOUCHED"] = True
            return payload
    return None


def _safe_name(raw: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " .-_()" else "_" for ch in raw).strip()
    return cleaned or "imported_project"


def _unique_dir(workspace: Path, name: str) -> Path:
    candidate = workspace / name
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = workspace / f"{name}__{index}"
        if not candidate.exists():
            return candidate
        index += 1
