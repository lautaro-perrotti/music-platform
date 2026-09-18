"""CAPTURE_HOST_BASELINE_V1 — canonical parked state for Copilot capture hosts.

A newly created Live audio track is initialization input, not product state.
A host is pool-ready only after this parked state is applied and freshly verified.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

MILESTONE = "CAPTURE_HOST_BASELINE_V1"
STATUS = "VERIFIED"

LIFECYCLE_CREATED = "HOST_CREATED"
LIFECYCLE_NORMALIZING = "HOST_NORMALIZING"
LIFECYCLE_PARKED_VERIFIED = "HOST_PARKED_VERIFIED"
LIFECYCLE_AVAILABLE = "HOST_AVAILABLE"
LIFECYCLE_PROVISION_FAILED = "HOST_PROVISION_FAILED"
LIFECYCLE_NOT_READY = "HOST_NOT_READY"

PARKED_OUTPUT = "Sends Only"
PARKED_OUTPUT_ALTERNATES = frozenset({"sends only", "no output"})
PARKED_INPUT = "Resampling"
PARKED_INPUT_CHANNEL = ""
PARKED_MONITORING = "off"
FORBIDDEN_INPUTS = frozenset({"ext. in", "ext in", "external in"})
FORBIDDEN_OUTPUTS = frozenset({"main", "master"})


@dataclass(frozen=True)
class CaptureHostParkedState:
    """Idle Copilot capture-host contract. Not Live's new-track defaults."""

    output_routing_type: str = PARKED_OUTPUT
    output_routing_channel: str = ""
    input_routing_type: str = PARKED_INPUT
    input_routing_channel: str = PARKED_INPUT_CHANNEL
    monitoring: str = PARKED_MONITORING
    rec: float = 0.0
    device_on: float = 1.0
    sends_silent: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def restore_baseline(self) -> dict[str, Any]:
        return {
            "input": {
                "input_routing_type": self.input_routing_type,
                "input_routing_channel": self.input_routing_channel,
            },
            "output": {
                "output_routing_type": self.output_routing_type,
                "output_routing_channel": self.output_routing_channel,
            },
            "monitoring": {"monitoring": self.monitoring, "value": self.monitoring},
            "sends": [],
        }


CANONICAL_PARKED = CaptureHostParkedState()


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def live_default_is_invalid(state: dict[str, Any]) -> bool:
    incoming = state.get("input") or {}
    outgoing = state.get("output") or {}
    return _norm(incoming.get("input_routing_type")) in FORBIDDEN_INPUTS or _norm(
        outgoing.get("output_routing_type")
    ) in FORBIDDEN_OUTPUTS


def _tap_fields(state: dict[str, Any]) -> dict[str, Any]:
    taps = list(state.get("taps") or [])
    tap = taps[0] if taps else {}
    params = {
        str(item.get("name") or "").lower(): item
        for item in list(tap.get("parameters") or [])
    }
    rec = params.get("rec") or {}
    device_on = params.get("device on") or params.get("on") or {}
    slot = params.get("slot") or {}
    return {
        "has_tap": bool(taps) or bool(state.get("devices")),
        "rec": rec.get("value"),
        "device_on": device_on.get("value"),
        "slot": slot.get("value"),
    }


def _sends_silent(state: dict[str, Any]) -> bool:
    sends = list(state.get("sends") or [])
    if not sends:
        return True
    for row in sends:
        level = row.get("value")
        if level is None:
            level = row.get("level")
        try:
            if abs(float(level or 0.0)) > 1.0e-4:
                return False
        except (TypeError, ValueError):
            return False
    return True


def evaluate_parked(
    state: dict[str, Any],
    *,
    expected_slot: int | None = None,
    parked: CaptureHostParkedState = CANONICAL_PARKED,
) -> dict[str, Any]:
    """Compare a READ_CAPTURE_HOST_STATE snapshot to the canonical idle contract."""
    incoming = state.get("input") or {}
    outgoing = state.get("output") or {}
    monitoring = state.get("monitoring") or {}
    tap = _tap_fields(state)
    mismatches: list[str] = []
    if _norm(outgoing.get("output_routing_type")) not in PARKED_OUTPUT_ALTERNATES:
        mismatches.append("output")
    if _norm(outgoing.get("output_routing_channel")):
        mismatches.append("output_channel")
    if _norm(incoming.get("input_routing_type")) != _norm(parked.input_routing_type):
        mismatches.append("input")
    if _norm(incoming.get("input_routing_type")) in FORBIDDEN_INPUTS:
        mismatches.append("forbidden_input")
    if _norm(outgoing.get("output_routing_type")) in FORBIDDEN_OUTPUTS:
        mismatches.append("forbidden_output")
    if _norm(monitoring.get("monitoring") or monitoring.get("value")) != _norm(parked.monitoring):
        mismatches.append("monitoring")
    if not _sends_silent(state):
        mismatches.append("sends")
    if tap["rec"] is not None and abs(float(tap["rec"]) - parked.rec) > 0.01:
        mismatches.append("rec")
    if tap["device_on"] is not None and abs(float(tap["device_on"]) - parked.device_on) > 0.01:
        mismatches.append("device_on")
    if expected_slot is not None and tap["slot"] is not None:
        if int(round(float(tap["slot"]))) != int(expected_slot):
            mismatches.append("slot")
    if live_default_is_invalid(state):
        mismatches.append("live_default")
    ok = not mismatches
    return {
        "milestone": MILESTONE,
        "ok": ok,
        "lifecycle": LIFECYCLE_AVAILABLE if ok else LIFECYCLE_NOT_READY,
        "mismatches": mismatches,
        "live_default": live_default_is_invalid(state),
    }


