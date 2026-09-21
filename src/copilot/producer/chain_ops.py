from __future__ import annotations

from typing import Any

from copilot.daw.adapter import DawError
from copilot.schemas.session import SessionState, TrackState


def _norm(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _find_track(session: SessionState, track_name: str) -> TrackState:
    track = session.track_by_name(track_name)
    if track is None:
        raise DawError(f"Track not found or ambiguous: {track_name!r}")
    return track


def _find_device_index(track: TrackState, device_name: str) -> int | None:
    want = _norm(device_name)
    # exact first
    for d in track.devices:
        if _norm(d.name) == want:
            return int(d.index)
    # substring fallback
    for d in track.devices:
        if want in _norm(d.name):
            return int(d.index)
    return None


def ensure_order(
    daw,
    *,
    session: SessionState,
    track_name: str,
    desired_order: list[str],
) -> dict[str, Any]:
    """Reorder devices to match desired order as a prefix.

    Uses bridge move operations. Keeps non-mentioned devices after the prefix.
    """
    track = _find_track(session, track_name)
    moves: list[dict[str, Any]] = []
    errors: list[str] = []

    for target_idx, device_name in enumerate(desired_order):
        fresh = daw.snapshot()
        t = _find_track(fresh, track_name)
        cur_idx = _find_device_index(t, device_name)
        if cur_idx is None:
            continue
        if cur_idx == target_idx:
            continue
        try:
            daw.bridge_command(
                "move_device",
                {
                    "track_index": int(t.index),
                    "device_index": int(cur_idx),
                    "new_index": int(target_idx),
                },
                side_effect=True,
            )
            moves.append({"device": device_name, "from": cur_idx, "to": target_idx})
        except Exception as exc:  # noqa: BLE001
            # fallback: move right until target when cur < target
            if cur_idx < target_idx:
                idx = cur_idx
                while idx < target_idx:
                    daw.bridge_command(
                        "move_device_right",
                        {"track_index": int(t.index), "device_index": int(idx)},
                        side_effect=True,
                    )
                    idx += 1
                moves.append({"device": device_name, "from": cur_idx, "to": target_idx, "via": "right"})
            else:
                errors.append(f"move {device_name}: {exc}")

    after = daw.snapshot()
    t_after = _find_track(after, track_name)
    names = [_norm(d.name) for d in t_after.devices]
    prefix = [_norm(x) for x in desired_order if x]

    ok = len(prefix) <= len(names) and names[: len(prefix)] == prefix
    return {
        "ok": ok,
        "track": track_name,
        "moves": moves,
        "errors": errors,
        "final_order": [d.name for d in t_after.devices],
    }


def ensure_chain(
    daw,
    *,
    session: SessionState,
    track_name: str,
    devices: list[str],
) -> dict[str, Any]:
    """Ensure a track has the requested devices in order (as prefix)."""
    track = _find_track(session, track_name)
    loaded: list[str] = []
    already_present: list[str] = []
    errors: list[str] = []

    for device_name in devices:
        fresh = daw.snapshot()
        t = _find_track(fresh, track_name)
        if _find_device_index(t, device_name) is not None:
            already_present.append(device_name)
            continue
        try:
            daw.load_instrument_or_effect(int(track.index), device_name)
            loaded.append(device_name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"load {device_name}: {exc}")

    order = ensure_order(daw, session=daw.snapshot(), track_name=track_name, desired_order=devices)
    errors.extend(order.get("errors", []))
    return {
        "ok": bool(order.get("ok", False)) and not errors,
        "track": track_name,
        "loaded": loaded,
        "already_present": already_present,
        "errors": errors,
        "final_order": order.get("final_order", []),
    }


def ensure_sidechain(
    daw,
    *,
    session: SessionState,
    track_name: str,
    device_name: str,
    source_track: str,
    source_channel: str = "Post FX",
) -> dict[str, Any]:
    """Set sidechain routing on a device and verify from command readback."""
    track = _find_track(session, track_name)
    device_idx = _find_device_index(track, device_name)
    if device_idx is None:
        raise DawError(f"Device {device_name!r} not found on track {track_name!r}")

    result = daw.set_device_input_routing(
        int(track.index), int(device_idx), source_track, source_channel
    )

    # bridge returns verification hints (type_matched/channel_matched)
    type_matched = bool(result.get("type_matched", True))
    channel_matched = result.get("channel_matched", True)
    ok = type_matched and (True if channel_matched is None else bool(channel_matched))

    return {
        "ok": ok,
        "track": track_name,
        "device": device_name,
        "device_index": int(device_idx),
        "source_track": source_track,
        "source_channel": source_channel,
        "result": result,
    }
