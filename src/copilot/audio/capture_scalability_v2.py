"""CAPTURE_SCALABILITY_V2 — scalable capture-pool capacity.

Physical V3 ceiling is the Max slot selector ``sel 0 1 2`` plus two provisioned
hosts. This module discovers capacity from the running taps and plans passes.
It does not hardcode 4 as a permanent maximum.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from copilot.audio.live_capture import STAGING_BASS, STAGING_KICK, STAGING_NAME

CAPTURE_HOST = "Copilot Capture"
CAPTURE_BASS = "Copilot Capture Bass"

MILESTONE = "CAPTURE_SCALABILITY_V2"
STATUS = "VERIFIED"
LEGACY_TAP_PROTOCOL = 3
SCALABLE_TAP_PROTOCOL = 4
MAIN_SLOT = 0
LEGACY_SOURCE_CAPACITY = 2
PRACTICAL_MAX_SOURCE_SLOTS = 8
LEGACY_HOST_BY_SLOT = {1: CAPTURE_HOST, 2: CAPTURE_BASS}
_NUMBERED_HOST = re.compile(r"^Copilot Capture (\d+)$")


@dataclass(frozen=True)
class CaptureCapacity:
    max_concurrent_sources: int
    available_hosts: int
    occupied_hosts: int
    main_sidecar_supported: bool
    protocol_version: int
    max_slot: int
    source: str = "inventory"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def host_name_for_slot(slot: int) -> str:
    if int(slot) in LEGACY_HOST_BY_SLOT:
        return LEGACY_HOST_BY_SLOT[int(slot)]
    return f"Copilot Capture {int(slot)}"


def slot_for_host_name(name: str) -> int | None:
    if name == CAPTURE_HOST:
        return 1
    if name == CAPTURE_BASS:
        return 2
    match = _NUMBERED_HOST.match(name)
    if match:
        value = int(match.group(1))
        if value >= 3:
            return value
    return None


def is_capture_host_name(name: str) -> bool:
    return slot_for_host_name(name) is not None


def staging_for_slot(slot: int) -> str:
    mapping = {0: STAGING_NAME, 1: STAGING_KICK, 2: STAGING_BASS}
    if int(slot) in mapping:
        return mapping[int(slot)]
    return f"_next_s{int(slot)}.wav"


def slot_staging_map(max_slot: int = PRACTICAL_MAX_SOURCE_SLOTS) -> dict[int, str]:
    return {slot: staging_for_slot(slot) for slot in range(0, max_slot + 1)}


def source_slots_for_protocol(protocol: int) -> int:
    if int(protocol) >= SCALABLE_TAP_PROTOCOL:
        return PRACTICAL_MAX_SOURCE_SLOTS
    return LEGACY_SOURCE_CAPACITY


def discover_capacity(
    *,
    session: Any | None = None,
    inventory: list[dict[str, Any]] | None = None,
    advertised_protocol: int | None = None,
    ready_hosts: list[str] | None = None,
) -> CaptureCapacity:
    """Runtime authority: live tap inventory / advertised protocol. Not disk.

    ``ready_hosts`` is the HOST_AVAILABLE set. Track existence alone is not capacity.
    """
    protocols = [
        int(row["tap_protocol"])
        for row in (inventory or [])
        if row.get("tap_protocol") is not None
    ]
    protocol = int(advertised_protocol or 0)
    if protocols:
        protocol = max(protocol, max(protocols))
    if protocol <= 0:
        protocol = LEGACY_TAP_PROTOCOL
    max_slot = MAIN_SLOT + source_slots_for_protocol(protocol)
    host_names = []
    if ready_hosts is not None:
        host_names = [name for name in ready_hosts if is_capture_host_name(name)]
    elif session is not None:
        host_names = [
            track.name
            for track in getattr(session, "tracks", []) or []
            if is_capture_host_name(getattr(track, "name", ""))
        ]
    available = len(host_names)
    main = False
    for row in inventory or []:
        if int(row.get("track_index", 1)) == -1 or int(row.get("slot") or -1) == MAIN_SLOT:
            if str(row.get("track_name") or "").upper() in {"MASTER", "MAIN", ""}:
                main = True
            if int(row.get("track_index", 1)) < 0:
                main = True
    if inventory and any(int(row.get("slot") or -1) == MAIN_SLOT for row in inventory):
        main = True
    concurrent = source_slots_for_protocol(protocol)
    if available:
        concurrent = min(concurrent, available)
    return CaptureCapacity(
        max_concurrent_sources=max(1, concurrent) if available or protocol else LEGACY_SOURCE_CAPACITY,
        available_hosts=available,
        occupied_hosts=0,
        # Main sidecar support is an observed capability, not a protocol
        # default.  Do not claim it when the inventory/session has no Main tap.
        main_sidecar_supported=main,
        protocol_version=protocol,
        max_slot=max_slot,
        source="inventory" if inventory else ("session" if session is not None else "legacy"),
    )


def plan_source_passes(
    targets: list[Any],
    *,
    capacity: CaptureCapacity,
    available_host_count: int | None = None,
) -> list[list[Any]]:
    """Split sources into playback passes from discovered capacity."""
    if not targets:
        return []
    width = int(capacity.max_concurrent_sources or LEGACY_SOURCE_CAPACITY)
    if available_host_count is not None:
        width = min(width, max(1, int(available_host_count)))
    width = max(1, width)
    return [targets[i : i + width] for i in range(0, len(targets), width)]


def host_specs_for_capacity(capacity: CaptureCapacity) -> list[dict[str, Any]]:
    count = min(int(capacity.max_concurrent_sources), PRACTICAL_MAX_SOURCE_SLOTS)
    specs: list[dict[str, Any]] = []
    for slot in range(1, count + 1):
        specs.append(
            {
                "name": host_name_for_slot(slot),
                "slot": slot,
                "staging": staging_for_slot(slot),
                "key": f"s{slot}",
            }
        )
    return specs


def park_capture_host(daw: Any, track_index: int, *, expected_slot: int | None = None) -> dict[str, Any]:
    from copilot.audio.capture_host_baseline_v1 import normalize_and_verify_host

    return normalize_and_verify_host(daw, track_index, expected_slot=expected_slot)


def ensure_capture_pool(
    daw: Any,
    session: Any,
    needed: int,
    *,
    capacity: CaptureCapacity | None = None,
) -> dict[str, Any]:
    """Idempotent Copilot-owned host bank. Creates only what this run needs."""
    from copilot.audio.batch_capture import ensure_named_audio_track, load_tap_on_track
    from copilot.audio.capture_host_baseline_v1 import evaluate_parked
    from copilot.audio.live_capture import find_tap
    from copilot.daw.adapter import DawError
    from copilot.runtime.host_state import read_capture_host_state

    target = min(max(int(needed), 0), PRACTICAL_MAX_SOURCE_SLOTS)
    mutations: list[str] = []
    hosts: list[dict[str, Any]] = []
    current = session
    for slot in range(1, target + 1):
        name = host_name_for_slot(slot)
        track = current.track_by_name(name) if current is not None else None
        if track is None:
            created = ensure_named_audio_track(daw, name, snapshot=True)
            mutations.append(f"create:{name}")
            if created.get("created"):
                current = daw.snapshot(include_notes=False)
            track = current.track_by_name(name)
        if track is None:
            continue
        try:
            tap = find_tap(daw, int(track.index))
        except DawError:
            tap = None
        if tap is None:
            load_tap_on_track(daw, int(track.index))
            mutations.append(f"tap:{name}")
            tap = find_tap(daw, int(track.index))
        try:
            current_state = read_capture_host_state(daw, int(track.index), fresh=True)
            already = evaluate_parked(current_state, expected_slot=slot)
        except Exception:
            already = {"ok": False}
        if already.get("ok"):
            hosts.append(
                {
                    "name": name,
                    "slot": slot,
                    "index": int(track.index),
                    "staging": staging_for_slot(slot),
                    "lifecycle": "HOST_AVAILABLE",
                }
            )
            continue
        parked = park_capture_host(daw, int(track.index), expected_slot=slot)
        if not parked.get("verified"):
            mutations.append(f"HOST_PROVISION_FAILED:{name}")
            continue
        mutations.append(f"park:{name}")
        hosts.append(
            {
                "name": name,
                "slot": slot,
                "index": int(track.index),
                "staging": staging_for_slot(slot),
                "lifecycle": "HOST_AVAILABLE",
            }
        )
    ready_names = [row["name"] for row in hosts]
    return {
        "milestone": MILESTONE,
        "needed": needed,
        "target": target,
        "mutations": mutations,
        "hosts": hosts,
        "ready_hosts": ready_names,
        "NO_CHANGES_REQUIRED": not mutations,
        "session": current,
    }


def capture_identity(
    *,
    operation_id: str,
    source_id: str,
    host_id: str,
    slot: int,
    pass_id: str,
) -> dict[str, Any]:
    return {
        "capture_operation_id": operation_id,
        "source_persistent_identity": source_id,
        "capture_host_identity": host_id,
        "slot": int(slot),
        "recorder_identity": f"{host_id}:slot{int(slot)}",
        "batch_pass_id": pass_id,
        "artifact_name": f"{pass_id}_{host_id}_slot{int(slot)}.wav",
        "shared_transport_pass": True,
    }