def parked_restore_baseline(
    parked: CaptureHostParkedState = CANONICAL_PARKED,
) -> dict[str, Any]:
    return parked.restore_baseline()


def apply_parked_state(
    daw: Any,
    track_index: int,
    *,
    expected_slot: int | None = None,
    parked: CaptureHostParkedState = CANONICAL_PARKED,
) -> dict[str, Any]:
    """Initialize a Copilot host to the canonical parked state. Not a rollback."""
    from copilot.audio.live_capture import (
        find_tap,
        set_tap_enabled,
        set_tap_recording,
        set_tap_slot,
    )
    from copilot.audio.views import _pick_output
    from copilot.daw.adapter import DawError

    mutations: list[str] = []
    index = int(track_index)
    outs = list(daw.get_available_outputs(index).get("available_outputs") or [])
    off = _pick_output(outs, parked.output_routing_type, "No Output")
    if not off:
        return {
            "status": LIFECYCLE_PROVISION_FAILED,
            "reason": "NO_OFF_MAIN_OUTPUT",
            "mutations": mutations,
        }
    daw.set_track_output_routing(index, off, parked.output_routing_channel)
    mutations.append(f"output:{off}")
    ins = list(daw.get_available_inputs(index).get("available_inputs") or [])
    names = [str(item) for item in ins]
    parked_in = _pick_output(names, parked.input_routing_type)
    if not parked_in:
        return {
            "status": LIFECYCLE_PROVISION_FAILED,
            "reason": "NO_RESAMPLING_INPUT",
            "mutations": mutations,
        }
    daw.set_track_input_routing(index, parked_in, parked.input_routing_channel)
    mutations.append(f"input:{parked_in}")
    try:
        daw.set_track_monitoring(index, parked.monitoring)
        mutations.append(f"monitor:{parked.monitoring}")
    except DawError as exc:
        return {
            "status": LIFECYCLE_PROVISION_FAILED,
            "reason": f"MONITOR:{exc}",
            "mutations": mutations,
        }
    try:
        sends = list(daw.get_track_sends(index) or [])
        for row in sends:
            daw.set_send_level(index, int(row.get("send_index", 0)), 0.0)
        if sends:
            mutations.append("sends:0")
    except DawError:
        pass
    try:
        set_tap_recording(daw, False, index, broadcast_udp=False)
        set_tap_enabled(daw, index, True)
        if expected_slot is not None:
            set_tap_slot(daw, index, int(expected_slot))
            mutations.append(f"slot:{expected_slot}")
    except DawError as exc:
        if find_tap(daw, index) is None:
            return {
                "status": LIFECYCLE_PROVISION_FAILED,
                "reason": f"TAP:{exc}",
                "mutations": mutations,
            }
        return {
            "status": LIFECYCLE_PROVISION_FAILED,
            "reason": f"TAP_PARAM:{exc}",
            "mutations": mutations,
        }
    return {"status": LIFECYCLE_NORMALIZING, "mutations": mutations}


def normalize_and_verify_host(
    daw: Any,
    track_index: int,
    *,
    expected_slot: int | None = None,
    parked: CaptureHostParkedState = CANONICAL_PARKED,
) -> dict[str, Any]:
    from copilot.runtime.host_state import read_capture_host_state

    applied = apply_parked_state(
        daw, track_index, expected_slot=expected_slot, parked=parked
    )
    if applied.get("status") == LIFECYCLE_PROVISION_FAILED:
        return {**applied, "verified": False, "lifecycle": LIFECYCLE_PROVISION_FAILED}
    state = read_capture_host_state(daw, int(track_index), fresh=True)
    verdict = evaluate_parked(state, expected_slot=expected_slot, parked=parked)
    lifecycle = LIFECYCLE_AVAILABLE if verdict["ok"] else LIFECYCLE_PROVISION_FAILED
    return {
        "milestone": MILESTONE,
        "status": LIFECYCLE_PARKED_VERIFIED if verdict["ok"] else LIFECYCLE_PROVISION_FAILED,
        "lifecycle": lifecycle,
        "verified": bool(verdict["ok"]),
        "verdict": verdict,
        "applied": applied,
        "state": state,
    }


def record_host_repair(
    host_name: str,
    payload: dict[str, Any],
    *,
    directory: Any | None = None,
) -> Any:
    """Append-only repair journal. Never rewrites a historical capture journal."""
    from pathlib import Path
    from uuid import uuid4

    from copilot.agent.journal import DurableJournal
    from copilot.human_eval.store import now_iso

    root = Path(directory) if directory is not None else Path("logs") / "host_baseline_journal"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{str(host_name).replace(' ', '_')}_{uuid4().hex[:8]}.jsonl"
    DurableJournal(path).append(
        {
            "event": "HOST_RECOVERY",
            "milestone": MILESTONE,
            "host": host_name,
            "ts": now_iso(),
            **payload,
        }
    )
    return path
