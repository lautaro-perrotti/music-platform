from __future__ import annotations

from enum import StrEnum

from copilot.daw.adapter import DawError


class WriteClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    IDEMPOTENT_WRITE = "IDEMPOTENT_WRITE"
    NON_IDEMPOTENT_WRITE = "NON_IDEMPOTENT_WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"


class Permission(StrEnum):
    READ = "READ"
    SAFE_WRITE = "SAFE_WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    EXTERNAL = "EXTERNAL"


class ReconcileResult(StrEnum):
    SATISFIED = "SATISFIED"
    ABSENT = "ABSENT"
    AMBIGUOUS = "AMBIGUOUS"


class WriteInDoubt(DawError):
    """Command was sent; remote outcome is unknown. Never treat as FAILED."""

    def __init__(self, operation: str, command_id: str = "") -> None:
        super().__init__(f"IN_DOUBT: {operation} command_id={command_id}")
        self.operation = operation
        self.command_id = command_id


TOOL_SPECS: dict[str, tuple[WriteClass, Permission]] = {
    "get_session_snapshot": (WriteClass.READ_ONLY, Permission.READ),
    "get_clip_notes": (WriteClass.READ_ONLY, Permission.READ),
    "create_midi_track": (WriteClass.NON_IDEMPOTENT_WRITE, Permission.SAFE_WRITE),
    "create_midi_clip": (WriteClass.NON_IDEMPOTENT_WRITE, Permission.SAFE_WRITE),
    "set_clip_name": (WriteClass.IDEMPOTENT_WRITE, Permission.SAFE_WRITE),
    "set_track_name": (WriteClass.IDEMPOTENT_WRITE, Permission.SAFE_WRITE),
    "replace_clip_notes": (WriteClass.IDEMPOTENT_WRITE, Permission.SAFE_WRITE),
    "set_mixer_volume": (WriteClass.IDEMPOTENT_WRITE, Permission.SAFE_WRITE),
    "set_device_parameter": (WriteClass.IDEMPOTENT_WRITE, Permission.SAFE_WRITE),
    "delete_track": (WriteClass.DESTRUCTIVE, Permission.DESTRUCTIVE),
    "delete_clip": (WriteClass.DESTRUCTIVE, Permission.DESTRUCTIVE),
}


def spec_for(operation: str) -> tuple[WriteClass, Permission]:
    return TOOL_SPECS.get(
        operation, (WriteClass.NON_IDEMPOTENT_WRITE, Permission.SAFE_WRITE)
    )
