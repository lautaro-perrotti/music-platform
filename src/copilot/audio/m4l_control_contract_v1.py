"""Minimal M4L command/event contract. No UI. Frozen surface."""

from __future__ import annotations

from typing import Any

MILESTONE = "M4L_CONTROL_CONTRACT_V1"
STATUS = "DOCUMENTED"
FROZEN = True

COMMANDS: tuple[dict[str, Any], ...] = (
    {"command": "ANALYZE", "cli": "producer-analyze", "writes_music": False},
    {"command": "STATUS", "cli": "doctor then onboard-project", "writes_music": False},
    {"command": "DIAGNOSIS", "cli": "logs/producer_analyze_v1.json", "writes_music": False},
    {"command": "PROPOSED ACTION", "cli": "gate + MusicPlan dry-run", "writes_music": False},
    {"command": "APPLY", "cli": "producer-run --mode autonomous", "writes_music": True},
    {"command": "ROLLBACK", "cli": "existing transaction rollback", "writes_music": False},
)

EVENTS: tuple[str, ...] = (
    "STATUS",
    "DIAGNOSIS",
    "PROPOSED_ACTION",
    "APPLY_RESULT",
    "TERMINAL_STATE",
)

APPLY_VOCABULARY = ("SET_TRACK_VOLUME",)
APPLY_RESULTS = ("KEEP", "ROLLBACK", "ABSTAIN", "IN_DOUBT")


def control_contract() -> dict[str, Any]:
    return {
        "milestone": MILESTONE,
        "status": STATUS,
        "frozen": FROZEN,
        "commands": [dict(row) for row in COMMANDS],
        "events": list(EVENTS),
        "APPLY_VOCABULARY": list(APPLY_VOCABULARY),
        "APPLY_RESULTS": list(APPLY_RESULTS),
        "NO FRONTEND": True,
        "NO HIDDEN FALLBACK": True,
    }
