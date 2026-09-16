from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

from copilot.audio.lowend_features import ANALYZER_ID, analyzer_fingerprint
from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    CAPTURE_HOST,
)
from copilot.audio.live_capture import EXPECTED_TAP_PROTOCOL, master_tap_position
from copilot.audio.tap_trust import duplicate_slots, inventory_taps, routing_claim
from copilot.daw.ableton_tcp import AbletonTcpAdapter

LAB_TRACK_MARKERS = (
    "LIVE22 Kick",
    "LIVE3 Kick",
    "LIVE2 Source",
    "AI Test",
)
KICK_PAD = "Kick 808 Deep"
BASS_TARGET = "Sub Sub Bass"
WORKING_COPY_SUFFIX = "pista_copilot_eval.als"
ORIGINAL_SET_SUFFIX = "pista.als"
REAL_SET_CANDIDATE = r"C:\Users\lsper\Desktop\pista Project\pista.als"
WORKING_COPY_CANDIDATE = r"C:\Users\lsper\Desktop\pista Project\pista_copilot_eval.als"
REAL_KICK_NAMES = (KICK_PAD,)
REAL_BASS_NAMES = (BASS_TARGET,)
EXPECTED_SLOTS = {
    "MASTER": 0,
    CAPTURE_HOST: 1,
    CAPTURE_BASS: 2,
}


def classify_kick_source(input_type: str | None, input_channel: str | None) -> str:
    blob = f"{input_type or ''} {input_channel or ''}".lower()
    if KICK_PAD.lower() in blob:
        return "KICK_PAD_ISOLATED"
    if "drums" in blob:
        return "DRUMS_WHOLE"
    if not blob.strip():
        return "UNSET"
    return "OTHER"


def classify_bass_source(input_type: str | None, input_channel: str | None) -> str:
    blob = f"{input_type or ''} {input_channel or ''}".lower()
    if "bassline" in blob:
        return "BASSLINE_CHAIN"
    if BASS_TARGET.lower() in blob and "post mixer" in blob:
        return "TRACK_POST_MIXER"
    if BASS_TARGET.lower() in blob:
        return "TRACK_NOT_POST_MIXER"
    if not blob.strip():
        return "UNSET"
    return "OTHER"


def _path_norm(path: str) -> str:
    return path.replace("/", "\\").lower()


def _tap_rec_is_off(row: dict[str, Any] | None) -> bool:
    if row is None or row.get("rec") is None:
        return False
    return abs(float(row["rec"])) < 0.01


def _with_sends(daw: AbletonTcpAdapter, track_index: int, info: dict[str, Any] | None) -> dict[str, Any] | None:
    if info is None:
        return None
    if info.get("sends"):
        return info
    merged = dict(info)
    try:
        merged["sends"] = daw.get_track_sends(track_index)
    except Exception as exc:
        merged["sends_error"] = str(exc)
    return merged


def _pad_off_mix_graph(info: dict[str, Any]) -> dict[str, Any]:
    """Kick pad isolate is not whole-track Post Mixer; mix-graph still means off Main + silent sends."""
    from copilot.audio.tap_trust import interpret_send_level

    output = str(info.get("output_routing_type") or "")
    monitoring = str(info.get("monitoring") or "")
    through_main = output.lower() in {"main", "master"}
    isolated = (
        classify_kick_source(
            str(info.get("input_routing_type") or ""),
            str(info.get("input_routing_channel") or ""),
        )
        == "KICK_PAD_ISOLATED"
    )
    monitor_in = monitoring.lower() in {"in", "in/in"}
    off_main = output.lower() in {"no output", "sends only"}
    post_mixer = "post mixer" in str(info.get("input_routing_channel") or "").lower()
    interpreted = [interpret_send_level(row) for row in list(info.get("sends") or [])]
    all_silent = bool(interpreted) and all(item.get("semantic_silence") for item in interpreted)
    all_readable = bool(interpreted) and all(item.get("readable") for item in interpreted)
    off_direct = isolated and post_mixer and monitor_in and off_main and not through_main
    mix_graph = off_direct and all_silent and all_readable
    return {
        "from_target": isolated,
        "post_mixer": post_mixer,
        "monitor_in": monitor_in,
        "direct_main_disabled": off_main,
        "through_main": through_main,
        "sends": interpreted,
        "all_sends_semantic_silence": all_silent,
        "source_shape": "DRUM_RACK_PAD",
        "claim": (
            "OFF_MIX_GRAPH"
            if mix_graph
            else "OFF_DIRECT_MAIN"
            if off_direct
            else "FAILED"
        ),
    }


