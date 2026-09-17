"""PROJECT_FOLDER_IMPORT_V1 — folder in, read-only Copilot envelope out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copilot.audio.cross_project_bootstrap_v1 import (
    _live_host_infos,
    bootstrap_project,
    classify_project,
    retain_tokens,
)
from copilot.audio.cross_project_musical_validation_v1 import run_cross_project_musical_validation
from copilot.audio.doctor_v1 import doctor
from copilot.audio.producer_analyze_v1 import producer_analyze
from copilot.audio.project_ready_v1 import project_ready
from copilot.audio.tap_trust import inventory_taps
from copilot.audio.terminal_state_v1 import verify_terminal_state
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.session_ready_v1 import SESSION_READY, probe_session_ready, wait_for_session
from copilot.human_eval.store import now_iso
from copilot.importing.ableton_launcher_v1 import launch_working_copy, names_match, paths_match
from copilot.importing.m4l_runtime_v1 import ensure_m4l_runtime, verify_live_browser
from copilot.importing.project_folder_resolver_v1 import resolve_ableton_project
from copilot.importing.working_copy_manager_v1 import create_working_copy, import_identity

MILESTONE = "PROJECT_FOLDER_IMPORT_V1"
ARTIFACT = "project_folder_import_v1.json"


def import_project_folder(
    folder: str | Path,
    *,
    evidence: Path,
    workspace: str | Path | None = None,
    launch: bool = True,
) -> dict[str, Any]:
    discovery = resolve_ableton_project(folder)
    if discovery.get("status") != "RESOLVED":
        report = _blocked(
            discovery.get("status") or "FOLDER_DISCOVERY_FAILED",
            discovery=discovery,
            source_folder=str(folder),
        )
        _persist(report, evidence)
        return report

    source_als = Path(str(discovery["source_als"]))
    source_root = Path(str(discovery["project_root"]))
    source_identity_before = import_identity(source_als, source_root)

    copy = create_working_copy(
        source_als=source_als,
        project_root=source_root,
        copy_scope=str(discovery.get("copy_scope") or "project_directory"),
        workspace=workspace,
    )
    if copy.get("status") not in {"CREATED", "REUSED"}:
        report = _blocked(
            str(copy.get("status") or "WORKING_COPY_FAILED"),
            discovery=discovery,
            copy=copy,
            source_folder=str(folder),
        )
        _persist(report, evidence)
        return report

    source_identity_after = import_identity(source_als, source_root)
    source_untouched = source_identity_before == source_identity_after
    working_als = Path(str(copy["working_als"]))

    launch_report: dict[str, Any] = {"status": "SKIPPED"}
    if not launch:
        report = _blocked(
            "LAUNCH_SKIPPED",
            discovery=discovery,
            copy=copy,
            launch=launch_report,
            source_folder=str(folder),
            resolved_source_als=str(source_als),
            working_copy_path=copy.get("working_root"),
            ORIGINAL_UNTOUCHED=source_untouched,
        )
        _persist(report, evidence)
        return report

    provision = ensure_m4l_runtime(evidence=evidence)
    if provision.get("status") not in {"INSTALLED", "ALREADY_CURRENT", "UPDATED"}:
        report = _blocked(
            str(provision.get("reason") or provision.get("status") or "M4L_RUNTIME_BLOCKED"),
            discovery=discovery,
            copy=copy,
            m4l_runtime=provision,
            source_folder=str(folder),
            ORIGINAL_UNTOUCHED=source_untouched,
        )
        _persist(report, evidence)
        return report

    force_launch = provision.get("status") in {"INSTALLED", "UPDATED"}
    launch_report = launch_working_copy(working_als, force=force_launch)
    if launch_report.get("status") != SESSION_READY:
        report = _blocked(
            str(launch_report.get("status") or "LAUNCH_FAILED"),
            discovery=discovery,
            copy=copy,
            launch=launch_report,
            m4l_runtime=provision,
            source_folder=str(folder),
            ORIGINAL_UNTOUCHED=source_untouched,
        )
        _persist(report, evidence)
        return report

    probe = wait_for_session(deadline_s=120)
    if probe.status != SESSION_READY:
        report = _blocked(
            probe.status,
            discovery=discovery,
            copy=copy,
            launch=launch_report,
            session=probe.to_dict(),
            ORIGINAL_UNTOUCHED=source_untouched,
        )
        _persist(report, evidence)
        return report
    if not paths_match(probe.project_path, working_als) and not names_match(
        probe.project_name, working_als
    ):
        report = _blocked(
            "OPENED_PROJECT_MISMATCH",
            discovery=discovery,
            copy=copy,
            launch=launch_report,
            session=probe.to_dict(),
            ORIGINAL_UNTOUCHED=source_untouched,
        )
        _persist(report, evidence)
        return report

    daw = AbletonTcpAdapter()
    doctor_report: dict[str, Any] = {}
    bootstrap: dict[str, Any] = {}
    ready: dict[str, Any] = {}
    analyze: dict[str, Any] = {}
    identity: dict[str, Any] = {}
    cross: dict[str, Any] = {}
    browser: dict[str, Any] = {}
    second_provision: dict[str, Any] = {}
    second_bootstrap: dict[str, Any] = {}
    end_terminal: dict[str, Any] = {}
    session_identity = probe.project_identity
    try:
        daw.connect()
        session = daw.snapshot(include_notes=False)
        retain_tokens(session)
        identity = classify_project(session.project_path, session.project_name)
        session_identity = session.project_identity or probe.project_identity
        wait_index = str(launch_report.get("launch") or "") == "already_open"
        browser = verify_live_browser(daw, wait=wait_index)
        if browser.get("LIVE_BROWSER_RESOLUTION") != "VERIFIED":
            daw.disconnect()
            launch_report = launch_working_copy(working_als, force=True)
            if launch_report.get("status") != SESSION_READY:
                report = _blocked(
                    str(launch_report.get("status") or "LAUNCH_FAILED"),
                    discovery=discovery,
                    copy=copy,
                    launch=launch_report,
                    m4l_runtime=provision,
                    browser=browser,
                    ORIGINAL_UNTOUCHED=source_untouched,
                    reason_detail="M4L_BROWSER_RESTART_REQUIRED",
                )
                _persist(report, evidence)
                return report
            probe = probe_session_ready()
            daw.connect()
            browser = verify_live_browser(daw, wait=False)
        if browser.get("LIVE_BROWSER_RESOLUTION") != "VERIFIED":
            report = _blocked(
                "M4L_BROWSER_RESTART_REQUIRED",
                discovery=discovery,
                copy=copy,
                launch=launch_report,
                m4l_runtime=provision,
                browser=browser,
                ORIGINAL_UNTOUCHED=source_untouched,
            )
            _persist(report, evidence)
            return report
        doctor_report = doctor(evidence=evidence, daw=daw, session_probe=probe)
        bootstrap = bootstrap_project(daw, evidence=evidence)
        second_provision = ensure_m4l_runtime(evidence=evidence)
        second_bootstrap = bootstrap_project(daw, evidence=evidence)
        ready = project_ready(daw, evidence=evidence)
        cross = run_cross_project_musical_validation(daw, evidence=evidence)
        analyze = {
            "status": cross.get("analyze_status"),
            "gate": cross.get("gate"),
        }
        if not analyze.get("status") and ready.get("PROJECT_READY") == "VERIFIED":
            analyze = producer_analyze(daw, evidence=evidence)
        end_session = daw.snapshot(include_notes=False)
        retain_tokens(end_session)
        end_terminal = verify_terminal_state(
            transport_playing=end_session.transport.playing,
            taps=inventory_taps(daw),
            host_infos=_live_host_infos(daw, end_session),
        )
    except Exception as exc:  # noqa: BLE001
        report = _blocked(
            "LIVE_SESSION_ERROR",
            discovery=discovery,
            copy=copy,
            launch=launch_report,
            source_folder=str(folder),
            ORIGINAL_UNTOUCHED=source_untouched,
            error=str(exc),
            project=identity,
            bootstrap_status=bootstrap.get("status"),
            project_ready_status=ready.get("PROJECT_READY"),
        )
        _persist(report, evidence)
        return report
    finally:
        daw.disconnect()

    plumbing_ok = (
        bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1") == "VERIFIED"
        and ready.get("PROJECT_READY") == "VERIFIED"
        and source_untouched
        and probe.status == SESSION_READY
        and provision.get("M4L_ASSET_ON_DISK") == "VERIFIED"
        and browser.get("LIVE_BROWSER_RESOLUTION") == "VERIFIED"
        and second_provision.get("status") == "ALREADY_CURRENT"
        and second_bootstrap.get("status") == "NO_CHANGES_REQUIRED"
        and bool(end_terminal.get("ok"))
    )
    overall = "READY" if plumbing_ok else "BLOCKED"
    blocker = None
    if not plumbing_ok:
        if end_terminal and not end_terminal.get("ok"):
            blocker = ",".join(end_terminal.get("failures") or ["TERMINAL_NOT_RESTORED"])
        elif bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1") != "VERIFIED":
            blocker = str(bootstrap.get("reason") or "BOOTSTRAP_NOT_VERIFIED")
        elif second_bootstrap.get("status") != "NO_CHANGES_REQUIRED":
            blocker = "BOOTSTRAP_NOT_IDEMPOTENT"
        elif ready.get("PROJECT_READY") != "VERIFIED":
            blocker = str(ready.get("reason") or "PROJECT_READY_BLOCKED")
        elif browser.get("LIVE_BROWSER_RESOLUTION") != "VERIFIED":
            blocker = "LIVE_BROWSER_RESOLUTION"
        else:
            blocker = "PROJECT_FOLDER_IMPORT_GATES"
    report = {
        "milestone": MILESTONE,
        "status": overall,
        "PROJECT_FOLDER_IMPORT_V1": overall,
        "M4L_RUNTIME_PROVISIONING_V1": provision.get("M4L_RUNTIME_PROVISIONING_V1"),
        "ts": now_iso(),
        "source_folder": str(folder),
        "resolved_source_als": str(source_als),
        "working_copy_path": copy.get("working_root"),
        "working_als": str(working_als),
        "launch_status": launch_report.get("status"),
        "SESSION_READY": probe.status,
        "project_identity": session_identity,
        "project": identity,
        "bootstrap_status": bootstrap.get("status"),
        "bootstrap_milestone": bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1"),
        "bootstrap_second_status": second_bootstrap.get("status"),
        "REAL_EXTERNAL_SONG": identity.get("kind") == "external",
        "BLOCKER": blocker,
        "m4l_runtime_status": provision.get("status"),
        "m4l_second_status": second_provision.get("status"),
        "M4L_ASSET_ON_DISK": provision.get("M4L_ASSET_ON_DISK"),
        "LIVE_BROWSER_RESOLUTION": browser.get("LIVE_BROWSER_RESOLUTION"),
        "project_ready_status": ready.get("PROJECT_READY"),
        "preflight_status": ready.get("preflight_status"),
        "preflight_kind": ready.get("preflight_kind"),
        "producer_analyze_status": analyze.get("status") or cross.get("analyze_status"),
        "cross_project_status": cross.get("status"),
        "CROSS_PROJECT_MUSICAL_VALIDATION_V1": cross.get("CROSS_PROJECT_MUSICAL_VALIDATION_V1"),
        "MUSICAL WRITES": 0,
        "NO WRITE": True,
        "ORIGINAL_UNTOUCHED": source_untouched,
        "NO MANUAL ROUTING": True,
        "terminal": end_terminal or (ready.get("terminal") or {}),
        "open_journals": end_terminal.get("open_capture_journals")
        or (ready.get("journals") or []),
        "open_transactions": end_terminal.get("open_transactions")
        or (ready.get("transactions") or []),
        "bootstrap": {
            "status": bootstrap.get("status"),
            "CROSS_PROJECT_BOOTSTRAP_V1": bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1"),
            "reason": bootstrap.get("reason"),
            "off_mix_graph": bootstrap.get("off_mix_graph"),
            "missing_after": bootstrap.get("missing_after"),
        },
        "second_bootstrap": {
            "status": second_bootstrap.get("status"),
            "CROSS_PROJECT_BOOTSTRAP_V1": second_bootstrap.get("CROSS_PROJECT_BOOTSTRAP_V1"),
            "off_mix_graph": second_bootstrap.get("off_mix_graph"),
        },
        "discovery": discovery,
        "working_copy": copy,
        "launch": launch_report,
        "m4l_runtime": provision,
        "browser": browser,
        "doctor_status": doctor_report.get("status"),
        "gate": analyze.get("gate") or cross.get("gate"),
        "decision": cross.get("decision"),
        "off_mix_graph": bootstrap.get("off_mix_graph") or second_bootstrap.get("off_mix_graph"),
    }
    _persist(report, evidence)
    return report


def _blocked(status: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "milestone": MILESTONE,
        "status": "BLOCKED",
        "PROJECT_FOLDER_IMPORT_V1": "BLOCKED",
        "reason": status,
        "MUSICAL WRITES": 0,
        "NO WRITE": True,
        "ts": now_iso(),
    }
    payload.update(extra)
    return payload


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
