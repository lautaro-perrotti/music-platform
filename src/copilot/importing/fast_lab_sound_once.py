"""Route the verified drum roles to two project samples in disposable Live."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from copilot.audio.drum_events_v1 import DrumEventSetV1
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.importing.ableton_launcher_v1 import paths_match
from copilot.importing.working_copy_manager_v1 import is_copilot_working_copy
from copilot.musicplan import build_create_track_action, build_pattern_action, build_sample_load_action
from copilot.musicplan.drum_reconstruction_v1 import build_drum_reconstruction
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass, PlanStatus
from copilot.schemas.session import MidiNote, TrackState


def _plan(session, actions, source_asset_id: str, suffix: str,
          target_track: TrackState | None = None) -> MusicPlan:
    return MusicPlan(
        plan_id=f"fast_lab_{suffix}_{session.revision}",
        status=PlanStatus.READY_FOR_EXECUTION,
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        diagnosis=DiagnosisBinding(
            diagnosis_id=f"drum-sound:{source_asset_id}",
            diagnosis_status="EXISTING_SYMBOLIC_RECONSTRUCTION",
            diagnosis_accepted=True,
        ),
        project_state_token=session.project_token or "",
        audible_state_token=session.audible_token or "",
        target_state_tokens={target_track.name: target_token(target_track)} if target_track is not None else {},
        evidence_refs=[source_asset_id],
        actions=actions,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _apply(executor, plan, session) -> dict:
    compiled = ProductionCompiler().compile(plan, session=session)
    if compiled.status != "COMPILED" or compiled.intent is None:
        raise RuntimeError(f"COMPILE_FAILED: {compiled.reasons}")
    result = executor.run(compiled.intent)
    summary = {
        "ok": result.ok,
        "failure": str(result.failure) if result.failure else None,
        "decision": str(result.decision),
        "musical_writes": result.musical_writes,
        "readback_matched": bool(result.readbacks) and all(row.authoritative and row.matched for row in result.readbacks),
        "error": result.error,
    }
    if not result.ok:
        raise RuntimeError(f"SAFEWRITE_FAILED: {summary}")
    return summary


def run(working_als: Path, event_path: Path, journal_root: Path, *,
        kick_track_name: str = "LAB_KICK_SIMPLER",
        kick_sample_name: str = "kick-deep-beater.wav") -> dict:
    if not is_copilot_working_copy(working_als):
        raise RuntimeError("WORKING_COPY_REQUIRED")
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    reconstruction = build_drum_reconstruction(DrumEventSetV1.model_validate(payload))
    daw = AbletonTcpAdapter()
    daw.connect()
    try:
        if not paths_match(daw.get_session_path().get("path"), working_als):
            raise RuntimeError("PROJECT_MISMATCH")
        executor = build_safe_write_executor(
            daw,
            journal_path=journal_root / "safe-write.jsonl",
            persist_dir=journal_root / "prestate",
        )
        unresolved = [row for row in executor.recover() if row["recovery"] != "VERIFIED"]
        if unresolved:
            raise RuntimeError(f"UNRESOLVED_SAFEWRITE_JOURNAL: {unresolved}")
        summary: dict = {"sample_tracks": {}, "symbolic_role_mapping": {"KICK": 36, "CLOSED_HAT": 42},
                         "simpler_trigger_pitch": 60}
        for role, track_name, sample_name in (
            ("KICK", kick_track_name, kick_sample_name),
            ("CLOSED_HAT", "LAB_HAT_SIMPLER", "hat-muted.wav"),
        ):
            sample_path = working_als.parent / "Samples" / sample_name
            if not sample_path.is_file():
                raise RuntimeError(f"SAMPLE_MISSING: {sample_path}")
            events = [event for event in reconstruction.events if event.role == role]
            if len(events) != 16:
                raise RuntimeError(f"UNEXPECTED_{role}_COUNT: {len(events)}")
            notes = [MidiNote(pitch=60, start_time=event.onset_qn,
                              duration=event.note_duration_qn or 0.125,
                              velocity=event.velocity or 64) for event in events]
            session = executor.tools.get_session_snapshot()
            attach_tokens(session)
            track = session.track_by_name(track_name)
            if track is None:
                virtual = TrackState(stable_id="", index=-1, name=track_name, role="midi")
                actions = [
                    build_create_track_action(project_identity=session.project_identity or "",
                                              track_name=track_name, track_kind="midi",
                                              reason=f"FAST_LAB {role} sample lane", evidence_refs=[reconstruction.source_asset_id]),
                    build_pattern_action(track=virtual, project_identity=session.project_identity or "",
                                         clip_index=0, length_beats=16.0, notes=notes,
                                         reason=f"FAST_LAB {role} source timing", evidence_refs=[reconstruction.source_asset_id]),
                ]
                created = _apply(executor, _plan(session, actions, reconstruction.source_asset_id,
                                                  f"{role.lower()}_pattern"), session)
                session = executor.tools.get_session_snapshot()
                attach_tokens(session)
                track = session.track_by_name(track_name)
            else:
                created = {"ok": True, "reused": True}
            if track is None:
                raise RuntimeError(f"TRACK_READBACK_MISSING: {track_name}")
            if not track.devices:
                action = build_sample_load_action(
                    track=track, project_identity=session.project_identity or "", clip_index=0,
                    sample_uri=f"Samples/FAST_LAB_2026_10_02/{sample_name}", reason=f"FAST_LAB {role} sound",
                    evidence_refs=[reconstruction.source_asset_id],
                    session_incarnation_id=session.session_incarnation_id or "",
                )
                loaded = _apply(executor, _plan(session, [action], reconstruction.source_asset_id,
                                                 f"{role.lower()}_sample", target_track=track), session)
            else:
                loaded = {"ok": True, "reused": True, "devices": [device.name for device in track.devices]}
            fresh = executor.tools.get_session_snapshot()
            observed_track = fresh.track_by_name(track_name)
            observed_notes = daw.get_clip_notes(observed_track.index, 0)
            summary["sample_tracks"][role] = {
                "track_name": track_name,
                "track_index": observed_track.index,
                "pattern": created,
                "sample": loaded,
                "note_count": observed_notes.get("note_count"),
                "devices": [(device.name, device.sample_uri) for device in observed_track.devices],
            }
        summary["status"] = "PASS" if all(
            row["note_count"] == 16 and row["sample"]["ok"] for row in summary["sample_tracks"].values()
        ) else "FAILED"
        return summary
    finally:
        daw.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("working_als", type=Path)
    parser.add_argument("event_artifact", type=Path)
    parser.add_argument("journal_root", type=Path)
    parser.add_argument("--kick-track", default="LAB_KICK_SIMPLER")
    parser.add_argument("--kick-sample", default="kick-deep-beater.wav")
    args = parser.parse_args()
    print(json.dumps(run(args.working_als, args.event_artifact, args.journal_root,
                         kick_track_name=args.kick_track,
                         kick_sample_name=args.kick_sample), indent=2))


if __name__ == "__main__":
    main()
