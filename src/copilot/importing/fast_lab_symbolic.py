"""Small reusable FAST_LAB MIDI lane realization via Compiler and SafeWrite."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.importing.ableton_launcher_v1 import paths_match
from copilot.importing.working_copy_manager_v1 import is_copilot_working_copy
from copilot.musicplan import build_create_track_action, build_device_load_action, build_pattern_action
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass, PlanStatus
from copilot.schemas.session import MidiNote, TrackState


def _execute(executor, session, actions, *, label: str, evidence_ref: str,
             target: TrackState | None = None) -> dict:
    plan = MusicPlan(
        plan_id=f"fast_lab_{label}_{session.revision}",
        status=PlanStatus.READY_FOR_EXECUTION,
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        diagnosis=DiagnosisBinding(diagnosis_id=f"symbolic:{evidence_ref}",
                                   diagnosis_status="INFERRED_SYMBOLIC_INTERPRETATION",
                                   diagnosis_accepted=True),
        project_state_token=session.project_token or "",
        audible_state_token=session.audible_token or "",
        target_state_tokens={target.name: target_token(target)} if target else {},
        evidence_refs=[evidence_ref], actions=actions,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    compiled = ProductionCompiler().compile(plan, session=session)
    if compiled.status != "COMPILED" or compiled.intent is None:
        raise RuntimeError(f"COMPILE_FAILED: {compiled.reasons}")
    result = executor.run(compiled.intent)
    summary = {"ok": result.ok, "failure": str(result.failure) if result.failure else None,
               "decision": str(result.decision), "musical_writes": result.musical_writes,
               "readback_matched": bool(result.readbacks) and all(row.authoritative and row.matched for row in result.readbacks),
               "error": result.error}
    if not result.ok:
        raise RuntimeError(f"SAFEWRITE_FAILED: {summary}")
    return summary


def realize_lane(working_als: Path, journal_root: Path, output_path: Path,
                 *, track_name: str, notes: list[MidiNote], length_beats: float,
                 instrument_name: str, evidence_ref: str) -> dict:
    if not is_copilot_working_copy(working_als) or output_path.exists():
        raise RuntimeError("WORKING_COPY_REQUIRED_OR_OUTPUT_EXISTS")
    if not notes or len(notes) > 512 or any(note.start_time < 0 or note.start_time + note.duration > length_beats + 1e-6 for note in notes):
        raise RuntimeError("INVALID_NOTE_SPAN")
    daw = AbletonTcpAdapter()
    daw.connect()
    try:
        if not paths_match(daw.get_session_path().get("path"), working_als):
            raise RuntimeError("PROJECT_MISMATCH")
        executor = build_safe_write_executor(daw, journal_path=journal_root / "safe-write.jsonl",
                                             persist_dir=journal_root / "prestate")
        unresolved = [row for row in executor.recover() if row["recovery"] != "VERIFIED"]
        if unresolved:
            raise RuntimeError(f"UNRESOLVED_SAFEWRITE_JOURNAL: {unresolved}")
        session = executor.tools.get_session_snapshot()
        attach_tokens(session)
        if session.track_by_name(track_name) is not None:
            raise RuntimeError("TRACK_ALREADY_EXISTS; no silent overwrite")
        virtual = TrackState(stable_id="", index=-1, name=track_name, role="midi")
        actions = [
            build_create_track_action(project_identity=session.project_identity or "", track_name=track_name,
                                      track_kind="midi", reason="FAST_LAB symbolic lane", evidence_refs=[evidence_ref]),
            build_pattern_action(track=virtual, project_identity=session.project_identity or "", clip_index=0,
                                 length_beats=length_beats, notes=notes,
                                 reason="FAST_LAB inferred symbolic notes", evidence_refs=[evidence_ref]),
        ]
        pattern = _execute(executor, session, actions, label=f"{track_name}_pattern", evidence_ref=evidence_ref)
        session = executor.tools.get_session_snapshot()
        attach_tokens(session)
        track = session.track_by_name(track_name)
        if track is None:
            raise RuntimeError("TRACK_READBACK_MISSING")
        search = daw.search_browser(instrument_name, "instruments")
        choices = [row for row in search.get("results", []) if row.get("name") == instrument_name and row.get("is_loadable")]
        if len(choices) != 1:
            raise RuntimeError(f"INSTRUMENT_NOT_UNAMBIGUOUS: {search}")
        device_action = build_device_load_action(
            track=track, project_identity=session.project_identity or "", device_name=instrument_name,
            device_uri=choices[0]["uri"], reason="FAST_LAB audible symbolic interpretation",
            evidence_refs=[evidence_ref], session_incarnation_id=session.session_incarnation_id or "",
        )
        device = _execute(executor, session, [device_action], label=f"{track_name}_instrument",
                          evidence_ref=evidence_ref, target=track)
        final = executor.tools.get_session_snapshot()
        track = final.track_by_name(track_name)
        if track is None:
            raise RuntimeError("TRACK_LOST")
        observed = daw.get_clip_notes(track.index, 0)["notes"]
        exact = len(observed) == len(notes) and all(
            row["pitch"] == note.pitch and abs(row["start_time"] - note.start_time) < 1e-5
            and abs(row["duration"] - note.duration) < 1e-5 and row["velocity"] == note.velocity
            for row, note in zip(observed, notes)
        )
        report = {"status": "PASS" if exact else "FAILED", "track_name": track_name,
                  "track_index": track.index, "instrument": instrument_name,
                  "notes_requested": len(notes), "notes_readback": len(observed), "readback_exact": exact,
                  "pattern_safe_write": pattern, "instrument_safe_write": device,
                  "evidence_ref": evidence_ref, "working_als": str(working_als)}
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not exact:
            raise RuntimeError("AUTHORITATIVE_NOTE_READBACK_MISMATCH")
        return report
    finally:
        daw.disconnect()


def evidence_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
