"""Bind the existing deterministic analysis to the current clean Live copy.

This is an operational, read-only preparation step for the creative vertical
slice. It never writes to Ableton. The generated JSON artifacts live inside
the controlled working copy so the producer boundary can verify provenance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from copilot.audio.musical_understanding_v1 import analyze_musical_understanding
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.object_ref import ref_from_track
from copilot.integration.reference_variation_v1 import validate_midi_reference
from copilot.schemas.music_analysis import MusicAnalysisPack
from copilot.schemas.musical_understanding import MusicalUnderstanding


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-als", required=True, type=Path)
    parser.add_argument("--source-pack", required=True, type=Path)
    parser.add_argument("--stem-analysis", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    working_als = args.working_als.resolve()
    source_pack_path = args.source_pack.resolve()
    stem_analysis_path = args.stem_analysis.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        session = adapter.snapshot(include_notes=True)
    finally:
        adapter.disconnect()

    actual_path = Path(str(session.project_path or "")).resolve()
    if actual_path != working_als:
        raise RuntimeError(f"CLEAN_WORKING_COPY_MISMATCH: {actual_path} != {working_als}")
    track = next((item for item in session.tracks if item.name == "Rose Bass"), None)
    if track is None:
        raise RuntimeError("ROSE_BASS_TRACK_NOT_FOUND")

    source_pack = MusicAnalysisPack.model_validate_json(
        source_pack_path.read_text(encoding="utf-8")
    )
    reference_id = source_pack.reference_id or "ableton_reference_to_variation_v1_rose_bass"
    pack_stem = "reference_to_variation_v1_clean_bass_pack"
    pack_path = output_dir / f"{pack_stem}.json"
    midi_pack_path = output_dir / "midi_source_clean_rose_bass.json"
    understanding_path = output_dir / "musical_understanding_clean_bass.json"
    reconciliation_path = output_dir / "midi_source_reconciliation_clean_bass.json"

    persistent_ref = ref_from_track(track, project_identity=session.project_identity)
    source_ref = {
        "kind": "ABLETON_REFERENCE_MIDI_READ_ONLY",
        "project_path": str(working_als),
        "project_identity": session.project_identity,
        "project_token": session.project_token,
        "audible_token": session.audible_token,
        "track_index": track.index,
        "track_name": track.name,
        "track_stable_id": track.stable_id,
        "persistent_track_ref": persistent_ref.model_dump(mode="json"),
        "arrangement_clips": [
            {
                "id": f"arr:{track.index}:160.000000000:192.000000000:{track.name}",
                "track_index": track.index,
                "name": track.name,
                "start_time": 160.0,
                "end_time": 192.0,
                "length": 32.0,
            }
        ],
        "capture_path": None,
        "identity_timing_authority": "ABLETON_SESSION_READBACK",
    }
    timeline = {
        "tempo_bpm": float(session.transport.tempo),
        "meter": {"numerator": 4, "denominator": 4},
        "bar_start": 41,
        "bar_end": 49,
        "start_qn": 160.0,
        "end_qn": 192.0,
        "start_s": 0.0,
        "end_s": 32.0 * 60.0 / float(session.transport.tempo),
        "duration_s": 32.0 * 60.0 / float(session.transport.tempo),
        "timing_source": "ABLETON_AUTHORITATIVE_SESSION_TEMPO_AND_MIDI_REGION",
        "status": "AUTHORITATIVE_MIDI_REGION",
    }
    pack = source_pack.model_copy(
        update={
            "project_id": session.project_identity,
            "reference_id": reference_id,
            "tokens": source_pack.tokens.model_copy(
                update={
                    "project_identity": session.project_identity,
                    "project_token": session.project_token,
                    "audible_token": session.audible_token,
                }
            ),
            "source_ref": source_ref,
            "timeline": timeline,
            "tempo_bpm": float(session.transport.tempo),
            "provenance": {
                **source_pack.provenance,
                "rebound_from": str(source_pack_path),
                "rebound_reason": "clean_working_copy_authoritative_midi",
                "audio_measurements_reused_from": "prior_read_only_pack_not_used_as_identity_authority",
                "project_identity": session.project_identity,
                "project_path": str(working_als),
                "musical_writes": 0,
                "model_api_calls": 0,
            },
            "limitations": [
                *source_pack.limitations,
                "REFERENCE_AUDIO_MEASUREMENTS_ARE_NOT_REBOUND_TO_THIS_COPY",
                "CREATIVE_INPUT_IS_AUTHORITATIVE_MIDI_FROM_CURRENT_CLEAN_WORKING_COPY",
            ],
            "metadata": {
                **source_pack.metadata,
                "clean_working_copy_rebound": True,
                "source_project_identity": session.project_identity,
            },
        }
    )
    pack_path.write_text(pack.model_dump_json(indent=2), encoding="utf-8")

    midi_pack = {
        "schema_version": "midi-source-read-only-v1",
        "status": "AUTHORITATIVE_MIDI_SOURCE",
        "reference_id": reference_id,
        "timeline": timeline,
        "source_ref": source_ref,
        "no_write": True,
        "musical_writes": 0,
        "model_api_calls": 0,
    }
    midi_pack_path.write_text(json.dumps(midi_pack, indent=2), encoding="utf-8")

    understanding = analyze_musical_understanding(
        stem_analysis_path,
        midi_pack_path=midi_pack_path,
        midi_reconciliation_path=reconciliation_path,
        output_path=understanding_path,
    )
    understanding = understanding.model_copy(
        update={
            "reference_id": reference_id,
            "source_analysis_id": pack_stem,
            "timeline": timeline,
            "tempo_bpm": float(session.transport.tempo),
            "provenance": {
                **understanding.provenance,
                # The producer contract binds this field to the analysis pack
                # path; retain the lower-level source pack separately.
                "midi_pack_path": str(pack_path),
                "midi_source_pack_path": str(midi_pack_path),
                "clean_working_copy": str(working_als),
                "project_identity": session.project_identity,
                "model_api_calls": 0,
                "musical_writes": 0,
            },
        }
    )
    understanding_path.write_text(understanding.model_dump_json(indent=2), encoding="utf-8")
    validate_midi_reference(
        pack,
        understanding,
        pack_path=pack_path,
        understanding_path=understanding_path,
        project_identity=session.project_identity,
    )

    print(json.dumps({
        "status": "READY",
        "project_identity": session.project_identity,
        "project_path": str(working_als),
        "track": {"name": track.name, "index": track.index, "stable_id": track.stable_id},
        "midi_events": len(understanding.bass.pitch_events),
        "midi_status": understanding.bass.source_diagnostics.get("status"),
        "reconciliation": understanding.bass.source_diagnostics.get("reconciliation"),
        "pack": str(pack_path),
        "midi_pack": str(midi_pack_path),
        "understanding": str(understanding_path),
        "reconciliation_artifact": str(reconciliation_path),
        "model_api_calls": 0,
        "musical_writes": 0,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
