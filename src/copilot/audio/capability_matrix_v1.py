"""Canonical capability matrix for the supported envelope. No mock success."""

from __future__ import annotations

from typing import Any

from copilot.audio.m4l_control_contract_v1 import MILESTONE as M4L_MILESTONE
from copilot.audio.working_copy_policy_v1 import MILESTONE as WORKING_COPY_MILESTONE
from copilot.daw.session_ready_v1 import FROZEN as SESSION_FROZEN
from copilot.daw.session_ready_v1 import MILESTONE as SESSION_MILESTONE
from copilot.daw.session_ready_v1 import STATUS as SESSION_STATUS

MILESTONE = "CAPABILITY_MATRIX_V1"

# claim: what is true today. Not a promise of musical generalization.
ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": SESSION_MILESTONE,
        "command": "doctor",
        "claim": "TCP + hello/request-id + light snapshot + project identity",
        "status": SESSION_STATUS,
        "frozen": SESSION_FROZEN,
        "writes": False,
    },
    {
        "id": WORKING_COPY_MILESTONE,
        "command": "onboard-project",
        "claim": "Refuse pista.als; treat open .als as the working copy; fixture is plumbing",
        "status": "VERIFIED",
        "frozen": False,
        "writes": False,
    },
    {
        "id": "CROSS_PROJECT_BOOTSTRAP_V1",
        "command": "project-bootstrap",
        "claim": "Install Main tap + capture hosts; second run NO_CHANGES_REQUIRED",
        "status": "VERIFIED",
        "frozen": False,
        "writes": False,
        "note": "Infra writes only. No musical mutations. Fixture live-validated.",
    },
    {
        "id": "PROJECT_READY_V1",
        "command": "onboard-project",
        "claim": "Identity -> bootstrap -> generic or session preflight",
        "status": "VERIFIED",
        "frozen": False,
        "writes": False,
        "note": "Fixture VERIFIED. Alias: project-ready.",
    },
    {
        "id": "PRODUCER_ANALYZE_V1",
        "command": "producer-analyze",
        "claim": "Read-only capture -> evidence pack -> Astra once -> gate. Statuses preserved.",
        "status": "COMMAND_VERIFIED",
        "frozen": False,
        "writes": False,
        "note": "Fixture INSUFFICIENT_EVIDENCE (empty arrangement). Musical generalization unproven.",
    },
    {
        "id": "PRODUCER_RUN_V1",
        "command": "producer-run",
        "claim": "analyze always; autonomous SET_TRACK_VOLUME only on development working copy",
        "status": "COMMAND_VERIFIED",
        "frozen": False,
        "writes": "development_working_copy_only",
        "note": "External projects: ACTION_NOT_AVAILABLE until CROSS_PROJECT_MUSICAL_VALIDATION_V1.",
    },
    {
        "id": "CROSS_PROJECT_MUSICAL_VALIDATION_V1",
        "command": "cross-project-validate",
        "claim": "First unseen real song, read-only. Result decides the next capability.",
        "status": "WAITING_FOR_EXTERNAL_SONG",
        "frozen": False,
        "writes": False,
        "note": "pista / fixture / untitled are not this test.",
    },
    {
        "id": M4L_MILESTONE,
        "command": None,
        "claim": "ANALYZE/STATUS/DIAGNOSIS/PROPOSED ACTION/APPLY/ROLLBACK - no frontend",
        "status": "DOCUMENTED",
        "frozen": True,
        "writes": "APPLY only when gate justifies SET_TRACK_VOLUME",
    },
    {
        "id": "SECOND_MACHINE_INSTALLER_V1",
        "command": "install",
        "claim": "Windows installer: venv, Copilot Remote Script, M4L, config template. No secrets.",
        "status": "IMPLEMENTED",
        "frozen": False,
        "writes": False,
        "note": "Infra only. SECOND_MACHINE_PORTABILITY_V1 waits on a friend machine.",
    },
    {
        "id": "REGRESSION_V1",
        "command": "regression-v1",
        "claim": "Offline supported-envelope suite. No live destructive writes.",
        "status": "VERIFIED",
        "frozen": False,
        "writes": False,
    },
)

UNSUPPORTED = (
    "EQ actions",
    "compressor actions",
    "MIDI editing",
    "arrangement editing",
    "unbounded plugin control",
    "web / Electron / React UI",
    "silent mock success",
    "collapsing producer statuses",
    "autonomous writes on an unvalidated external song",
)


def capability_matrix() -> dict[str, Any]:
    return {
        "milestone": MILESTONE,
        "LIVE_SESSION_READINESS_V1": f"{SESSION_STATUS} / FROZEN={SESSION_FROZEN}",
        "rows": list(ROWS),
        "unsupported": list(UNSUPPORTED),
        "NO MOCK SUCCESS": True,
        "musical_generalization": "UNPROVEN_UNTIL_SECOND_REAL_SONG",
    }
