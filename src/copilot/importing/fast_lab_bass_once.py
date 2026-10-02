"""One controlled inferred-bass realization in a manifest-backed FAST_LAB."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.importing.ableton_launcher_v1 import paths_match
from copilot.importing.fast_lab_sound_once import _apply, _plan
from copilot.importing.working_copy_manager_v1 import is_copilot_working_copy
from copilot.musicplan import build_create_track_action, build_device_load_action, build_pattern_action
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.session import MidiNote, TrackState


def run(working_als: Path, evidence_path: Path, journal_root: Path, output_path: Path,
        *, track_name: str = "LAB_BASS", instrument_name: str = "Operator") -> dict:
    if not is_copilot_working_copy(working_als):
        raise RuntimeError("WORKING_COPY_REQUIRED")
    if output_path.exists():
        raise FileExistsError(output_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence["status"] != "INFERRED_NOT_HUMAN_VERIFIED" or evidence["tempo_bpm"] != 125:
        raise RuntimeError("BASS_EVIDENCE_NOT_READY")
    source_id = evidence["source_sha256"]
    if hashlib.sha256(Path(evidence["source"]).read_bytes()).hexdigest() != source_id:
        raise RuntimeError("BASS_SOURCE_HASH_MISMATCH")
    expected = []
    for note in evidence["accepted_notes"]:
        if note["authority"] != "INFERRED" or note["source_sha256"] != source_id:
            raise RuntimeError("BASS_NOTE_PROVENANCE_MISMATCH")
        if not 0 <= float(note["local_onset_s"]) < 7.68 or float(note["duration_s"]) <= 0:
            raise RuntimeError("BASS_NOTE_OUTSIDE_REGION")
        expected.append(MidiNote(
            pitch=int(note["midi_note"]), start_time=float(note["local_onset_s"]) * 125 / 60,
            duration=float(note["duration_s"]) * 125 / 60,
            velocity=100,
        ))
    if not expected or len(expected) > 512:
        raise RuntimeError("BASS_NOTE_COUNT_OUT_OF_BOUNDS")
    daw = AbletonTcpAdapter()
    daw.connect()
    try:
        if not paths_match(daw.get_session_path().get("path"), working_als):
            raise RuntimeError("PROJECT_MISMATCH")
        journal_root.mkdir(parents=True, exist_ok=True)
        executor = build_safe_write_executor(
            daw, journal_path=journal_root / "safe-write.jsonl", persist_dir=journal_root / "prestate"
        )
        unresolved = [row for row in executor.recover() if row["recovery"] != "VERIFIED"]
        if unresolved:
            raise RuntimeError(f"UNRESOLVED_SAFEWRITE_JOURNAL: {unresolved}")
        session = executor.tools.get_session_snapshot()
        attach_tokens(session)
        track = session.track_by_name(track_name)
        if track is None:
            virtual = TrackState(stable_id="", index=-1, name=track_name, role="midi")
            actions = [
                build_create_track_action(project_identity=session.project_identity or "", track_name=track_name,
                                          track_kind="midi", reason="FAST_LAB inferred bass lane", evidence_refs=[source_id]),
                build_pattern_action(track=virtual, project_identity=session.project_identity or "", clip_index=0,
                                     length_beats=16.0, notes=expected, reason="FAST_LAB inferred bass timing and pitch",
                                     evidence_refs=[source_id]),
            ]
            pattern = _apply(executor, _plan(session, actions, source_id, f"{track_name}_pattern"), session)
            session = executor.tools.get_session_snapshot()
            attach_tokens(session)
            track = session.track_by_name(track_name)
        else:
            pattern = {"ok": True, "reused": True}
        if track is None:
            raise RuntimeError("BASS_TRACK_READBACK_MISSING")
        if track.devices:
            if not any(device.name == instrument_name for device in track.devices):
                raise RuntimeError("UNEXPECTED_BASS_DEVICE")
            device_result = {"ok": True, "reused": True}
        else:
            search = daw.search_browser(instrument_name, "instruments")
            choices = [row for row in search.get("results", []) if row.get("name") == instrument_name and row.get("is_loadable")]
            if len(choices) != 1:
                raise RuntimeError(f"INSTRUMENT_NOT_UNAMBIGUOUS: {search}")
            action = build_device_load_action(
                track=track, project_identity=session.project_identity or "", device_name=instrument_name,
                device_uri=choices[0]["uri"], reason="FAST_LAB stock bass instrument",
                evidence_refs=[source_id], session_incarnation_id=session.session_incarnation_id or "",
            )
            device_result = _apply(executor, _plan(session, [action], source_id, f"{track_name}_instrument", target_track=track), session)
        fresh = executor.tools.get_session_snapshot()
        observed = fresh.track_by_name(track_name)
        if observed is None:
            raise RuntimeError("BASS_TRACK_LOST")
        readback = daw.get_clip_notes(observed.index, 0)
        actual = readback.get("notes", [])
        exact = len(actual) == len(expected) and all(
            int(row["pitch"]) == note.pitch
            and abs(float(row["start_time"]) - note.start_time) < 1e-5
            and abs(float(row["duration"]) - note.duration) < 1e-5
            and int(row["velocity"]) == note.velocity
            for row, note in zip(actual, expected)
        )
        report = {
            "status": "PASS" if exact and pattern["ok"] and device_result["ok"] else "FAILED",
            "working_als": str(working_als), "track_name": track_name, "track_index": observed.index,
            "evidence": str(evidence_path), "source_sha256": source_id,
            "notes_requested": len(expected), "notes_readback": len(actual), "readback_exact": exact,
            "instrument": [(item.name, item.index) for item in observed.devices],
            "pattern_safe_write": pattern, "instrument_safe_write": device_result,
            "instrument_requested": instrument_name,
        }
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if report["status"] != "PASS":
            raise RuntimeError(f"BASS_READBACK_FAILED: {report}")
        return report
    finally:
        daw.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("working_als", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("journal_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--track-name", default="LAB_BASS")
    parser.add_argument("--instrument", default="Operator")
    args = parser.parse_args()
    print(json.dumps(run(args.working_als, args.evidence, args.journal_root, args.output,
                         track_name=args.track_name, instrument_name=args.instrument), indent=2))
