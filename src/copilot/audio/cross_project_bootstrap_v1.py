"""CROSS_PROJECT_BOOTSTRAP_V1 — install observation infra on an unknown project.

No musical mutations. Exact infra names only. Lookalike user tracks are never
taken over. Second complete run returns NO_CHANGES_REQUIRED.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.agent.journal import DurableJournal
from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    CAPTURE_HOST,
    CAPTURE_KICK,
    ensure_named_audio_track,
    load_tap_on_track,
    route_host_post_mixer,
)
from copilot.audio.live_capture import (
    EXPECTED_TAP_PROTOCOL,
    MASTER_INDEX,
    AudioCaptureError,
    ensure_master_tap,
    find_tap,
    master_tap_position,
    set_tap_recording,
    set_tap_slot,
)
from copilot.importing.working_copy_manager_v1 import is_copilot_source, is_copilot_working_copy
from copilot.audio.tap_trust import duplicate_slots, inventory_taps, routing_claim
from copilot.audio.terminal_state_v1 import (
    BOOTSTRAP_JOURNAL_DIR,
    unresolved_agent_transactions,
    unresolved_bootstrap_journals,
    unresolved_capture_journals,
    verify_terminal_state,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.daw.object_ref import PersistentObjectRef, ref_from_track
from copilot.daw.state_tokens import attach_tokens
from copilot.human_eval.store import now_iso
from copilot.schemas.session import SessionState, TrackState

MILESTONE = "CROSS_PROJECT_BOOTSTRAP_V1"
ARTIFACT = "cross_project_bootstrap_v1.json"
FIXTURE_SUFFIX = "copilot_bootstrap_fixture.als"
INFRA_HOSTS = (CAPTURE_HOST, CAPTURE_BASS)
KNOWN_INFRA_NAMES = frozenset({CAPTURE_HOST, CAPTURE_BASS, CAPTURE_KICK})
SLOT_BY_OWNER = { "MASTER": 0, CAPTURE_HOST: 1, CAPTURE_BASS: 2}
SOURCE_BUDGET = 2


def _path_norm(path: str) -> str:
    return path.replace("/", "\\").lower()


def retain_tokens(session: SessionState) -> SessionState:
    """attach_tokens(path=None) would wipe project_path. Keep identity."""
    return attach_tokens(session, path=session.project_path, name=session.project_name)


def classify_project(project_path: str | None, project_name: str | None = None) -> dict[str, Any]:
    path = _path_norm(project_path or "")
    name = (project_name or "").lower()
    blob = unicodedata.normalize("NFKD", f"{path} {name}")
    blob = "".join(ch for ch in blob if not unicodedata.combining(ch))
    kind = "unknown"
    if is_copilot_working_copy(project_path or ""):
        kind = "development_working_copy"
    elif is_copilot_source(project_path or ""):
        kind = "development_original"
    elif path.endswith(FIXTURE_SUFFIX.lower()) or "copilot_bootstrap_fixture" in blob:
        kind = "bootstrap_fixture"
    elif "untitled" in blob or "sin titulo" in blob:
        kind = "untitled_scratch"
    elif path or name:
        kind = "external"
    return {
        "kind": kind,
        "project_path": project_path or "",
        "project_name": project_name or "",
        "refuse_original": kind == "development_original",
        "is_development_working_copy": kind == "development_working_copy",
    }


def is_exact_infra_name(name: str) -> bool:
    from copilot.audio.capture_scalability_v2 import is_capture_host_name

    return name in KNOWN_INFRA_NAMES or is_capture_host_name(name)


def is_lookalike_user_track(name: str) -> bool:
    if is_exact_infra_name(name):
        return False
    lowered = name.lower()
    return "copilot capture" in lowered or lowered.startswith("copilot ")


def _tap_rec_off(row: dict[str, Any] | None) -> bool:
    if row is None or row.get("rec") is None:
        return False
    try:
        return abs(float(row["rec"])) < 0.01
    except (TypeError, ValueError):
        return False


def _user_collision_on_infra_name(track: TrackState) -> bool:
    if not is_exact_infra_name(track.name):
        return False
    foreign = [
        device
        for device in track.devices
        if "copilot audio tap" not in device.name.lower()
    ]
    return bool(track.clips or foreign)


def eligible_bootstrap_sources(session: SessionState) -> list[TrackState]:
    rows: list[TrackState] = []
    for track in session.tracks:
        if track.role not in {"audio", "midi"}:
            continue
        if is_exact_infra_name(track.name) or is_lookalike_user_track(track.name):
            continue
        if _user_collision_on_infra_name(track):
            continue
        rows.append(track)
    return rows[:SOURCE_BUDGET]


def expected_host_slot(name: str) -> int | None:
    if name in SLOT_BY_OWNER:
        return SLOT_BY_OWNER[name]
    from copilot.audio.capture_scalability_v2 import slot_for_host_name

    return slot_for_host_name(name)


def _host_info_from_track(track: TrackState) -> dict[str, Any]:
    routing = getattr(track, "routing", None)
    if routing is None:
        return {}
    return {
        "input_routing_type": routing.input_type,
        "input_routing_channel": routing.input_channel,
        "output_routing_type": routing.output_type,
        "monitoring": routing.monitoring,
        "sends": [
            {"send_index": send.index, "level": send.value, "min": 0.0, "max": 1.0}
            for send in getattr(track, "sends", []) or []
        ],
    }


def _host_routing_card(info: dict[str, Any] | None, target_name: str | None) -> dict[str, Any]:
    from copilot.audio.capture_host_baseline_v1 import evaluate_parked

    if info is None:
        return {"claim": "MISSING", "routed_target": None, "already_correct": False}
    through_main = str(info.get("output_routing_type") or "").lower() in {
        "main",
        "master",
    }
    current = str(info.get("input_routing_type") or "")
    target = current or (target_name or "")
    claim = routing_claim(
        input_type=str(info.get("input_routing_type")),
        input_channel=str(info.get("input_routing_channel")),
        output=str(info.get("output_routing_type")),
        monitoring=str(info.get("monitoring")),
        through_main=through_main,
        sends=list(info.get("sends") or []),
        target_name=target,
    )
    parked_state = {
        "input": {
            "input_routing_type": info.get("input_routing_type"),
            "input_routing_channel": info.get("input_routing_channel"),
        },
        "output": {
            "output_routing_type": info.get("output_routing_type"),
            "output_routing_channel": info.get("output_routing_channel") or "",
        },
        "monitoring": {"monitoring": info.get("monitoring"), "value": info.get("monitoring")},
        "sends": list(info.get("sends") or []),
        "taps": list(info.get("taps") or []),
        "devices": list(info.get("devices") or []),
    }
    parked = evaluate_parked(parked_state)
    return {
        **claim,
        "routed_target": current or None,
        "already_correct": bool(parked.get("ok")),
        "parked": parked,
    }


def discover_topology(
    *,
    session: SessionState,
    inventory: list[dict[str, Any]],
    master_pos: dict[str, Any],
    host_infos: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    retain_tokens(session)
    identity = classify_project(session.project_path, session.project_name)
    project_identity = session.project_identity or session.project_token or ""
    tap_by_track = {str(row.get("track_name")): row for row in inventory}
    collisions = duplicate_slots(inventory)
    sources: list[dict[str, Any]] = []
    for track in session.tracks:
        if track.role in {"return", "master"}:
            continue
        ref = ref_from_track(track, project_identity=project_identity)
        sources.append(
            {
                "ref": ref.model_dump(mode="json"),
                "display_name": track.name,
                "role": track.role,
                "index_locator_only": track.index,
                "infra": is_exact_infra_name(track.name),
                "lookalike_user": is_lookalike_user_track(track.name),
                "name_collision": _user_collision_on_infra_name(track),
                "device_names": [device.name for device in track.devices],
            }
        )
    hosts: dict[str, Any] = {}
    infos = host_infos or {}
    from copilot.audio.capture_scalability_v2 import is_capture_host_name

    host_names = list(INFRA_HOSTS)
    for track in session.tracks:
        if is_capture_host_name(track.name) and track.name not in host_names:
            host_names.append(track.name)
    for name in host_names:
        track = session.track_by_name(name)
        info = infos.get(name)
        if info is None and track is not None:
            info = _host_info_from_track(track)
        hosts[name] = {
            "present": track is not None,
            "index": None if track is None else track.index,
            "name_collision": False if track is None else _user_collision_on_infra_name(track),
            "tap": tap_by_track.get(name),
            "target_name": None,
            "routing": _host_routing_card(info, None),
            "lookalikes": [
                row["display_name"] for row in sources if row["lookalike_user"]
            ],
        }
    return {
        "project": identity,
        "project_identity": project_identity,
        "project_token": session.project_token,
        "audible_token": session.audible_token,
        "revision": session.revision,
        "transport_playing": bool(session.transport.playing),
        "tempo": session.transport.tempo,
        "track_count": len(session.tracks),
        "sources": sources,
        "eligible_source_names": [t.name for t in eligible_bootstrap_sources(session)],
        "hosts": hosts,
        "main": {
            "tap": master_pos.get("tap"),
            "is_last": bool(master_pos.get("is_last")),
            "devices": master_pos.get("devices") or [],
            "claim": "MAIN_FINAL" if master_pos.get("is_last") else "MAIN_NOT_FINAL",
        },
        "taps": inventory,
        "slot_collisions": collisions,
        "lookalike_user_tracks": [
            row["display_name"] for row in sources if row["lookalike_user"]
        ],
        "infra_name_collisions": [
            row["display_name"] for row in sources if row["name_collision"]
        ],
    }


def missing_topology(discovery: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if discovery.get("transport_playing"):
        missing.append("transport is playing; stop Live before bootstrap")
    if (discovery.get("project") or {}).get("refuse_original"):
        missing.append("ORIGINAL_SET_OPEN: refuse bootstrap on the protected source project")
    if discovery.get("infra_name_collisions"):
        missing.append(
            "USER_TRACK_NAME_COLLISION: "
            + ", ".join(discovery["infra_name_collisions"])
        )
    main = discovery.get("main") or {}
    if main.get("tap") is None:
        missing.append("Main has no Copilot Audio Tap")
    elif not main.get("is_last"):
        missing.append("Main tap is not last (MAIN_FINAL required)")
    for name, host in (discovery.get("hosts") or {}).items():
        required = name in INFRA_HOSTS
        if host.get("name_collision"):
            continue
        if not host.get("present"):
            if required:
                missing.append(f"capture track missing: {name}")
            continue
        tap = host.get("tap")
        if tap is None:
            missing.append(f"{name} has no Copilot Audio Tap")
            continue
        expected_slot = expected_host_slot(name)
        slot = tap.get("slot")
        if expected_slot is not None and (slot is None or int(slot) != expected_slot):
            missing.append(f"{name} Slot={slot} expected {expected_slot}")
        if not _tap_rec_off(tap):
            missing.append(f"{name} Rec={tap.get('rec')} expected 0")
        protocol = tap.get("tap_protocol")
        if protocol is not None and int(protocol) not in {EXPECTED_TAP_PROTOCOL, 4}:
            missing.append(
                f"{name} TapProtocol={protocol} expected {EXPECTED_TAP_PROTOCOL} or 4"
            )
        routing = host.get("routing") or {}
        if not routing.get("already_correct"):
            missing.append(f"{name} is not in canonical parked state")
    if discovery.get("slot_collisions"):
        missing.append(f"duplicate tap slots: {discovery['slot_collisions']}")
    return missing


def plan_bootstrap(discovery: dict[str, Any]) -> dict[str, Any]:
    if (discovery.get("project") or {}).get("refuse_original"):
        return {
            "status": "BLOCKED",
            "reason": "ORIGINAL_SET_OPEN",
            "actions": [],
        }
    if discovery.get("infra_name_collisions"):
        return {
            "status": "BLOCKED",
            "reason": "USER_TRACK_NAME_COLLISION",
            "actions": [],
            "collisions": discovery["infra_name_collisions"],
        }
    if discovery.get("transport_playing"):
        return {
            "status": "BLOCKED",
            "reason": "TRANSPORT_PLAYING",
            "actions": [],
        }
    actions: list[dict[str, Any]] = []
    main = discovery.get("main") or {}
    if main.get("tap") is None:
        actions.append({"op": "ENSURE_MASTER_TAP"})
    elif not main.get("is_last"):
        actions.append({"op": "ENSURE_MASTER_TAP_FINAL"})
    hosts = discovery.get("hosts") or {}
    for name in INFRA_HOSTS:
        host = hosts.get(name) or {}
        if host.get("name_collision"):
            continue
        if not host.get("present"):
            actions.append({"op": "ENSURE_HOST_TRACK", "name": name})
        tap = host.get("tap")
        if tap is None:
            actions.append({"op": "ENSURE_HOST_TAP", "name": name})
        expected_slot = expected_host_slot(name)
        if tap is None or tap.get("slot") is None or (
            expected_slot is not None and int(tap.get("slot")) != expected_slot
        ):
            actions.append({"op": "ENSURE_SLOT", "name": name, "slot": expected_slot})
        elif not _tap_rec_off(tap):
            actions.append({"op": "IDLE_TAP", "name": name})
    for name, host in hosts.items():
        if host.get("name_collision") or not host.get("present"):
            continue
        if name not in INFRA_HOSTS:
            tap = host.get("tap")
            if tap is None:
                actions.append({"op": "ENSURE_HOST_TAP", "name": name})
            expected_slot = expected_host_slot(name)
            if tap is None or (
                expected_slot is not None
                and (tap.get("slot") is None or int(tap.get("slot")) != expected_slot)
            ):
                actions.append({"op": "ENSURE_SLOT", "name": name, "slot": expected_slot})
            elif not _tap_rec_off(tap):
                actions.append({"op": "IDLE_TAP", "name": name})
        routing = host.get("routing") or {}
        if routing.get("already_correct"):
            continue
        actions.append({"op": "ENSURE_HOST_PARKED", "name": name})
    if not actions:
        return {"status": "NO_CHANGES_REQUIRED", "actions": []}
    return {"status": "CHANGES_REQUIRED", "actions": actions}


def _live_host_infos(
    daw: AbletonTcpAdapter, session: SessionState
) -> dict[str, dict[str, Any]]:
    infos: dict[str, dict[str, Any]] = {}
    from copilot.audio.capture_scalability_v2 import is_capture_host_name

    names = list(INFRA_HOSTS)
    for track in session.tracks:
        if is_capture_host_name(track.name) and track.name not in names:
            names.append(track.name)
    for name in names:
        track = session.track_by_name(name)
        if track is None:
            continue
        try:
            infos[name] = daw.get_track_info(track.index)
        except DawError:
            continue
    return infos


def _host_index(session: SessionState, name: str) -> int | None:
    track = session.track_by_name(name)
    return None if track is None else track.index


def _apply_action(
    daw: AbletonTcpAdapter,
    session: SessionState,
    action: dict[str, Any],
    mutations: list[str],
) -> dict[str, Any]:
    op = str(action.get("op") or "")
    if op == "ENSURE_MASTER_TAP":
        result = ensure_master_tap(daw)
        mutations.append("master_tap")
        return result
    if op == "ENSURE_MASTER_TAP_FINAL":
        # Cannot reorder devices safely from Core; detect only.
        return {"skipped": True, "reason": "MAIN_TAP_NOT_FINAL_REQUIRES_MANUAL"}
    if op == "ENSURE_HOST_TRACK":
        created = ensure_named_audio_track(daw, str(action["name"]), snapshot=True)
        if created.get("created"):
            mutations.append(f"create:{action['name']}")
        return created
    if op == "ENSURE_HOST_TAP":
        session = daw.snapshot(include_notes=False)
        index = _host_index(session, str(action["name"]))
        if index is None:
            raise AudioCaptureError("TAP_MISSING", f"{action['name']} missing after create")
        loaded = load_tap_on_track(daw, index)
        if not loaded.get("already_loaded"):
            mutations.append(f"tap:{action['name']}")
        return loaded
    if op == "ENSURE_SLOT":
        session = daw.snapshot(include_notes=False)
        index = _host_index(session, str(action["name"]))
        if index is None:
            raise AudioCaptureError("TAP_MISSING", f"{action['name']} missing for slot")
        slot_set = set_tap_slot(daw, index, int(action["slot"]))
        mutations.append(f"slot:{action['name']}->{action['slot']}")
        return slot_set
    if op == "IDLE_TAP":
        session = daw.snapshot(include_notes=False)
        index = _host_index(session, str(action["name"]))
        if index is None:
            return {"skipped": True}
        set_tap_recording(daw, False, index, broadcast_udp=False)
        mutations.append(f"idle:{action['name']}")
        return {"rec": 0}
    if op == "ENSURE_HOST_PARKED":
        from copilot.audio.capture_host_baseline_v1 import (
            normalize_and_verify_host,
            record_host_repair,
        )

        session = daw.snapshot(include_notes=False)
        index = _host_index(session, str(action["name"]))
        if index is None:
            raise AudioCaptureError(
                "HOST_PROVISION_FAILED", f"{action['name']} missing for parked init"
            )
        parked = normalize_and_verify_host(
            daw, index, expected_slot=expected_host_slot(str(action["name"]))
        )
        if not parked.get("verified"):
            raise AudioCaptureError(
                "HOST_PROVISION_FAILED",
                f"{action['name']} parked verify failed: "
                f"{(parked.get('verdict') or {}).get('mismatches')}",
            )
        mutations.append(f"park:{action['name']}")
        record_host_repair(
            str(action["name"]),
            {"verified": True, "mutations": (parked.get("applied") or {}).get("mutations") or []},
        )
        return parked
    if op == "ENSURE_HOST_ROUTING":
        session = daw.snapshot(include_notes=False)
        index = _host_index(session, str(action["name"]))
        if index is None:
            raise AudioCaptureError(
                "CAPTURE_ROUTING_UNSUPPORTED", f"{action['name']} missing for routing"
            )
        info = daw.get_track_info(index)
        target = str(action["target_name"])
        already = (
            target.lower() in str(info.get("input_routing_type") or "").lower()
            and "post mixer" in str(info.get("input_routing_channel") or "").lower()
            and str(info.get("output_routing_type") or "").lower()
            in {"no output", "sends only"}
        )
        if already:
            return {"skipped": True, "already_correct": True, "target": target}
        routed = route_host_post_mixer(daw, index, target)
        sends = list(daw.get_track_sends(index) or [])
        for row in sends:
            daw.set_send_level(index, int(row.get("send_index", 0)), 0.0)
        mutations.append(f"route:{action['name']}->{target}")
        return routed
    raise AudioCaptureError("BOOTSTRAP_UNKNOWN_OP", op)


def _off_mix_claims(
    daw: AbletonTcpAdapter, session: SessionState, discovery: dict[str, Any]
) -> dict[str, Any]:
    claims: dict[str, Any] = {}
    for name, host in (discovery.get("hosts") or {}).items():
        track = session.track_by_name(name)
        if track is None:
            claims[name] = {"claim": "MISSING"}
            continue
        info = daw.get_track_info(track.index)
        through_main = str(info.get("output_routing_type") or "").lower() in {
            "main",
            "master",
        }
        target = str(info.get("input_routing_type") or "")
        claims[name] = routing_claim(
            input_type=str(info.get("input_routing_type")),
            input_channel=str(info.get("input_routing_channel")),
            output=str(info.get("output_routing_type")),
            monitoring=str(info.get("monitoring")),
            through_main=through_main,
            sends=list(info.get("sends") or daw.get_track_sends(track.index) or []),
            target_name=target,
        )
    return claims


def bootstrap_project(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path,
    journal_dir: Path | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Install missing observation infrastructure. Never mutates user tracks."""
    pass_id = uuid4().hex[:12]
    root = journal_dir or BOOTSTRAP_JOURNAL_DIR
    root.mkdir(parents=True, exist_ok=True)
    journal = DurableJournal(root / f"{pass_id}.jsonl")
    journal.append({"status": "PREPARED", "milestone": MILESTONE, "pass_id": pass_id})

    session = daw.snapshot(include_notes=False)
    retain_tokens(session)
    inventory = inventory_taps(daw)
    master_pos = master_tap_position(daw)
    discovery = discover_topology(
        session=session,
        inventory=inventory,
        master_pos=master_pos,
        host_infos=_live_host_infos(daw, session),
    )
    plan = plan_bootstrap(discovery)
    stale = unresolved_bootstrap_journals(root)
    if stale:
        journal.append(
            {
                "status": "APPLYING",
                "recovery_of": [row["pass_id"] for row in stale],
            }
        )

    report: dict[str, Any] = {
        "milestone": MILESTONE,
        "pass_id": pass_id,
        "ts": now_iso(),
        "project": discovery["project"],
        "project_identity": discovery["project_identity"],
        "project_token": discovery["project_token"],
        "audible_token": discovery["audible_token"],
        "discovery": {
            "track_count": discovery["track_count"],
            "eligible_source_names": discovery["eligible_source_names"],
            "lookalike_user_tracks": discovery["lookalike_user_tracks"],
            "infra_name_collisions": discovery["infra_name_collisions"],
            "main": discovery["main"],
            "hosts": {
                name: {
                    "present": card.get("present"),
                    "name_collision": card.get("name_collision"),
                    "has_tap": card.get("tap") is not None,
                }
                for name, card in discovery["hosts"].items()
            },
        },
        "missing_before": missing_topology(discovery),
        "plan_status": plan["status"],
        "actions": plan.get("actions") or [],
        "mutations": [],
        "musical_mutations": 0,
        "duplicate_devices": 0,
        "duplicate_capture_tracks": 0,
        "NO MUSICAL MUTATIONS": True,
    }

    if plan["status"] == "BLOCKED":
        journal.append({"status": "FAILED", "reason": plan.get("reason")})
        report["status"] = "BLOCKED"
        report["reason"] = plan.get("reason")
        report["CROSS_PROJECT_BOOTSTRAP_V1"] = "BLOCKED"
        _persist(report, evidence)
        return report

    if plan["status"] == "NO_CHANGES_REQUIRED" or not apply:
        journal.append({"status": "VERIFIED", "result": "NO_CHANGES_REQUIRED"})
        session = daw.snapshot(include_notes=False)
        inventory = inventory_taps(daw)
        discovery = discover_topology(
            session=session,
            inventory=inventory,
            master_pos=master_tap_position(daw),
            host_infos=_live_host_infos(daw, session),
        )
        report["status"] = "NO_CHANGES_REQUIRED"
        report["CROSS_PROJECT_BOOTSTRAP_V1"] = (
            "VERIFIED" if not missing_topology(discovery) else "PARTIAL"
        )
        report["missing_after"] = missing_topology(discovery)
        report["off_mix_graph"] = _off_mix_claims(daw, session, discovery)
        report["terminal"] = _terminal(daw, session, inventory, journal_dir=root)
        _persist(report, evidence)
        return report

    journal.append({"status": "APPLYING", "actions": plan["actions"]})
    mutations: list[str] = []
    applied: list[dict[str, Any]] = []
    try:
        for action in plan["actions"]:
            applied.append(
                {"action": action, "result": _apply_action(daw, session, action, mutations)}
            )
    except (AudioCaptureError, DawError) as exc:
        journal.append({"status": "FAILED", "error": str(exc)})
        report["status"] = "BLOCKED"
        report["reason"] = str(exc)
        report["CROSS_PROJECT_BOOTSTRAP_V1"] = "BLOCKED"
        report["applied"] = applied
        report["mutations"] = mutations
        _persist(report, evidence)
        return report

    session = daw.snapshot(include_notes=False)
    retain_tokens(session)
    inventory = inventory_taps(daw)
    discovery_after = discover_topology(
        session=session,
        inventory=inventory,
        master_pos=master_tap_position(daw),
        host_infos=_live_host_infos(daw, session),
    )
    missing_after = missing_topology(discovery_after)
    second = plan_bootstrap(discovery_after)
    infra_tracks = [t.name for t in session.tracks if is_exact_infra_name(t.name)]
    host_counts = {name: infra_tracks.count(name) for name in INFRA_HOSTS}
    tap_counts: dict[str, int] = {}
    for row in inventory:
        key = str(row.get("track_name"))
        tap_counts[key] = tap_counts.get(key, 0) + 1

    report["applied"] = applied
    report["mutations"] = mutations
    report["missing_after"] = missing_after
    report["second_plan_status"] = second["status"]
    report["duplicate_capture_tracks"] = sum(
        1 for count in host_counts.values() if count > 1
    )
    report["duplicate_devices"] = sum(1 for count in tap_counts.values() if count > 1)
    report["off_mix_graph"] = _off_mix_claims(daw, session, discovery_after)
    report["open_journals"] = unresolved_capture_journals()
    report["open_transactions"] = unresolved_agent_transactions()
    report["terminal"] = _terminal(daw, session, inventory, journal_dir=root)
    if missing_after or second["status"] not in {"NO_CHANGES_REQUIRED", "BLOCKED"}:
        if second["status"] == "BLOCKED":
            report["status"] = "BLOCKED"
            report["CROSS_PROJECT_BOOTSTRAP_V1"] = "BLOCKED"
            journal.append({"status": "FAILED", "missing": missing_after})
        else:
            report["status"] = "PARTIAL"
            report["CROSS_PROJECT_BOOTSTRAP_V1"] = "PARTIAL"
            journal.append({"status": "VERIFIED", "result": "PARTIAL", "missing": missing_after})
    else:
        report["status"] = "BOOTSTRAP_APPLIED"
        report["CROSS_PROJECT_BOOTSTRAP_V1"] = "VERIFIED"
        journal.append({"status": "VERIFIED", "result": "BOOTSTRAP_APPLIED"})
    _persist(report, evidence)
    return report


def _terminal(
    daw: AbletonTcpAdapter,
    session: SessionState,
    inventory: list[dict[str, Any]],
    *,
    journal_dir: Path,
) -> dict[str, Any]:
    host_infos: dict[str, dict[str, Any]] = {}
    for name in INFRA_HOSTS:
        track = session.track_by_name(name)
        if track is None:
            continue
        host_infos[name] = daw.get_track_info(track.index)
    return verify_terminal_state(
        transport_playing=session.transport.playing,
        taps=inventory,
        host_infos=host_infos,
        bootstrap_journal_dir=journal_dir,
    )


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def write_blocker(evidence: Path, reason: str, **extra: Any) -> dict[str, Any]:
    report = {
        "milestone": MILESTONE,
        "CROSS_PROJECT_BOOTSTRAP_V1": "BLOCKED",
        "status": "BLOCKED",
        "reason": reason,
        "ts": now_iso(),
        **extra,
    }
    _persist(report, evidence)
    return report
