"""Working-copy policy. Never operate on the sole copy of a song."""

from __future__ import annotations

from typing import Any

from copilot.audio.cross_project_bootstrap_v1 import classify_project

MILESTONE = "WORKING_COPY_POLICY_V1"
STATUS = "VERIFIED"

# Human duplicates first. Core never copies the .als itself.
OPERATOR_MUST_DUPLICATE = True


def evaluate_working_copy(
    project_path: str | None,
    project_name: str | None = None,
) -> dict[str, Any]:
    identity = classify_project(project_path, project_name)
    kind = identity["kind"]
    controlled = kind in {"development_working_copy", "bootstrap_fixture"}
    operate = controlled and not identity["refuse_original"]
    read_only_ok = kind in {
        "development_working_copy",
        "bootstrap_fixture",
        "external",
    }
    autonomous_writes_ok = identity["is_development_working_copy"]
    musical_holdout = kind == "external"
    plumbing_only = kind == "bootstrap_fixture"
    reason = "OK"
    if kind == "development_original":
        reason = "ORIGINAL_SET_OPEN"
    elif kind == "unknown":
        reason = "PROJECT_UNIDENTIFIED"
        operate = False
        read_only_ok = False
    elif kind == "untitled_scratch":
        reason = "UNTITLED_NOT_A_HOLDOUT"
        operate = False
        read_only_ok = False
    elif kind == "bootstrap_fixture":
        reason = "FIXTURE_PLUMBING_ONLY"
    elif kind == "external":
        reason = "WORKING_COPY_REQUIRED"
    elif kind == "development_working_copy":
        reason = "DEVELOPMENT_WORKING_COPY"
    return {
        "milestone": MILESTONE,
        "status": STATUS,
        "kind": kind,
        "operate": operate,
        "read_only_ok": read_only_ok,
        "autonomous_writes_ok": autonomous_writes_ok,
        "musical_holdout": musical_holdout,
        "plumbing_only": plumbing_only,
        "refuse_original": identity["refuse_original"],
        "OPERATOR_MUST_DUPLICATE": OPERATOR_MUST_DUPLICATE,
        "reason": reason,
        "project": identity,
        "NO MUSICAL WRITES": not autonomous_writes_ok,
    }
