"""Real controlled drum proof on an already-ready disposable Live set."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from copilot.audio.drum_events_v1 import DrumEventSetV1
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.importing.ableton_launcher_v1 import paths_match
from copilot.importing.working_copy_manager_v1 import is_copilot_working_copy
from copilot.musicplan.controlled_drum_pattern_v1 import build_controlled_drum_pattern_plan
from copilot.musicplan.drum_reconstruction_v1 import build_drum_reconstruction
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor


def run(working_als: Path, event_path: Path, journal_root: Path) -> dict:
    if not is_copilot_working_copy(working_als):
        raise RuntimeError("WORKING_COPY_REQUIRED")
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    source = Path(str(payload["source_path"]))
    if hashlib.sha256(source.read_bytes()).hexdigest() != payload["source_sha256"]:
        raise RuntimeError("SOURCE_HASH_MISMATCH")
    reconstruction = build_drum_reconstruction(DrumEventSetV1.model_validate(payload))
    if len(reconstruction.events) != 32:
        raise RuntimeError("NOT_32_EVENTS")

    daw = AbletonTcpAdapter()
    daw.connect()
    try:
        path = daw.get_session_path().get("path")
        if not paths_match(path, working_als):
            raise RuntimeError(f"PROJECT_MISMATCH: {path}")
        journal_root.mkdir(parents=True, exist_ok=True)
        executor = build_safe_write_executor(
            daw,
            journal_path=journal_root / "safe-write.jsonl",
            persist_dir=journal_root / "prestate",
        )
        results = []
        for _ in range(2):
            session = executor.tools.get_session_snapshot()
            attach_tokens(session)
            plan = build_controlled_drum_pattern_plan(
                reconstruction, session=session, meter_authority="ASSUMED"
            )
            compiled = ProductionCompiler().compile(plan, session=session)
            if compiled.status != "COMPILED" or compiled.intent is None:
                raise RuntimeError(f"COMPILE_FAILED: {compiled.reasons}")
            result = executor.run(compiled.intent)
            results.append({
                "ok": result.ok,
                "failure": str(result.failure) if result.failure else None,
                "decision": str(result.decision),
                "musical_writes": result.musical_writes,
                "journal_terminal_state": result.journal_terminal_state,
                "idempotent_replay": result.recovery.get("IDEMPOTENT_REPLAY"),
                "readback_matched": all(row.authoritative and row.matched for row in result.readbacks),
                "error": result.error,
            })
            if not result.ok:
                break
        final_session = executor.tools.get_session_snapshot()
        track = final_session.track_by_name("MP_DRUM_RECON_V1")
        observed = daw.get_clip_notes(track.index, 0) if track is not None else {}
        return {
            "status": "PASS" if len(results) == 2 and all(row["ok"] for row in results)
            and observed.get("note_count") == 32 else "FAILED",
            "working_als": str(working_als),
            "event_artifact": str(event_path),
            "tempo_bpm": final_session.transport.tempo,
            "meter": [final_session.transport.signature_numerator, final_session.transport.signature_denominator],
            "results": results,
            "track_index": track.index if track is not None else None,
            "clip_readback_note_count": observed.get("note_count"),
            "clip_readback_name": observed.get("clip_name"),
            "journal": str(journal_root / "safe-write.jsonl"),
        }
    finally:
        daw.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("working_als", type=Path)
    parser.add_argument("event_artifact", type=Path)
    parser.add_argument("journal_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.working_als, args.event_artifact, args.journal_root), indent=2))


if __name__ == "__main__":
    main()
