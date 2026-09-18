"""Multi-tap capture trust: inventory, slot ownership, stale taps, files, journal."""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.agent.journal import DurableJournal
from copilot.audio.file_hash import sha256_file
from copilot.audio.live_capture import (
    MASTER_INDEX,
    STAGING_BASS,
    STAGING_KICK,
    STAGING_NAME,
    TAP_NAME,
    TAP_UDP_PORT,
    AudioCaptureError,
    _exclusive_open_ok,
    _send_tap_udp,
    capture_dir,
    set_tap_enabled,
    set_tap_recording,
    staging_path,
    wav_lock_owners,
    wav_shared_read_ok,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError

from copilot.audio.capture_scalability_v2 import slot_staging_map

SLOT_STAGING = slot_staging_map()
UDP_REMOVED_PROTOCOL = 3
JOURNAL_DIR = Path("logs") / "capture_journal"

PREPARED = "PREPARED"
RECORDING = "RECORDING"
FINALIZING = "FINALIZING"
VERIFIED = "VERIFIED"
FAILED = "FAILED"
IN_DOUBT = "IN_DOUBT"
RECOVERED = "RECOVERED"
TERMINAL_JOURNAL_STATUSES = frozenset({VERIFIED, FAILED, IN_DOUBT, RECOVERED})


def tap_instance_id(track_index: int, device_index: int) -> str:
    return f"tap:{int(track_index)}:{int(device_index)}"


def _param_map(params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in params.get("parameters") or []:
        name = str(item.get("name") or "")
        if name:
            out[name.lower()] = dict(item)
    return out


def _taps_from_info(
    daw: AbletonTcpAdapter,
    track_index: int,
    track_name: str | None,
    track_type: str,
    info: dict[str, Any],
    *,
    use_cached_taps: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for device in info.get("devices") or []:
        name = str(device.get("name") or "")
        if TAP_NAME.lower() not in name.lower():
            continue
        device_index = int(device["index"])
        accessible = True
        values: dict[str, Any] = {}
        cached_tap = None
        if use_cached_taps:
            for tap in info.get("taps") or []:
                if int(tap.get("device_index", -1)) == device_index:
                    cached_tap = tap
                    break
        try:
            raw = cached_tap or daw.get_device_parameters(track_index, device_index)
            values = _param_map(raw)
        except DawError as exc:
            accessible = False
            values = {"error": str(exc)}
        rec = values.get("rec") or {}
        slot = values.get("slot") or {}
        proto = values.get("tapprotocol") or {}
        on = values.get("device on") or values.get("on") or {}
        rows.append(
            {
                "tap_instance_id": tap_instance_id(track_index, device_index),
                "track_index": track_index,
                "track_name": track_name,
                "track_type": track_type,
                "device_index": device_index,
                "device_name": name,
                "device_on": None if not on else float(on.get("value") or 0.0),
                "device_on_param_index": None if not on else on.get("index"),
                "rec": None if not rec else float(rec.get("value") or 0.0),
                "rec_param_index": None if not rec else rec.get("index"),
                "slot": None
                if not slot
                else int(round(float(slot.get("value") or 0.0))),
                "slot_param_index": None if not slot else slot.get("index"),
                "tap_protocol": None
                if not proto
                else int(round(float(proto.get("value") or 0.0))),
                "runtime_accessible": accessible,
            }
        )
    return rows


def inventory_taps(
    daw: AbletonTcpAdapter,
    *,
    track_infos: dict[int, dict[str, Any]] | None = None,
    master_info: dict[str, Any] | None = None,
    use_cached_taps: bool = True,
) -> list[dict[str, Any]]:
    """Every Copilot Audio Tap in the set, including leftovers."""
    if track_infos is None:
        if not daw.last_track_infos:
            daw.snapshot(include_notes=False)
        track_infos = dict(daw.last_track_infos)
    if master_info is None:
        master_info = daw.last_master_info or daw.get_master_info()
        daw.last_master_info = master_info
    rows = _taps_from_info(
        daw,
        MASTER_INDEX,
        "MASTER",
        "master",
        master_info,
        use_cached_taps=use_cached_taps,
    )
    for index in sorted(track_infos):
        info = track_infos[index]
        midi = bool(info.get("is_midi_track")) and not bool(info.get("is_audio_track"))
        rows.extend(
            _taps_from_info(
                daw,
                int(index),
                str(info.get("name") or ""),
                "midi" if midi else "audio",
                info,
                use_cached_taps=use_cached_taps,
            )
        )
    return rows


def slot_ownership_map(inventory: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in inventory:
        slot = row.get("slot")
        if slot is None:
            continue
        grouped[int(slot)].append(row)
    return dict(grouped)


def duplicate_slots(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collisions: list[dict[str, Any]] = []
    for slot, holders in slot_ownership_map(inventory).items():
        if len(holders) < 2:
            continue
        collisions.append(
            {
                "slot": slot,
                "holders": [
                    {
                        "tap_instance_id": item["tap_instance_id"],
                        "track_index": item["track_index"],
                        "track_name": item["track_name"],
                        "device_index": item["device_index"],
                        "device_on": item.get("device_on"),
                        "rec": item.get("rec"),
                        "tap_protocol": item.get("tap_protocol"),
                    }
                    for item in holders
                ],
            }
        )
    return collisions


def assert_unique_slots(inventory: list[dict[str, Any]]) -> None:
    found = duplicate_slots(inventory)
    if not found:
        return
    raise AudioCaptureError(
        "TAP_SLOT_COLLISION",
        "duplicate Slot among loaded taps: " + str(found),
    )


def reserve_capture_dests(
    recorders: list[dict[str, Any]],
    *,
    pass_id: str,
    root: Path | None = None,
) -> dict[str, Path]:
    root = root or capture_dir()
    stagings = [str(rec["staging"]) for rec in recorders]
    if len(set(stagings)) != len(stagings):
        raise AudioCaptureError(
            "CAPTURE_PATH_COLLISION",
            f"two taps share a staging file: {stagings}",
        )
    slots = [rec.get("slot") for rec in recorders if rec.get("slot") is not None]
    if len(slots) != len(set(slots)):
        raise AudioCaptureError(
            "CAPTURE_PATH_COLLISION",
            f"two taps share a Slot: {slots}",
        )
    reserved: dict[str, Path] = {}
    dests: list[Path] = []
    for rec in recorders:
        slot = rec.get("slot")
        dest = root / (
            f"capture_{pass_id}_slot{slot}.wav"
            if slot is not None
            else f"capture_{pass_id}_{rec['key']}.wav"
        )
        dests.append(dest)
        if dest.exists() or dest.with_name(dest.stem + "_raw.wav").exists():
            raise AudioCaptureError("CAPTURE_PATH_COLLISION", str(dest))
        reserved[str(rec["key"])] = dest
    if len({str(path) for path in dests}) != len(dests):
        raise AudioCaptureError("CAPTURE_PATH_COLLISION", "two recorders share dest")
    return reserved


def interpret_send_level(row: dict[str, Any]) -> dict[str, Any]:
    """Live mixer send.value is 0..1. 0.0 is fader-down (-inf), not 0 dB."""
    if row.get("error"):
        return {
            "readable": False,
            "semantic_silence": False,
            "reason": str(row.get("error")),
        }
    try:
        level = float(row.get("level"))
    except (TypeError, ValueError):
        return {
            "readable": False,
            "semantic_silence": False,
            "reason": "missing level",
        }
    min_v = row.get("min")
    max_v = row.get("max")
    try:
        min_f = float(min_v) if min_v is not None else None
        max_f = float(max_v) if max_v is not None else None
    except (TypeError, ValueError):
        min_f, max_f = None, None
    at_min = min_f is not None and abs(level - min_f) <= 1e-5
    live_mixer_01 = min_f == 0.0 and max_f == 1.0
    # Track volume 0 dB is ~0.85 on this same 0..1 scale. 0.0 is silence.
    semantic_silence = bool(at_min and live_mixer_01)
    return {
        "readable": True,
        "level": level,
        "min": min_f,
        "max": max_f,
        "name": row.get("name"),
        "at_minimum": at_min,
        "live_mixer_0_1": live_mixer_01,
        "semantic_silence": semantic_silence,
        "zero_is_not_0dB": True,
        "basis": (
            "Ableton mixer DeviceParameter value 0.0..1.0; "
            "set_send_level clamps to that range; 0.0 is minimum (-inf), "
            "not unity. Unity on this scale is near 0.85."
        ),
    }


def routing_claim(
    *,
    input_type: str | None,
    input_channel: str | None,
    output: str | None,
    monitoring: str | None,
    through_main: bool,
    sends: list[dict[str, Any]],
    target_name: str,
) -> dict[str, Any]:
    from_target = target_name.lower() in str(input_type or "").lower()
    post = "post mixer" in str(input_channel or "").lower()
    monitor_in = str(monitoring or "").lower() in {"in", "in/in"}
    off_main = str(output or "").lower() in {"no output", "sends only"}
    interpreted = [interpret_send_level(row) for row in sends]
    all_silent = bool(interpreted) and all(
        item.get("semantic_silence") for item in interpreted
    )
    all_readable = all(item.get("readable") for item in interpreted)
    off_direct = from_target and post and monitor_in and off_main and not through_main
    mix_graph = off_direct and all_silent and all_readable
    return {
        "from_target": from_target,
        "post_mixer": post,
        "monitor_in": monitor_in,
        "direct_main_disabled": off_main,
        "through_main": through_main,
        "sends": interpreted,
        "all_sends_semantic_silence": all_silent,
        "claim": (
            "OFF_MIX_GRAPH"
            if mix_graph
            else "OFF_DIRECT_MAIN"
            if off_direct
            else "FAILED"
        ),
    }


def reconcile_stale_taps(
    daw: AbletonTcpAdapter,
    inventory: list[dict[str, Any]],
    recorder_ids: set[str],
) -> dict[str, Any]:
    """Rec=0 + Device Off leftovers. Never delete. Fail closed if Rec stays armed."""
    excluded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for row in inventory:
        tap_id = str(row["tap_instance_id"])
        if tap_id in recorder_ids:
            continue
        track_index = int(row["track_index"])
        try:
            set_tap_recording(daw, False, track_index, broadcast_udp=False)
            set_tap_enabled(daw, track_index, False)
        except (DawError, AudioCaptureError) as exc:
            failed.append({**row, "error": str(exc)})
            continue
        params = daw.get_device_parameters(track_index, int(row["device_index"]))
        values = _param_map(params)
        rec_now = float((values.get("rec") or {}).get("value") or 0.0)
        on_now = float((values.get("device on") or values.get("on") or {}).get("value") or 0.0)
        slot = row.get("slot")
        handle = None
        shared = None
        owners: list[dict[str, object]] = []
        staging = SLOT_STAGING.get(int(slot)) if slot is not None else None
        if staging:
            staging_file = staging_path(staging)
            handle = _exclusive_open_ok(staging_file)
            shared = wav_shared_read_ok(staging_file)
            owners = wav_lock_owners(staging_file)
        state = {
            **row,
            "rec_after": rec_now,
            "device_on_after": on_now,
            "handle": handle,
            "shared_read": shared,
            "handle_owners": owners,
        }
        if rec_now >= 0.5:
            failed.append(state)
            continue
        # Rec=0 is idle. Max sfrecord~ commonly keeps the default staging WAV
        # open (winerror=32) after Device Off. That is not TAP_STALE_ARMED.
        # Exclusive-open is a finalization probe for the ACTIVE recorder only.
        if handle is not None and handle.get("exists") and not handle.get("exclusive"):
            state["idle_handle_held"] = True
        excluded.append(state)
    if failed:
        raise AudioCaptureError(
            "TAP_STALE_ARMED",
            "residual tap could not be reconciled: " + str(failed),
        )
    return {"excluded": excluded, "failed": failed, "ok": True}


def udp_control_audit() -> dict[str, Any]:
    return {
        "udp_port": TAP_UDP_PORT,
        "udp_scope": "global — every loaded Copilot Audio Tap has udpreceive 19877",
        "per_instance": [
            "Device On via LOM set_device_parameter",
            "Rec via LOM set_device_parameter",
            "Slot via LOM set_device_parameter",
        ],
        "selection": "Python addresses (track_index, device_index); Slot only selects sfrecord~ path",
        "slot_x_order_cannot_target_one_udp_listener": True,
        "same_slot_two_devices": "both open the same staging WAV; TAP_SLOT_COLLISION",
        "residual_device": "receives the same UDP Rec as everyone; must Rec=0 via LOM and Device Off",
        "python_broadcast_udp_default": True,
        "capture_pass_must_use_broadcast_udp_false": True,
        "protocol_fix": (
            f"TapProtocol {UDP_REMOVED_PROTOCOL} removes Rec from UDP. "
            "Loaded instances stay on whatever they were dropped with."
        ),
    }


class CaptureJournal:
    def __init__(self, pass_id: str, directory: Path | None = None) -> None:
        root = directory or JOURNAL_DIR
        self.pass_id = pass_id
        self.path = root / f"{pass_id}.jsonl"
        self.journal = DurableJournal(self.path)
        self.status = PREPARED

    def record(self, status: str, **body: Any) -> dict[str, Any]:
        self.status = status
        return self.journal.append(
            {"pass_id": self.pass_id, "status": status, **body}
        )

    def last_status(self) -> str | None:
        rows = self.journal.read_all()
        if not rows:
            return None
        return str(rows[-1].get("status") or "")

    def claim_verified(self) -> str:
        last = self.last_status()
        if last == VERIFIED:
            return VERIFIED
        if last == FAILED:
            return FAILED
        if last == RECOVERED:
            return RECOVERED
        return IN_DOUBT


def incomplete_assets_are_invalid(status: str) -> bool:
    return status not in {VERIFIED}


def new_pass_id() -> str:
    return uuid4().hex[:12]


def _read_rec(daw: AbletonTcpAdapter, track_index: int, device_index: int) -> float | None:
    try:
        raw = daw.get_device_parameters(track_index, device_index)
    except DawError:
        return None
    rec = _param_map(raw).get("rec") or {}
    if rec.get("value") is None:
        return None
    return float(rec["value"])


def lom_crosstalk_test(
    daw: AbletonTcpAdapter, taps: list[dict[str, Any]]
) -> dict[str, Any]:
    """Rec=1 on one LOM instance must not arm the others."""
    if len(taps) < 2:
        return {"ok": False, "reason": "need at least two taps"}
    for tap in taps:
        set_tap_recording(daw, False, int(tap["track_index"]), broadcast_udp=False)
    time.sleep(0.15)
    mutations: list[dict[str, Any]] = []
    ok = True
    for armed in taps:
        set_tap_recording(daw, True, int(armed["track_index"]), broadcast_udp=False)
        time.sleep(0.12)
        row = {"armed": armed["tap_instance_id"], "rec": {}}
        for tap in taps:
            rec = _read_rec(daw, int(tap["track_index"]), int(tap["device_index"]))
            row["rec"][tap["tap_instance_id"]] = rec
            if tap["tap_instance_id"] == armed["tap_instance_id"]:
                if rec is None or rec < 0.5:
                    ok = False
            elif rec is not None and rec >= 0.5:
                ok = False
        set_tap_recording(daw, False, int(armed["track_index"]), broadcast_udp=False)
        time.sleep(0.08)
        mutations.append(row)
    return {"ok": ok, "channel": "LOM Rec", "mutations": mutations}


def udp_crosstalk_test(
    daw: AbletonTcpAdapter, taps: list[dict[str, Any]]
) -> dict[str, Any]:
    """UDP can drive sfrecord~ without flipping the Rec parameter. Watch files."""
    for tap in taps:
        set_tap_recording(daw, False, int(tap["track_index"]), broadcast_udp=False)
    time.sleep(0.2)
    before: dict[str, int] = {}
    after: dict[str, int] = {}
    for tap in taps:
        slot = tap.get("slot")
        if slot is None:
            continue
        staging = SLOT_STAGING.get(int(slot))
        if not staging:
            continue
        path = staging_path(staging)
        before[str(slot)] = path.stat().st_size if path.exists() else 0
    _send_tap_udp(1)
    time.sleep(0.7)
    _send_tap_udp(0)
    time.sleep(0.35)
    recs = {
        tap["tap_instance_id"]: _read_rec(
            daw, int(tap["track_index"]), int(tap["device_index"])
        )
        for tap in taps
    }
    for tap in taps:
        set_tap_recording(daw, False, int(tap["track_index"]), broadcast_udp=False)
        slot = tap.get("slot")
        if slot is None:
            continue
        staging = SLOT_STAGING.get(int(slot))
        if not staging:
            continue
        path = staging_path(staging)
        after[str(slot)] = path.stat().st_size if path.exists() else 0
    grown = [slot for slot, size in after.items() if size > before.get(slot, 0) + 64]
    return {
        "ok": len(grown) == 0,
        "channel": "UDP 19877",
        "rec_after_udp": recs,
        "sizes_before": before,
        "sizes_after": after,
        "grown_slots": grown,
        "global_file_crosstalk": len(grown) > 1,
        "udp_inert": len(grown) == 0,
        "rec_parameter_hides_udp": all((rec or 0) < 0.5 for rec in recs.values())
        and len(grown) > 0,
        "ok": len(grown) == 0,
    }
