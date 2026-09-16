"""Small Live smoke for session state trust. Not a capture fixture."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.daw.object_ref import ResolveStatus, ref_from_track, resolve_track
from copilot.daw.plan_envelope import authorize_execution, observe_plan
from copilot.daw.state_errors import STALE_PLAN, StateTrustError
from copilot.daw.state_tokens import StateScope
from copilot.schemas.session import MidiNote

PROBE = "Copilot State Probe"
PROBE_RENAMED = "Copilot State Probe Renamed"
PROBE_B = "Copilot State Probe B"


def _track_by_index(session, index: int | None):
    if index is None:
        return None
    for track in session.tracks:
        if track.index == index:
            return track
    return None


def run_live_state(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "phase": "SESSION STATE TRUST — LIVE SMOKE",
        "NO CAPTURE": True,
        "NO DIAGNOSIS": True,
        "NO LIVE-4": True,
    }

    def snapshot():
        t0 = time.perf_counter()
        tcp0 = int(daw.tcp_stats()["total"])
        session = daw.snapshot(include_notes=False)
        report.setdefault("PERF", {})["last_snapshot_s"] = time.perf_counter() - t0
        report["PERF"]["last_snapshot_tcp"] = int(daw.tcp_stats()["total"]) - tcp0
        report["PERF"]["snapshot_source"] = daw.snapshot_source
        return session

    def cleanup() -> None:
        if daw._sock is None:
            daw.connect()
        live = daw.snapshot(include_notes=False)
        for name in (PROBE_RENAMED, PROBE, PROBE_B):
            track = live.track_by_name(name)
            if track is None:
                continue
            try:
                daw.delete_track(track.index)
            except DawError:
                pass
            live = daw.snapshot(include_notes=False)

    try:
        daw.reset_tcp_stats()
        session = snapshot()
        kick = session.track_by_name("LIVE22 Kick") or session.track_by_name("Kick")
        if kick is None:
            report["STOP"] = "need Kick track as identity subject"
            return report
        kick_ref = ref_from_track(kick, project_identity=session.project_identity)
        report["PROJECT"] = {
            "path": session.project_path,
            "name": session.project_name,
            "identity": session.project_identity,
            "project_token": session.project_token,
            "audible_token": session.audible_token,
            "revision": session.revision,
            "identity_kind": "live_set_path" if session.project_path else "structural_fingerprint",
        }

        daw.create_midi_track(PROBE, -1)
        session = snapshot()
        probe = session.track_by_name(PROBE)
        if probe is None:
            raise DawError("probe track missing after create")
        try:
            daw.create_midi_clip(probe.index, 0, 4.0)
            daw.replace_clip_notes(
                probe.index, 0, [MidiNote(pitch=60, start_time=0.0, duration=1.0)]
            )
        except DawError as exc:
            report["probe_clip"] = str(exc)
        session = snapshot()
        probe = session.track_by_name(PROBE)
        assert probe is not None
        probe_ref = ref_from_track(probe, project_identity=session.project_identity)

        daw.set_track_name(probe.index, PROBE_RENAMED)
        session = snapshot()
        renamed = resolve_track(session, probe_ref)
        report["RENAME"] = renamed.model_dump()

        daw.create_midi_track(PROBE_B, 0)
        session = snapshot()
        inserted = resolve_track(session, probe_ref)
        kick_now = resolve_track(session, kick_ref)
        report["INSERT_UNRELATED"] = inserted.model_dump()
        report["KICK_IDENTITY_AFTER_INSERT"] = kick_now.model_dump()

        probe_now = _track_by_index(session, inserted.track_index)
        if probe_now is None:
            probe_now = session.track_by_name(PROBE_RENAMED)
        if probe_now is None:
            raise DawError("probe missing after insert")
        plan = observe_plan(
            session,
            scope=StateScope.TARGET,
            track=probe_now,
            intended_operation="volume",
        )
        authorize_execution(session, plan)
        want = 0.31 if probe_now.mixer.volume > 0.5 else 0.72
        daw.set_mixer_volume(probe_now.index, want)
        stale_session = snapshot()
        try:
            authorize_execution(stale_session, plan)
            report["STALE_PLAN"] = {"raised": False}
        except StateTrustError as exc:
            report["STALE_PLAN"] = {"raised": True, "code": exc.code}

        incarnation = stale_session.session_incarnation_id
        project_identity = stale_session.project_identity
        daw.disconnect()
        daw.session_incarnation_id = f"sess_{uuid4().hex[:12]}"
        daw.connect()
        reconnected = snapshot()
        report["RECONNECT"] = {
            "incarnation_changed": reconnected.session_incarnation_id != incarnation,
            "same_project": reconnected.project_identity == project_identity,
            "kick": resolve_track(reconnected, kick_ref).status.value,
            "probe": resolve_track(reconnected, probe_ref).status.value,
            "tcp_total": daw.tcp_stats()["total"],
        }
        report["ACCEPT"] = {
            "rename_resolved": renamed.status is ResolveStatus.RESOLVED,
            "insert_resolved": inserted.status is ResolveStatus.RESOLVED,
            "kick_preserved": kick_now.status is ResolveStatus.RESOLVED,
            "stale_plan": (report.get("STALE_PLAN") or {}).get("code") == STALE_PLAN,
            "reconnect_same_project": report["RECONNECT"]["same_project"],
            "reconnect_kick": report["RECONNECT"]["kick"] == "RESOLVED",
            "snapshot_tcp_ok": int(report["PERF"].get("last_snapshot_tcp") or 99) <= 8,
        }
        report["ok"] = all(report["ACCEPT"].values())
        return report
    finally:
        try:
            cleanup()
        except DawError:
            report["cleanup_error"] = True
