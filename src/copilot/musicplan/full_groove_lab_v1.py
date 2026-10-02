"""One four-role, eight-bar FAST_LAB MusicPlan using existing verified motifs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.importing.ableton_launcher_v1 import paths_match
from copilot.importing.fast_lab_sound_once import _apply, _plan
from copilot.importing.working_copy_manager_v1 import is_copilot_working_copy
from copilot.musicplan import (build_create_track_action, build_device_load_action,
                               build_duplicate_clip_to_arrangement_action,
                               build_pattern_action, build_sample_load_action)
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass, PlanStatus
from copilot.schemas.session import MidiNote, TrackState


def _repeat_four_bar_notes(daw: AbletonTcpAdapter, track_index: int) -> list[MidiNote]:
    source = daw.get_clip_notes(track_index, 0)
    if source.get("length") != 16.0 or not source.get("notes"):
        raise RuntimeError("SOURCE_MOTIF_NOT_FOUR_BARS")
    return [MidiNote(pitch=int(row["pitch"]), start_time=float(row["start_time"]) + offset,
                     duration=float(row["duration"]), velocity=int(row["velocity"]))
            for offset in (0.0, 16.0) for row in source["notes"]]


def _same_notes(observed: list[dict], expected: list[MidiNote]) -> bool:
    if len(observed) != len(expected):
        return False
    actual_rows = sorted((int(row["pitch"]), float(row["start_time"]), float(row["duration"]),
                          int(row["velocity"])) for row in observed)
    expected_rows = sorted((note.pitch, note.start_time, note.duration, note.velocity) for note in expected)
    return all(a[0] == b[0] and a[3] == b[3] and abs(a[1] - b[1]) < 1e-5
               and abs(a[2] - b[2]) < 1e-5 for a, b in zip(actual_rows, expected_rows))


def reconcile_existing(working_als: Path, output_dir: Path) -> dict:
    """Read-only reconciliation of a completed intent; never replay it."""
    plan_path, report_path = output_dir / "full_groove_musicplan.json", output_dir / "full_groove_execution.json"
    plan = MusicPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report["combined_safewrite_ok"] or report["combined_safewrite_writes"] != 14:
        raise RuntimeError("COMBINED_TRANSACTION_NOT_VERIFIED")
    expected = {action.target.ref["name"]: action.params.notes for action in plan.actions
                if action.action_type.value == "CREATE_PATTERN"}
    daw = AbletonTcpAdapter()
    daw.connect()
    try:
        if not paths_match(daw.get_session_path().get("path"), working_als):
            raise RuntimeError("PROJECT_MISMATCH")
        arrangement = daw.get_arrangement_clips()["clips"]
        session = daw.snapshot(include_notes=False)
        for track_name, notes in expected.items():
            track = session.track_by_name(track_name)
            if track is None:
                raise RuntimeError(f"TRACK_MISSING: {track_name}")
            observed = daw.get_clip_notes(track.index, 0)["notes"]
            matching = [clip for clip in arrangement if clip["track_index"] == track.index
                        and abs(clip["start_time"]) < 1e-6 and abs(clip["length"] - 32) < 1e-6]
            if not _same_notes(observed, notes) or len(matching) != 1:
                raise RuntimeError(f"AUTHORITATIVE_READBACK_MISMATCH: {track_name}")
            report["tracks"][track_name]["exact"] = True
            report["tracks"][track_name]["arrangement_8bars"] = True
        report["status"] = "PASS"
        report["reconciliation"] = "READ_ONLY_CANONICAL_NOTE_ORDER_AND_ARRANGEMENT_READBACK"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        daw.disconnect()


def run(working_als: Path, journal_root: Path, output_dir: Path, *,
        drum_evidence: Path, bass_evidence: Path, chord_evidence: Path) -> dict:
    if not is_copilot_working_copy(working_als):
        raise RuntimeError("WORKING_COPY_REQUIRED")
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path, report_path = output_dir / "full_groove_musicplan.json", output_dir / "full_groove_execution.json"
    if plan_path.exists() or report_path.exists():
        raise FileExistsError("FULL_GROOVE_PLAN_ALREADY_EXISTS")
    source_ids = [str(path) for path in (drum_evidence, bass_evidence, chord_evidence)]
    for path in (drum_evidence, bass_evidence, chord_evidence):
        if not path.is_file():
            raise FileNotFoundError(path)
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
        if session.transport.tempo != 125 or session.transport.signature_numerator != 4:
            raise RuntimeError("TEMPO_OR_METER_MISMATCH")
        groups = (
            ("LAB_FULL_KICK", "LAB_KICK_V2_SIMPLER", None, drum_evidence),
            ("LAB_FULL_HAT", "LAB_HAT_SIMPLER", None, drum_evidence),
            ("LAB_FULL_BASS", "LAB_BASS_V2", "Analog", bass_evidence),
            ("LAB_FULL_HARMONY", "LAB_HARMONY", "Analog", chord_evidence),
        )
        if any(session.track_by_name(name) is not None for name, _, _, _ in groups):
            raise RuntimeError("FULL_GROOVE_TRACK_ALREADY_EXISTS")
        actions = []
        expected = {}
        for track_name, source_name, instrument, evidence in groups:
            source = session.track_by_name(source_name)
            if source is None:
                raise RuntimeError(f"SOURCE_TRACK_MISSING: {source_name}")
            notes = _repeat_four_bar_notes(daw, source.index)
            expected[track_name] = notes
            virtual = TrackState(stable_id="", index=-1, name=track_name, role="midi")
            actions.append(build_create_track_action(project_identity=session.project_identity or "",
                                                     track_name=track_name, track_kind="midi",
                                                     reason="FAST_LAB eight-bar combined groove", evidence_refs=[str(evidence)]))
            if instrument:
                search = daw.search_browser(instrument, "instruments")
                choices = [row for row in search.get("results", []) if row.get("name") == instrument and row.get("is_loadable")]
                if len(choices) != 1:
                    raise RuntimeError("INSTRUMENT_NOT_UNAMBIGUOUS")
                actions.append(build_device_load_action(track=virtual, project_identity=session.project_identity or "",
                                                        device_name=instrument, device_uri=choices[0]["uri"],
                                                        reason="Stock instrument for combined groove",
                                                        evidence_refs=[str(evidence)]))
            actions.append(build_pattern_action(track=virtual, project_identity=session.project_identity or "",
                                                clip_index=0, length_beats=32.0, notes=notes,
                                                reason="Eight-bar repeated verified motif", evidence_refs=[str(evidence)]))
            actions.append(build_duplicate_clip_to_arrangement_action(track=virtual,
                                                                       project_identity=session.project_identity or "",
                                                                       clip_index=0, destination_time=0.0, length=None,
                                                                       reason="Place eight-bar combined groove",
                                                                       evidence_refs=[str(evidence)]))
        plan = MusicPlan(
            plan_id=f"full_groove_lab_{session.revision}", status=PlanStatus.READY_FOR_EXECUTION,
            intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
            diagnosis=DiagnosisBinding(diagnosis_id="full-groove:verified-role-motifs",
                                       diagnosis_status="INFERRED_COMBINED_EXPERIMENT", diagnosis_accepted=True),
            project_state_token=session.project_token or "", audible_state_token=session.audible_token or "",
            evidence_refs=source_ids, actions=actions,
            notes=["Eight-bar combined realization; no autonomous musical quality claim."],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        compiled = ProductionCompiler().compile(plan, session=session)
        if compiled.status != "COMPILED" or compiled.intent is None:
            raise RuntimeError(f"COMBINED_COMPILE_FAILED: {compiled.reasons}")
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        result = executor.run(compiled.intent)
        if not result.ok:
            raise RuntimeError(f"COMBINED_SAFEWRITE_FAILED: {result.failure} {result.error}")
        # Drum sample loading remains a certified per-track SafeWrite action;
        # the multi-MIDI compiler intentionally does not accept LOAD_SAMPLE.
        sample_loads = {}
        for track_name, sample_name in (("LAB_FULL_KICK", "kick-dirt-beater.wav"),
                                        ("LAB_FULL_HAT", "hat-muted.wav")):
            session = executor.tools.get_session_snapshot()
            attach_tokens(session)
            track = session.track_by_name(track_name)
            if track is None:
                raise RuntimeError("COMBINED_TRACK_READBACK_MISSING")
            action = build_sample_load_action(track=track, project_identity=session.project_identity or "",
                                              clip_index=0,
                                              sample_uri=f"Samples/FAST_LAB_2026_10_02/{sample_name}",
                                              reason="Sound the combined drum role", evidence_refs=[str(drum_evidence)],
                                              session_incarnation_id=session.session_incarnation_id or "")
            sample_loads[track_name] = _apply(executor, _plan(session, [action], str(drum_evidence),
                                                               f"{track_name}_sample", target_track=track), session)
        final = executor.tools.get_session_snapshot()
        tracks = {}
        for track_name, _, _, _ in groups:
            track = final.track_by_name(track_name)
            if track is None:
                raise RuntimeError("COMBINED_TRACK_LOST")
            observed = daw.get_clip_notes(track.index, 0)["notes"]
            expected_notes = expected[track_name]
            exact = _same_notes(observed, expected_notes)
            tracks[track_name] = {"index": track.index, "requested": len(expected_notes),
                                  "readback": len(observed), "exact": exact,
                                  "devices": [device.name for device in track.devices]}
        report = {"status": "PASS" if all(item["exact"] for item in tracks.values()) else "FAILED",
                  "combined_musicplan": str(plan_path), "compiled_action_count": len(actions),
                  "combined_safewrite_ok": result.ok, "combined_safewrite_writes": result.musical_writes,
                  "sample_loads": sample_loads, "tracks": tracks, "working_als": str(working_als),
                  "limitations": ["Drum sample loads are two subsequent certified SafeWrite actions, not inside the multi-MIDI intent.",
                                  "Repetition is a bounded eight-bar proof, not producer-quality arrangement."]}
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if report["status"] != "PASS":
            raise RuntimeError("COMBINED_READBACK_MISMATCH")
        return report
    finally:
        daw.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("working_als", type=Path)
    parser.add_argument("journal_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("drum_evidence", type=Path)
    parser.add_argument("bass_evidence", type=Path)
    parser.add_argument("chord_evidence", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.working_als, args.journal_root, args.output_dir,
                         drum_evidence=args.drum_evidence, bass_evidence=args.bass_evidence,
                         chord_evidence=args.chord_evidence), indent=2))
