"""Generic project onboarding orchestrator.

open .als → project-ready

Reuses bootstrap + existing preflight. Does not run Astra. No musical writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copilot.audio.cross_project_bootstrap_v1 import (
    INFRA_HOSTS,
    MILESTONE as BOOTSTRAP_MILESTONE,
    _live_host_infos,
    bootstrap_project,
    classify_project,
    discover_topology,
    missing_topology,
    plan_bootstrap,
    retain_tokens,
)
from copilot.audio.live_capture import master_tap_position
from copilot.audio.tap_trust import inventory_taps
from copilot.audio.terminal_state_v1 import verify_terminal_state
from copilot.audio.working_copy_policy_v1 import evaluate_working_copy
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.human_eval.store import now_iso

MILESTONE = "PROJECT_READY_V1"
ARTIFACT = "project_ready_v1.json"


def generic_preflight(
    *,
    discovery: dict[str, Any],
    terminal: dict[str, Any],
) -> dict[str, Any]:
    """Read-only generic readiness. Not the development-lab preflight."""
    missing = list(missing_topology(discovery))
    if not terminal.get("ok"):
        missing.extend(f"terminal:{item}" for item in terminal.get("failures") or [])
    ready = not missing
    return {
        "kind": "GENERIC_PREFLIGHT_V1",
        "pass": ready,
        "status": "PRE-FLIGHT VERIFIED" if ready else "PRE-FLIGHT STOP",
        "missing": missing,
        "NO LIVE-4": True,
        "NO MUSICAL WRITES": True,
        "NO ASTRA": True,
        "project": discovery.get("project"),
        "project_identity": discovery.get("project_identity"),
        "track_count": discovery.get("track_count"),
        "main": discovery.get("main"),
        "eligible_source_names": discovery.get("eligible_source_names"),
        "lookalike_user_tracks": discovery.get("lookalike_user_tracks"),
        "instruction": (
            "PRE-FLIGHT VERIFIED. Observation infrastructure is ready."
            if ready
            else "PRE-FLIGHT STOP. Do not capture until missing topology is resolved."
        ),
    }


def project_ready(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path,
    bootstrap_apply: bool = True,
) -> dict[str, Any]:
    session = daw.snapshot(include_notes=False)
    retain_tokens(session)
    identity = classify_project(session.project_path, session.project_name)
    policy = evaluate_working_copy(session.project_path, session.project_name)
    if identity["refuse_original"] or not policy["operate"]:
        report = {
            "milestone": MILESTONE,
            "PROJECT_READY": "BLOCKED",
            "status": "BLOCKED",
            "reason": policy["reason"] if not policy["operate"] else "ORIGINAL_SET_OPEN",
            "project": identity,
            "working_copy_policy": policy,
            "ts": now_iso(),
            "NO ASTRA": True,
            "NO MUSICAL WRITES": True,
        }
        _persist(report, evidence)
        return report

    bootstrap = bootstrap_project(daw, evidence=evidence, apply=bootstrap_apply)
    session = daw.snapshot(include_notes=False)
    retain_tokens(session)
    inventory = inventory_taps(daw)
    discovery = discover_topology(
        session=session,
        inventory=inventory,
        master_pos=master_tap_position(daw),
        host_infos=_live_host_infos(daw, session),
    )
    host_infos: dict[str, dict[str, Any]] = {}
    for name in INFRA_HOSTS:
        track = session.track_by_name(name)
        if track is not None:
            host_infos[name] = daw.get_track_info(track.index)
    terminal = verify_terminal_state(
        transport_playing=session.transport.playing,
        taps=inventory,
        host_infos=host_infos,
    )

    # ``preflight_session`` is a legacy Groove Rider/lab diagnosis.  It
    # intentionally requires named Drums/Kick/Bass tracks and must not gate
    # generic onboarding of another working copy.  Project readiness only
    # proves identity + observation topology + terminal safety; capture
    # planning discovers the actual project sources afterwards.
    preflight = generic_preflight(discovery=discovery, terminal=terminal)
    (evidence / "generic_preflight_v1.json").write_text(
        json.dumps(preflight, indent=2, default=str), encoding="utf-8"
    )
    preflight_kind = "GENERIC_PREFLIGHT_V1"

    second = plan_bootstrap(discovery)
    bootstrap_ok = bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1") in {
        "VERIFIED",
        "PARTIAL",
    } and bootstrap.get("status") in {
        "NO_CHANGES_REQUIRED",
        "BOOTSTRAP_APPLIED",
        "PARTIAL",
    }
    # PARTIAL is not ready.
    if bootstrap.get("status") == "PARTIAL" or bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1") == "PARTIAL":
        bootstrap_ok = False
    if bootstrap.get("status") == "NO_CHANGES_REQUIRED" and bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1") == "VERIFIED":
        bootstrap_ok = True
    if bootstrap.get("status") == "BOOTSTRAP_APPLIED" and bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1") == "VERIFIED":
        bootstrap_ok = True

    ready = (
        bootstrap_ok
        and bool(preflight.get("pass"))
        and second.get("status") == "NO_CHANGES_REQUIRED"
        and bool(terminal.get("ok"))
    )
    report = {
        "milestone": MILESTONE,
        "PROJECT_READY": "VERIFIED" if ready else "BLOCKED",
        "status": "VERIFIED" if ready else "BLOCKED",
        "ts": now_iso(),
        "project": identity,
        "working_copy_policy": policy,
        "project_identity": discovery.get("project_identity"),
        "project_token": session.project_token,
        "audible_token": session.audible_token,
        "bootstrap_status": bootstrap.get("status"),
        "bootstrap_milestone": bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1"),
        "bootstrap_reused": BOOTSTRAP_MILESTONE,
        "preflight_kind": preflight_kind,
        "preflight_status": preflight.get("status"),
        "preflight_pass": preflight.get("pass"),
        "track_count": discovery.get("track_count"),
        "arrangement_status": {
            "eligible_sources": discovery.get("eligible_source_names"),
            "note": "clip/device presence only — not audible contribution",
        },
        "capture_readiness": {
            "main_final": bool((discovery.get("main") or {}).get("is_last")),
            "hosts": {
                name: bool((discovery.get("hosts") or {}).get(name, {}).get("present"))
                for name in INFRA_HOSTS
            },
        },
        "source_isolation_readiness": {
            "eligible_source_count": len(discovery.get("eligible_source_names") or []),
            "generic": not identity["is_development_working_copy"],
        },
        "journals": terminal.get("open_capture_journals") or [],
        "transactions": terminal.get("open_transactions") or [],
        "transport": {
            "playing": session.transport.playing,
            "stopped": not session.transport.playing,
            "tempo": session.transport.tempo,
        },
        "terminal": terminal,
        "second_bootstrap": second.get("status"),
        "NO ASTRA": True,
        "NO MUSICAL WRITES": True,
    }
    _persist(report, evidence)
    return report


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