def preflight_session(
    daw: AbletonTcpAdapter,
    *,
    lab_track_exclusions: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    """Read-only. No tracks, devices, routing, mixer, or MIDI writes.

    lab_track_exclusions: optional scoped allow-list of LAB_TRACK_MARKERS names
    that may remain present without failing THIS preflight call. Default empty —
    global certification stays strict. Callers must pass an explicit exclusion;
    never silently ignore lab tracks.
    """
    session = daw.snapshot(include_notes=False)
    names = [track.name for track in session.tracks]
    path = session.project_path or ""
    norm = _path_norm(path)
    working_copy = norm.endswith(WORKING_COPY_SUFFIX)
    original_open = norm.endswith(ORIGINAL_SET_SUFFIX) and not working_copy
    exclusions = frozenset(lab_track_exclusions or ())
    unknown_excl = sorted(exclusions - frozenset(LAB_TRACK_MARKERS))
    lab_hits = [item for item in names if item in LAB_TRACK_MARKERS]
    lab_hits_blocking = [item for item in lab_hits if item not in exclusions]
    lab_hits_excluded = [item for item in lab_hits if item in exclusions]
    drums = session.track_by_name("Drums")
    bass = session.track_by_name(BASS_TARGET)
    inventory = inventory_taps(daw)
    main_pos = master_tap_position(daw)
    collisions = duplicate_slots(inventory)
    tap_by_track = {str(row.get("track_name")): row for row in inventory}
    master_tap = tap_by_track.get("MASTER")
    capture_track = session.track_by_name(CAPTURE_HOST)
    bass_host = session.track_by_name(CAPTURE_BASS)
    capture_info = None if capture_track is None else _with_sends(
        daw, capture_track.index, daw.get_track_info(capture_track.index)
    )
    bass_host_info = None if bass_host is None else _with_sends(
        daw, bass_host.index, daw.get_track_info(bass_host.index)
    )
    kick_available_inputs: dict[str, Any] | None = None
    try:
        topology = daw.get_capture_topology()
    except Exception as exc:
        topology = {"error": str(exc)}
    hello = dict(daw.handshake_info or {})
    missing: list[str] = []
    kick_source_class = "UNSET"
    bass_source_class = "UNSET"
    kick_claim = None
    bass_claim = None
    target_source_unsupported = False

    if unknown_excl:
        missing.append(
            f"lab_track_exclusions contains non-lab names (ignored for matching): {unknown_excl}"
        )

    if session.transport.playing:
        missing.append("transport is playing; stop Live before diagnosis")

    if original_open:
        missing.append(
            "ORIGINAL_SET_OPEN: pista.als is loaded. Use working copy pista_copilot_eval.als."
        )
    elif not working_copy:
        missing.append(
            f"working copy not open (expected …\\{WORKING_COPY_SUFFIX}, open={path or session.project_name!r})"
        )
    if lab_hits_blocking:
        missing.append(f"lab tracks still present: {lab_hits_blocking}")
    if drums is None:
        missing.append("Drums track not found")
    if bass is None:
        missing.append(f"{BASS_TARGET} track not found")

    if main_pos.get("tap") is None:
        missing.append("Main has no Copilot Audio Tap")
    elif not main_pos.get("is_last"):
        missing.append(
            f"Main tap is not last (MAIN_FINAL required). devices={main_pos.get('devices')}"
        )
    devices = list(main_pos.get("devices") or [])
    if devices and devices[0] != "Patience Master":
        missing.append(f"Main first device is {devices[0]!r}, expected Patience Master")

    if master_tap is None:
        missing.append("Master tap not in inventory")
    else:
        if int(master_tap.get("tap_protocol") or -1) != EXPECTED_TAP_PROTOCOL:
            missing.append(
                f"Master TapProtocol={master_tap.get('tap_protocol')} expected {EXPECTED_TAP_PROTOCOL}"
            )
        if int(master_tap.get("slot") if master_tap.get("slot") is not None else -1) != 0:
            missing.append(f"Master tap Slot={master_tap.get('slot')} expected 0")
        if not _tap_rec_is_off(master_tap):
            missing.append(f"Master Rec={master_tap.get('rec')} expected 0")

    expected_tap_tracks = {"MASTER", CAPTURE_HOST, CAPTURE_BASS}
    extra_taps = [
        row for row in inventory if str(row.get("track_name")) not in expected_tap_tracks
    ]
    if extra_taps:
        missing.append(
            "stale/unexpected taps: "
            + ", ".join(str(row.get("track_name")) for row in extra_taps)
        )
    if collisions:
        missing.append(f"duplicate tap slots: {collisions}")
    present_slots = sorted(
        int(row["slot"]) for row in inventory if row.get("slot") is not None
    )
    if inventory and not extra_taps and not collisions and present_slots != [0, 1, 2]:
        missing.append(f"slot ownership is {present_slots}, expected unique [0, 1, 2]")

    if capture_track is None:
        missing.append(f"capture track missing: {CAPTURE_HOST}")
    else:
        row = tap_by_track.get(CAPTURE_HOST)
        if row is None:
            missing.append(f"{CAPTURE_HOST} has no Copilot Audio Tap")
        else:
            if int(row.get("tap_protocol") or -1) != EXPECTED_TAP_PROTOCOL:
                missing.append(
                    f"{CAPTURE_HOST} TapProtocol={row.get('tap_protocol')} expected {EXPECTED_TAP_PROTOCOL}"
                )
            if int(row.get("slot") if row.get("slot") is not None else -1) != 1:
                missing.append(f"{CAPTURE_HOST} Slot={row.get('slot')} expected 1")
            if not _tap_rec_is_off(row):
                missing.append(f"{CAPTURE_HOST} Rec={row.get('rec')} expected 0")
        if capture_info is not None:
            kick_source_class = classify_kick_source(
                str(capture_info.get("input_routing_type") or ""),
                str(capture_info.get("input_routing_channel") or ""),
            )
            try:
                kick_available_inputs = daw.get_available_inputs(capture_track.index)
            except Exception as extra:
                kick_available_inputs = {"error": str(extra)}
            kick_claim = _pad_off_mix_graph(capture_info)
            if kick_source_class != "KICK_PAD_ISOLATED":
                target_source_unsupported = True
                missing.append(
                    "TARGET_SOURCE_UNSUPPORTED: Copilot Capture Audio From must resolve to "
                    f"Drums → Drum Rack | {KICK_PAD} | Post Mixer. Got class={kick_source_class} "
                    f"input={capture_info.get('input_routing_type')!r} "
                    f"channel={capture_info.get('input_routing_channel')!r}. "
                    "Whole-track Drums is not accepted."
                )
            elif not kick_claim.get("post_mixer"):
                missing.append(
                    f"{CAPTURE_HOST} has {KICK_PAD} but subsource is not Post Mixer "
                    f"(channel={capture_info.get('input_routing_channel')!r})"
                )
            elif kick_claim.get("claim") != "OFF_MIX_GRAPH":
                missing.append(
                    f"{CAPTURE_HOST} routing claim={kick_claim.get('claim')} expected OFF_MIX_GRAPH"
                )

    if bass_host is None:
        missing.append(f"capture track missing: {CAPTURE_BASS}")
    else:
        row = tap_by_track.get(CAPTURE_BASS)
        if row is None:
            missing.append(f"{CAPTURE_BASS} has no Copilot Audio Tap")
        else:
            if int(row.get("tap_protocol") or -1) != EXPECTED_TAP_PROTOCOL:
                missing.append(
                    f"{CAPTURE_BASS} TapProtocol={row.get('tap_protocol')} expected {EXPECTED_TAP_PROTOCOL}"
                )
            if int(row.get("slot") if row.get("slot") is not None else -1) != 2:
                missing.append(f"{CAPTURE_BASS} Slot={row.get('slot')} expected 2")
            if not _tap_rec_is_off(row):
                missing.append(f"{CAPTURE_BASS} Rec={row.get('rec')} expected 0")
        if bass_host_info is not None:
            bass_source_class = classify_bass_source(
                str(bass_host_info.get("input_routing_type") or ""),
                str(bass_host_info.get("input_routing_channel") or ""),
            )
            through_main = str(bass_host_info.get("output_routing_type") or "").lower() in {
                "main",
                "master",
            }
            bass_claim = routing_claim(
                input_type=str(bass_host_info.get("input_routing_type")),
                input_channel=str(bass_host_info.get("input_routing_channel")),
                output=str(bass_host_info.get("output_routing_type")),
                monitoring=str(bass_host_info.get("monitoring")),
                through_main=through_main,
                sends=list(bass_host_info.get("sends") or []),
                target_name=BASS_TARGET,
            )
            if bass_source_class == "BASSLINE_CHAIN":
                missing.append(
                    f"{CAPTURE_BASS} source is Bassline chain; need full-track "
                    f"{BASS_TARGET} | Post Mixer "
                    f"(input={bass_host_info.get('input_routing_type')!r} "
                    f"channel={bass_host_info.get('input_routing_channel')!r})"
                )
            elif bass_claim.get("claim") != "OFF_MIX_GRAPH":
                missing.append(
                    f"{CAPTURE_BASS} routing claim={bass_claim.get('claim')} expected OFF_MIX_GRAPH "
                    f"(input={bass_host_info.get('input_routing_type')!r} "
                    f"channel={bass_host_info.get('input_routing_channel')!r} "
                    f"output={bass_host_info.get('output_routing_type')!r} "
                    f"monitor={bass_host_info.get('monitoring')!r})"
                )

    ready = not missing
    status = "PRE-FLIGHT VERIFIED" if ready else "PRE-FLIGHT STOP"
    if target_source_unsupported:
        status = "TARGET_SOURCE_UNSUPPORTED"
    return {
        "phase": "REAL SESSION DIAGNOSIS — PRE-FLIGHT RETRY",
        "status": status,
        "NO LIVE-4": True,
        "NO MUSICAL WRITES": True,
        "NO ROUTING WRITES": True,
        "NO DEVICE INSERTS": True,
        "NO CAPTURE": True,
        "NO DSP": True,
        "model_calls": 0,
        "pass": ready,
        "missing": missing,
        "lab_track_exclusions": sorted(exclusions),
        "lab_hits_present": lab_hits,
        "lab_hits_excluded": lab_hits_excluded,
        "lab_hits_blocking": lab_hits_blocking,
        "target_source_unsupported": target_source_unsupported,
        "kick_source_class": kick_source_class,
        "bass_source_class": bass_source_class,
        "routing_mutations": 0,
        "project_ok": working_copy,
        "working_copy": working_copy,
        "original_open": original_open,
        "live_set_path": session.project_path,
        "project_path": session.project_path,
        "project_name": session.project_name,
        "project_token": session.project_token,
        "audible_token": session.audible_token,
        "revision": session.revision,
        "tempo": session.transport.tempo,
        "playing": session.transport.playing,
        "signature": [
            session.transport.signature_numerator,
            session.transport.signature_denominator,
        ],
        "analyzer_id": ANALYZER_ID,
        "analyzer_sha256": analyzer_fingerprint(),
        "remote_script": {
            "protocol_version": hello.get("protocol_version"),
            "bridge_version": hello.get("bridge_version"),
            "mode": hello.get("mode"),
            "snapshot_source": daw.snapshot_source,
        },
        "expected_tap_protocol": EXPECTED_TAP_PROTOCOL,
        "main": {
            "devices": main_pos.get("devices"),
            "tap_last": main_pos.get("is_last"),
            "claim": "MAIN_FINAL" if main_pos.get("is_last") else "MAIN_NOT_FINAL",
            "tap": master_tap,
        },
        "taps": [
            {
                "track": row.get("track_name"),
                "index": row.get("track_index"),
                "slot": row.get("slot"),
                "tap_protocol": row.get("tap_protocol"),
                "rec": row.get("rec"),
                "device_on": row.get("device_on"),
            }
            for row in inventory
        ],
        "slot_collisions": collisions,
        "tracks": names,
        "drums": None if drums is None else {"index": drums.index, "name": drums.name, "stable_id": drums.stable_id},
        "bass": None if bass is None else {"index": bass.index, "name": bass.name, "stable_id": bass.stable_id},
        "kick_source": {
            "required": KICK_PAD,
            "class": kick_source_class,
            "claim": kick_claim,
            "available_inputs": kick_available_inputs,
        },
        "bass_source": {
            "required": BASS_TARGET,
            "class": bass_source_class,
            "claim": bass_claim,
        },
        "capture_hosts": {
            CAPTURE_HOST: _host_card(
                capture_track,
                capture_info,
                tap_by_track.get(CAPTURE_HOST),
                bool(kick_claim and kick_claim.get("claim") == "OFF_MIX_GRAPH"),
            ),
            CAPTURE_BASS: _host_card(
                bass_host,
                bass_host_info,
                tap_by_track.get(CAPTURE_BASS),
                bool(bass_claim and bass_claim.get("claim") == "OFF_MIX_GRAPH"),
            ),
        },
        "topology": topology,
        "lab_track_hits": lab_hits,
        "instruction": (
            "PRE-FLIGHT VERIFIED. Do not capture yet."
            if ready
            else "PRE-FLIGHT STOP. Do not capture. Do not create tracks, insert devices, or change routing from Core."
        ),
    }


def _host_card(track, info, tap_row, routing_ok: bool) -> dict[str, Any]:
    if track is None:
        return {"present": False}
    return {
        "present": True,
        "index": track.index,
        "name": track.name,
        "tap": tap_row,
        "routing": None
        if info is None
        else {
            "input_type": info.get("input_routing_type"),
            "input_channel": info.get("input_routing_channel"),
            "output": info.get("output_routing_type"),
            "monitoring": info.get("monitoring"),
            "already_correct_for_target": routing_ok,
        },
    }


def write_preflight(report: dict[str, Any], evidence: Path) -> Path:
    path = evidence / "session_diagnose_preflight.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def inspect_session(daw: AbletonTcpAdapter) -> dict[str, Any]:
    session = daw.snapshot(include_notes=False)
    names = [track.name for track in session.tracks]
    name = session.project_name or ""
    path = session.project_path or ""
    lab_project = "título" in name.lower() or "titulo" in name.lower() or "Sin título.als" in path
    lab_hits = [item for item in names if item in LAB_TRACK_MARKERS]
    kick = _first_named(session, REAL_KICK_NAMES)
    bass = _first_named(session, REAL_BASS_NAMES)
    ready = kick is not None and bass is not None and not lab_hits and not lab_project
    return {
        "phase": "REAL SESSION DIAGNOSIS — PHASE 1 INSPECT",
        "NO LIVE-4": True,
        "NO MUSICAL WRITES": True,
        "model_calls": 0,
        "analyzer_id": ANALYZER_ID,
        "analyzer_sha256": analyzer_fingerprint(),
        "project_path": session.project_path,
        "project_name": session.project_name,
        "project_token": session.project_token,
        "audible_token": session.audible_token,
        "tempo": session.transport.tempo,
        "track_names": names,
        "lab_track_hits": lab_hits,
        "lab_project": lab_project,
        "kick": None if kick is None else {"index": kick.index, "name": kick.name, "stable_id": kick.stable_id},
        "bass": None if bass is None else {"index": bass.index, "name": bass.name, "stable_id": bass.stable_id},
        "ready": ready,
        "block": None
        if ready
        else (
            "LIVE_HAS_LAB_SET"
            if lab_hits or lab_project
            else "NEED_KICK_AND_BASS_TRACKS"
        ),
        "real_set_on_disk": REAL_SET_CANDIDATE,
        "instruction": (
            "Open pista.als in Live (the real song). Leave this lab set. "
            "Do not capture Sin título as real-session diagnosis."
            if (lab_hits or lab_project)
            else "Session looks ready for capture."
        ),
        "proposed_regions": []
        if not ready
        else [
            {"id": "R1", "start_qn": 0.0, "end_qn": 8.0, "why_internal_only": "opening 2 bars"},
            {"id": "R2", "start_qn": 16.0, "end_qn": 24.0, "why_internal_only": "later groove"},
            {"id": "R3", "start_qn": 32.0, "end_qn": 40.0, "why_internal_only": "deeper into form"},
        ],
        "blind_rule": "Astra must not receive region beliefs, fixture names, or expected diagnoses.",
    }


def write_inspect(report: dict[str, Any], evidence: Path) -> Path:
    path = evidence / "session_diagnose_inspect.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def human_label_template(region_ids: list[str]) -> dict[str, Any]:
    return {
        "note": "Fill AFTER listening, BEFORE reading Astra if practical.",
        "regions": {
            region_id: {
                "PROBLEM_PRESENT": "YES|NO|UNSURE",
                "LIKELY_CAUSE": "TEMPORAL|SPECTRAL|LEVEL|ARRANGEMENT|SOUND_SELECTION|OTHER|UNKNOWN",
                "ACTION_NEEDED": "YES|NO|UNSURE",
                "confidence": "LOW|MEDIUM|HIGH",
                "notes": "",
            }
            for region_id in region_ids
        },
    }


def _first_named(session, names: tuple[str, ...]):
    for name in names:
        track = session.track_by_name(name)
        if track is not None:
            return track
    return None


def prompt_hash() -> str:
    from copilot.reasoning.prompt import build_prompt
    from copilot.reasoning.fixtures import pack_clear_no_action

    text = build_prompt(pack_clear_no_action())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
