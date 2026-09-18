"""READ_CAPTURE_HOST_STATE — one authoritative host snapshot.

Uses get_tracks_info (already on the wire) as the bulk contract. A named
get_capture_hosts_state command is preferred when the Remote Script has it,
and falls back without changing framing.
"""

from __future__ import annotations

from typing import Any

from copilot.runtime.freshness import FreshnessDomain, MONITORING_VALUE

HOST_STATE_VERSION = "capture-host-state-1"


def host_state_from_info(info: dict[str, Any]) -> dict[str, Any]:
    index = int(info["index"])
    monitoring = str(info.get("monitoring") or "")
    return {
        "version": HOST_STATE_VERSION,
        "index": index,
        "info": info,
        "input": {
            "input_routing_type": info.get("input_routing_type"),
            "input_routing_channel": info.get("input_routing_channel"),
        },
        "output": {
            "output_routing_type": info.get("output_routing_type"),
            "output_routing_channel": info.get("output_routing_channel"),
        },
        "monitoring": {
            "track_index": index,
            "monitoring": monitoring,
            "monitoring_value": MONITORING_VALUE.get(monitoring.lower(), -1),
            "can_monitor": monitoring not in {"", "NOT_APPLICABLE", "n/a"},
        },
        "sends": list(info.get("sends") or []),
        "devices": list(info.get("devices") or []),
        "taps": list(info.get("taps") or []),
        "provenance": {
            "source": info.get("_source") or "track_info",
            "domains": [d.value for d in (
                FreshnessDomain.MONITORING_STATE,
                FreshnessDomain.ROUTING_STATE,
                FreshnessDomain.SEND_STATE,
                FreshnessDomain.DEVICE_PARAMETER_STATE,
                FreshnessDomain.CAPTURE_HOST_STATE,
            )],
        },
    }


def _fetch_hosts(daw: Any, indices: list[int], *, fresh: bool = False) -> dict[str, Any]:
    getter = getattr(daw, "get_capture_hosts_state", None)
    if callable(getter):
        try:
            try:
                payload = getter(indices, fresh=fresh)
            except TypeError:
                payload = getter(indices)
            if isinstance(payload, dict) and isinstance(payload.get("tracks"), list):
                return payload
        except Exception:  # noqa: BLE001 — fall back to existing bulk read
            pass
    try:
        return daw.get_tracks_info(indices, fresh=fresh)
    except TypeError:
        return daw.get_tracks_info(indices)


def read_capture_hosts_state(
    daw: Any,
    indices: list[int],
    *,
    fresh: bool = False,
) -> dict[int, dict[str, Any]]:
    """One protocol request for N hosts when a fetch is required."""
    view = getattr(daw, "_project_read_view", None)
    assembled: dict[int, dict[str, Any]] = {}
    missing: list[int] = []
    for index in indices:
        idx = int(index)
        if not fresh and view is not None:
            cached = view.capture_host_state(idx)
            if cached is not None:
                assembled[idx] = cached
                continue
        missing.append(idx)
    if not missing:
        if view is not None:
            view.bulk_calls += 1
        return assembled
    payload = _fetch_hosts(daw, missing, fresh=fresh)
    for item in payload.get("tracks") or []:
        if "index" not in item:
            continue
        idx = int(item["index"])
        item = dict(item)
        item["_source"] = "get_tracks_info"
        daw.last_track_infos[idx] = item
        state = host_state_from_info(item)
        assembled[idx] = state
        if view is not None:
            view.ingest_track(item)
        clock = getattr(daw, "freshness", None)
        if clock is not None:
            clock.mark_track_bundle_read(idx)
    if view is not None:
        view.bulk_calls += 1
        view.fresh_reads_forced += len(missing) if fresh else 0
    return assembled


def read_capture_host_state(
    daw: Any, host_index: int, *, fresh: bool = False
) -> dict[str, Any]:
    states = read_capture_hosts_state(daw, [int(host_index)], fresh=fresh)
    if int(host_index) not in states:
        raise KeyError(f"capture host state missing for track {host_index}")
    return states[int(host_index)]
