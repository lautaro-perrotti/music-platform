from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.musicplan.controlled_drum_pattern_v1 import build_controlled_drum_pattern_plan
from copilot.musicplan.drum_reconstruction_v1 import (
    DRUM_RECONSTRUCTION_VERSION,
    DRUM_TRIGGER_DURATION_QN,
    DrumReconstructionEventV1,
    DrumReconstructionV1,
)
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.safe_write import ApplyDecision, MutationFailure
from copilot.schemas.musicplan import ControlledDrumMidiNote, ControlledDrumPatternActionParams


def _working_copy(tmp_path: Path) -> Path:
    root = tmp_path / "controlled-project"
    root.mkdir()
    als = root / "working.als"
    als.write_bytes(b"test working copy")
    (root / "copilot_import.json").write_text(json.dumps({
        "working_als": str(als), "ORIGINAL_UNTOUCHED": True,
    }), encoding="utf-8")
    return als


def _fixture() -> DrumReconstructionV1:
    source_hash = hashlib.sha256(b"immutable drum source").hexdigest()
    events = []
    for index in range(32):
        role = "KICK" if index % 2 == 0 else "CLOSED_HAT"
        events.append(DrumReconstructionEventV1(
            event_id=f"recon_evt_{index:02d}", source_event_id=f"source_evt_{index:02d}",
            role=role, role_status="INFERRED", midi_note=36 if role == "KICK" else 42,
            onset_qn=index * 0.5, bar=index // 8 + 1, beat_in_bar=(index % 8) / 2 + 1,
            subdivision="EIGHTH", observed_onset_seconds=index * 0.24, tempo_bpm=125.0,
            tempo_status="HUMAN_VERIFIED", micro_offset_ms=0.0,
            accent_rms_dbfs=-12.0 - index / 10, accent_normalized=0.5,
            velocity=70 + index, velocity_status="DERIVED_ROLE_RELATIVE_P10_P90_CLAMPED",
            note_duration_qn=DRUM_TRIGGER_DURATION_QN,
            note_duration_status="SYMBOLIC_TRIGGER_GATE_1_32_QN_NOT_SOURCE_DURATION",
            selected_sample_asset_id=None,
        ))
    return DrumReconstructionV1(
        schema_version=DRUM_RECONSTRUCTION_VERSION, status="SYMBOLIC_PROPOSAL_NOT_EXECUTABLE",
        source_asset_id="drum-source-fixture", source_sha256=source_hash,
        source_region_seconds={"start": 0.0, "end": 7.68}, velocity_mapping={"method": "fixture"},
        events=events, deferred_source_event_ids=[], blockers=["SAMPLE_SELECTION_REQUIRED"],
    )


def _ready(tmp_path: Path, *, strict_caps: bool = False):
    daw = MockAbletonAdapter()
    daw.connect()
    daw.session_path = str(_working_copy(tmp_path))
    daw.transport.tempo = 125.0
    if strict_caps:
        daw.strict_capabilities = True
        daw.capabilities = {
            "track.create_midi", "track.delete", "clip.create", "clip.delete",
            "clip.rename", "clip.write_notes", "clip.read_notes",
        }
    executor = build_safe_write_executor(
        daw, journal_path=tmp_path / "safe-write.jsonl", persist_dir=tmp_path / "prestate"
    )
    session = executor.tools.get_session_snapshot()
    attach_tokens(session)
    plan = build_controlled_drum_pattern_plan(
        _fixture(), session=session, meter_authority="ASSUMED"
    )
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED", compiled.reasons
    return daw, executor, plan, compiled


def test_controlled_drum_reconstruction_compiles_and_verifies_exact_32_notes(tmp_path):
    daw, executor, _plan, compiled = _ready(tmp_path, strict_caps=True)
    result = executor.run(compiled.intent)
    assert result.ok, result.to_dict()
    assert result.failure is None
    assert result.musical_writes == 1
    assert result.readbacks[0].authoritative and result.readbacks[0].matched
    assert result.intent.kind == "SAFEWRITE_CONTROLLED_MIDI_V1"
    snap = daw.snapshot()
    track = snap.track_by_name("MP_DRUM_RECON_V1")
    assert track is not None and track.role == "midi"
    clip = next(item for item in track.clips if item.slot_index == 0)
    assert clip.name == "MP_DRUM_RECON_4BAR_V1"
    assert clip.length_beats == pytest.approx(16.0)
    assert len(clip.notes) == 32
    assert sum(note.pitch == 36 for note in clip.notes) == 16
    assert sum(note.pitch == 42 for note in clip.notes) == 16


