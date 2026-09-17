"""PROJECT_FOLDER_RESOLVER_V1 — find an Ableton set in an arbitrary folder."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MILESTONE = "PROJECT_FOLDER_RESOLVER_V1"
BACKUP_DIR_NAMES = frozenset({"backup", "backups"})
PROJECT_DIR_MARKERS = frozenset(
    {
        "ableton project info",
        "samples",
        "recorded",
        "freeze",
    }
)


@dataclass(frozen=True)
class ProjectCandidate:
    path: str
    project_root: str
    file_type: str
    size: int
    modified_iso: str
    relationship: str
    in_backup: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_ableton_project(folder: str | Path) -> dict[str, Any]:
    root = Path(folder)
    if not root.exists() or not root.is_dir():
        return {
            "milestone": MILESTONE,
            "status": "FOLDER_NOT_FOUND",
            "folder": str(root),
            "candidates": [],
        }

    als: list[ProjectCandidate] = []
    alp: list[ProjectCandidate] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in {".als", ".alp"}:
            continue
        in_backup = _in_backup(path, root)
        project_root = infer_project_root(path)
        row = ProjectCandidate(
            path=str(path),
            project_root=str(project_root),
            file_type=suffix.lstrip("."),
            size=path.stat().st_size,
            modified_iso=_mtime(path),
            relationship=_relationship(path, project_root, in_backup),
            in_backup=in_backup,
        )
        if suffix == ".als":
            als.append(row)
        else:
            alp.append(row)

    primary = [row for row in als if not row.in_backup]
    if len(primary) == 1:
        chosen = primary[0]
        return {
            "milestone": MILESTONE,
            "status": "RESOLVED",
            "folder": str(root),
            "source_als": chosen.path,
            "project_root": chosen.project_root,
            "copy_scope": (
                "project_directory"
                if _looks_like_project_dir(Path(chosen.project_root))
                else "als_only"
            ),
            "candidates": [row.to_dict() for row in als + alp],
        }
    if len(primary) > 1:
        named = _name_match(primary, root)
        if named is not None:
            return {
                "milestone": MILESTONE,
                "status": "RESOLVED",
                "folder": str(root),
                "source_als": named.path,
                "project_root": named.project_root,
                "copy_scope": (
                    "project_directory"
                    if _looks_like_project_dir(Path(named.project_root))
                    else "als_only"
                ),
                "rule": "folder_name_matches_als_stem",
                "candidates": [row.to_dict() for row in als + alp],
            }
        return {
            "milestone": MILESTONE,
            "status": "PROJECT_SELECTION_REQUIRED",
            "folder": str(root),
            "candidates": [row.to_dict() for row in primary],
            "detail": "Multiple independent .als files. Will not guess.",
        }
    if alp and not als:
        return {
            "milestone": MILESTONE,
            "status": "ABLETON_PACK_INSTALL_REQUIRED",
            "folder": str(root),
            "candidates": [row.to_dict() for row in alp],
            "detail": "Only .alp found. Pack installation is not implemented in V1.",
        }
    return {
        "milestone": MILESTONE,
        "status": "NO_ABLETON_PROJECT",
        "folder": str(root),
        "candidates": [row.to_dict() for row in als + alp],
    }


def infer_project_root(als: Path) -> Path:
    parent = als.parent
    if _looks_like_project_dir(parent):
        return parent
    return parent


def _looks_like_project_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if path.name.lower().endswith(" project"):
        return True
    try:
        names = {child.name.lower() for child in path.iterdir()}
    except OSError:
        return False
    return bool(names & PROJECT_DIR_MARKERS)


def _in_backup(path: Path, root: Path) -> bool:
    try:
        parts = {part.lower() for part in path.relative_to(root).parts[:-1]}
    except ValueError:
        parts = {part.lower() for part in path.parts}
    return bool(parts & BACKUP_DIR_NAMES)


def _relationship(path: Path, project_root: Path, in_backup: bool) -> str:
    if in_backup:
        return "backup_version"
    if path.parent == project_root:
        return "primary_set"
    return "nested_set"


def _name_match(rows: list[ProjectCandidate], folder: Path) -> ProjectCandidate | None:
    needle = folder.name.lower().replace(" project", "").strip()
    hits = [
        row
        for row in rows
        if Path(row.path).stem.lower().replace(" project", "").strip() == needle
        or needle in Path(row.path).stem.lower()
    ]
    if len(hits) == 1:
        return hits[0]
    return None


def _mtime(path: Path) -> str:
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ts.isoformat()
