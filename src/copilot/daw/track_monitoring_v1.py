"""Capability-aware Live monitoring. Main/Return are not snapshot failures."""

from __future__ import annotations

NOT_APPLICABLE = "NOT_APPLICABLE"
SUPPORTED = "SUPPORTED"
MONITORING_STATES = {0: "in", 1: "auto", 2: "off"}


def monitoring_capability(
    *,
    can_be_armed: bool,
    is_main: bool = False,
    is_return: bool = False,
) -> str:
    if is_main or is_return or not can_be_armed:
        return NOT_APPLICABLE
    return SUPPORTED


def read_monitoring(
    *,
    can_be_armed: bool,
    is_main: bool = False,
    is_return: bool = False,
    raw_state: int | None = None,
) -> str:
    """Read monitoring only when the object supports it.

    Does not catch Live getter failures on supported tracks.
    """
    if (
        monitoring_capability(
            can_be_armed=can_be_armed,
            is_main=is_main,
            is_return=is_return,
        )
        == NOT_APPLICABLE
    ):
        return NOT_APPLICABLE
    if raw_state is None:
        raise ValueError("supported track missing monitoring state")
    return MONITORING_STATES.get(int(raw_state), "unknown")