def test_repeat_same_plan_is_journal_proven_idempotent_noop(tmp_path):
    daw, executor, _, compiled = _ready(tmp_path)
    first = executor.run(compiled.intent)
    assert first.ok
    session = executor.tools.get_session_snapshot()
    attach_tokens(session)
    plan = build_controlled_drum_pattern_plan(_fixture(), session=session, meter_authority="ASSUMED")
    repeated = ProductionCompiler().compile(plan, session=session)
    second = executor.run(repeated.intent)
    assert second.ok, second.to_dict()
    assert second.recovery["IDEMPOTENT_REPLAY"] is True
    assert second.musical_writes == 0
    assert len([track for track in daw.snapshot().tracks if track.name == "MP_DRUM_RECON_V1"]) == 1


def test_unowned_reserved_name_fails_closed(tmp_path):
    daw, executor, _, compiled = _ready(tmp_path)
    daw.create_midi_track("MP_DRUM_RECON_V1")
    daw.create_midi_clip(0, 0, 16.0)
    daw.set_clip_name(0, 0, "MP_DRUM_RECON_4BAR_V1")
    session = executor.tools.get_session_snapshot()
    attach_tokens(session)
    fresh = ProductionCompiler().compile(
        build_controlled_drum_pattern_plan(_fixture(), session=session, meter_authority="ASSUMED"),
        session=session,
    )
    result = executor.run(fresh.intent)
    assert result.failure is MutationFailure.TARGET_NOT_PLATFORM_OWNED
    assert len(daw.snapshot().tracks) == 1


def test_owned_clip_replaces_whole_note_set_and_can_rollback(tmp_path):
    daw, executor, _, compiled = _ready(tmp_path)
    first = executor.run(compiled.intent)
    assert first.ok
    before = daw.snapshot().track_by_name("MP_DRUM_RECON_V1").clips[0].notes
    reconstruction = _fixture()
    reconstruction.events[0].velocity += 1
    session = executor.tools.get_session_snapshot()
    attach_tokens(session)
    plan = build_controlled_drum_pattern_plan(reconstruction, session=session, meter_authority="ASSUMED")
    replacement = ProductionCompiler().compile(plan, session=session)
    assert replacement.status == "COMPILED", replacement.reasons
    assert replacement.intent.executions[0].action_type == "REPLACE_CONTROLLED_MIDI_PATTERN"
    result = executor.run(replacement.intent.model_copy(update={"decision_after_verify": ApplyDecision.ROLLBACK}))
    assert result.failure is None, result.to_dict()
    restored = daw.snapshot().track_by_name("MP_DRUM_RECON_V1").clips[0].notes
    assert [item.model_dump() for item in restored] == [item.model_dump() for item in before]


def test_missing_negotiated_capability_fails_before_any_write(tmp_path):
    daw, executor, _, compiled = _ready(tmp_path, strict_caps=True)
    daw.capabilities.remove("clip.read_notes")
    result = executor.run(compiled.intent)
    assert result.failure is MutationFailure.CAPABILITY_UNSUPPORTED
    assert daw.snapshot().tracks == []


def test_malformed_lineage_is_typed_validation_failure_before_create(tmp_path):
    daw, executor, _, compiled = _ready(tmp_path)
    malformed = compiled.intent.model_copy(deep=True)
    malformed.executions[0].arguments["note_lineage"][0] = None
    result = executor.run(malformed)
    assert result.failure is MutationFailure.VALIDATION_FAILED
    assert daw.snapshot().tracks == []


