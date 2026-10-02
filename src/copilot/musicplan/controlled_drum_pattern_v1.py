"""Build a bounded SafeWrite MusicPlan from an existing drum reconstruction."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

from copilot.musicplan.drum_reconstruction_v1 import (
    DRUM_RECONSTRUCTION_VERSION,
    DRUM_TRIGGER_DURATION_QN,
    DrumReconstructionV1,
)
from copilot.schemas.musicplan import (
    ControlledDrumMidiNote,
    ControlledDrumPatternActionParams,
    DiagnosisBinding,
    ExpectedEffect,
    MusicPlan,
    PlanAction,
    PlanIntentClass,
    PlanStatus,
    ProductionActionKind,
)
from copilot.schemas.session import SessionState


def _sha256(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_controlled_drum_pattern_plan(
    reconstruction: DrumReconstructionV1,
    *,
    session: SessionState,
    meter_authority: str,
) -> MusicPlan:
    """Map the existing 4-bar event proposal; never invent roles or velocity."""
    if reconstruction.schema_version != DRUM_RECONSTRUCTION_VERSION:
        raise ValueError("unsupported drum reconstruction version")
    if reconstruction.deferred_source_event_ids:
        raise ValueError("unmapped source events prevent controlled MIDI realization")
    if meter_authority not in {"HUMAN_VERIFIED", "ASSUMED"}:
        raise ValueError("meter authority must remain explicitly labeled")
    if session.transport.tempo != 125.0:
        raise ValueError("human-confirmed 125 BPM is required for this proof")
    if (session.transport.signature_numerator, session.transport.signature_denominator) != (4, 4):
        raise ValueError("4/4 Live grid required for the four-bar proof")

    region_start = float(reconstruction.source_region_seconds["start"])
    region_end = float(reconstruction.source_region_seconds["end"])
    if not (math.isfinite(region_start) and math.isfinite(region_end) and region_end > region_start):
        raise ValueError("source region must be finite and non-empty")
    if len(reconstruction.events) != 32:
        raise ValueError("this bounded proof accepts exactly the existing 32-event reconstruction")

    notes: list[ControlledDrumMidiNote] = []
    for event in reconstruction.events:
        if event.velocity is None:
            raise ValueError(f"event {event.source_event_id} has no derived accent velocity")
        if not math.isfinite(event.onset_qn) or not 0 <= event.onset_qn < 16:
            raise ValueError(f"event {event.source_event_id} falls outside the four-bar clip")
        if event.tempo_status not in {"HUMAN_VERIFIED", "VERIFIED"} or event.tempo_bpm != 125.0:
            raise ValueError("event tempo must retain the human-confirmed 125 BPM grid")
        if event.note_duration_qn != DRUM_TRIGGER_DURATION_QN:
            raise ValueError("drum trigger gate policy is missing or altered")
        if event.role_status not in {"HUMAN_VERIFIED", "INFERRED", "INFERRED_PROVISIONAL"}:
            raise ValueError(f"unsupported role authority for {event.source_event_id}: {event.role_status}")
        authority = "HUMAN_VERIFIED" if event.role_status == "HUMAN_VERIFIED" else "INFERRED_PROVISIONAL"
        notes.append(ControlledDrumMidiNote(
            source_event_id=event.source_event_id,
            source_role=event.role,
            source_role_authority=authority,
            pitch=event.midi_note,
            start_qn=float(event.onset_qn),
            duration_qn=DRUM_TRIGGER_DURATION_QN,
            velocity=int(event.velocity),
        ))

    reconstruction_payload = [note.model_dump(mode="json") for note in notes]
    reconstruction_sha = _sha256({
        "version": reconstruction.schema_version,
        "source_asset_id": reconstruction.source_asset_id,
        "source_sha256": reconstruction.source_sha256,
        "region_seconds": reconstruction.source_region_seconds,
        "notes": reconstruction_payload,
    })
    ownership_key = _sha256({
        "project_identity": session.project_identity,
        "target_track": "MP_DRUM_RECON_V1",
        "target_clip": "MP_DRUM_RECON_4BAR_V1",
        "source_sha256": reconstruction.source_sha256,
        "region_seconds": reconstruction.source_region_seconds,
    })
    params = ControlledDrumPatternActionParams(
        reconstruction_version=reconstruction.schema_version,
        source_asset_id=reconstruction.source_asset_id,
        source_sha256=reconstruction.source_sha256,
        reconstruction_sha256=reconstruction_sha,
        ownership_key=ownership_key,
        meter_authority=meter_authority,
        region_start_seconds=region_start,
        region_end_seconds=region_end,
        notes=notes,
    )
    return MusicPlan(
        plan_id=f"drum_recon_{reconstruction_sha[:16]}",
        status=PlanStatus.READY_FOR_EXECUTION,
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        diagnosis=DiagnosisBinding(
            diagnosis_id=f"drum-reconstruction:{reconstruction.source_asset_id}",
            diagnosis_status="EXISTING_SYMBOLIC_RECONSTRUCTION",
            diagnosis_accepted=True,
        ),
        project_state_token=session.project_token or "",
        audible_state_token=session.audible_token or "",
        evidence_refs=[reconstruction.source_asset_id, reconstruction.source_sha256],
        actions=[PlanAction(
            action_id="realize_controlled_drum_pattern",
            action_type=ProductionActionKind.REALIZE_CONTROLLED_MIDI_PATTERN,
            target={"ref": {"object_type": "track", "project_identity": session.project_identity or "",
                             "role": "midi", "name": "MP_DRUM_RECON_V1"}},
            params=params,
            reason="Realize the existing 4-bar drum reconstruction as a controlled editable MIDI proof.",
            evidence_refs=[reconstruction.source_asset_id, reconstruction.source_sha256],
            expected_effect=ExpectedEffect(
                affected_target="MP_DRUM_RECON_V1/MP_DRUM_RECON_4BAR_V1",
                direction="CREATE_CONTROLLED_MIDI_STATE",
                description="Create the exact bounded MIDI note set with source-event lineage; no audio is implied.",
                measurement_to_compare_after="authoritative Live clip and full note readback",
            ),
        )],
        created_at=datetime.now(timezone.utc).isoformat(),
        notes=[f"Meter authority: {meter_authority}; role authority preserved per event."],
    )


__all__ = ["build_controlled_drum_pattern_plan"]