def test_malformed_lineage_is_typed_validation_failure_before_replace(tmp_path):
    daw, executor, _, compiled = _ready(tmp_path)
    assert executor.run(compiled.intent).ok
    reconstruction = _fixture()
    reconstruction.events[0].velocity += 1
    session = executor.tools.get_session_snapshot()
    attach_tokens(session)
    plan = build_controlled_drum_pattern_plan(reconstruction, session=session, meter_authority="ASSUMED")
    replacement = ProductionCompiler().compile(plan, session=session)
    assert replacement.status == "COMPILED"
    malformed = replacement.intent.model_copy(deep=True)
    malformed.executions[0].arguments["note_lineage"][0]["source_role"] = "CLOSED_HAT"
    before = [item.model_dump() for item in daw.snapshot().track_by_name("MP_DRUM_RECON_V1").clips[0].notes]
    result = executor.run(malformed)
    assert result.failure is MutationFailure.VALIDATION_FAILED
    after = [item.model_dump() for item in daw.snapshot().track_by_name("MP_DRUM_RECON_V1").clips[0].notes]
    assert after == before


@pytest.mark.parametrize("mismatch", ["count", "pitch", "timing", "velocity"])
def test_exact_note_readback_mismatch_rolls_back_created_proof_track(tmp_path, mismatch):
    daw, executor, _, compiled = _ready(tmp_path)
    original = daw.get_clip_notes

    def mismatched(track_index: int, clip_index: int):
        result = original(track_index, clip_index)
        if mismatch == "count":
            result["notes"].pop()
            result["note_count"] -= 1
        elif mismatch == "pitch":
            result["notes"][0]["pitch"] += 1
        elif mismatch == "timing":
            result["notes"][0]["start_time"] += 0.01
        else:
            result["notes"][0]["velocity"] += 1
        return result

    daw.get_clip_notes = mismatched
    result = executor.run(compiled.intent)
    assert result.failure is MutationFailure.READBACK_MISMATCH
    assert daw.snapshot().track_by_name("MP_DRUM_RECON_V1") is None


def test_stale_project_and_state_trust_rejected_before_write(tmp_path):
    daw, executor, _, compiled = _ready(tmp_path)
    stale_token = compiled.intent.model_copy(update={"expected_project_token": "stale-token"})
    result = executor.run(stale_token)
    assert result.failure is MutationFailure.STALE_STATE
    assert daw.snapshot().tracks == []
    wrong_project = compiled.intent.model_copy(update={"project_identity": "different-project"})
    result = executor.run(wrong_project)
    assert result.failure is MutationFailure.PROJECT_MISMATCH
    assert daw.snapshot().tracks == []


def test_controlled_note_schema_rejects_invalid_bounds_and_nonfinite_values():
    base = {
        "source_event_id": "event-1", "source_role": "KICK",
        "source_role_authority": "INFERRED_PROVISIONAL", "pitch": 36,
        "start_qn": 0.0, "duration_qn": 1 / 32, "velocity": 100,
    }
    for updates in ({"pitch": 200}, {"velocity": 128}, {"duration_qn": 0}, {"start_qn": float("nan")}):
        with pytest.raises(Exception):
            ControlledDrumMidiNote(**(base | updates))

    note = ControlledDrumMidiNote(**base)
    common = {
        "reconstruction_version": DRUM_RECONSTRUCTION_VERSION,
        "source_asset_id": "source",
        "source_sha256": hashlib.sha256(b"source").hexdigest(),
        "reconstruction_sha256": hashlib.sha256(b"recon").hexdigest(),
        "ownership_key": hashlib.sha256(b"owner").hexdigest(),
        "meter_authority": "ASSUMED", "region_start_seconds": 0.0, "region_end_seconds": 7.68,
    }
    with pytest.raises(Exception):
        ControlledDrumPatternActionParams(**(common | {"notes": [note.model_dump()] * 513}))


def test_unmanifested_or_original_project_is_rejected(tmp_path):
    daw, executor, _, compiled = _ready(tmp_path)
    (Path(daw.session_path).parent / "copilot_import.json").unlink()
    result = executor.run(compiled.intent)
    assert result.failure is MutationFailure.PRECONDITION_FAILED
    assert result.error == "CONTROLLED_WORKING_COPY_REQUIRED"
    assert daw.snapshot().tracks == []
