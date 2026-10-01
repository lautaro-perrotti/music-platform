"""Provider-neutral symbolic drum reconstruction proposal.

The proposal preserves reference evidence and does not claim that timing,
roles, MIDI velocity, samples, or Ableton state have been verified.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from pydantic import BaseModel, Field

from copilot.audio.drum_events_v1 import DrumEventSetV1, DrumEventV1


DRUM_RECONSTRUCTION_VERSION = "drum-reconstruction-v1"
PROOF_MAPPING_ID = "local-drum-proof-map-v1"
MIDI_NOTE_BY_ROLE = {"KICK": 36, "CLOSED_HAT": 42}


class DrumReconstructionEventV1(BaseModel):
    event_id: str
    source_event_id: str
    role: str
    role_status: str
    midi_note: int
    onset_qn: float
    bar: int
    beat_in_bar: float
    subdivision: str
    observed_onset_seconds: float
    tempo_bpm: float
    tempo_status: str
    micro_offset_ms: float
    accent_rms_dbfs: float | None
    accent_normalized: float | None
    velocity: int | None = Field(default=None, ge=1, le=127)
    velocity_status: str
    note_duration_qn: float | None = None
    note_duration_status: str = "NOT_DERIVED"
    selected_sample_asset_id: str | None
    ableton_status: str = "NOT_WRITTEN"


class DrumReconstructionV1(BaseModel):
    schema_version: str = DRUM_RECONSTRUCTION_VERSION
    status: str
    source_asset_id: str
    source_sha256: str
    source_region_seconds: dict[str, float]
    proof_mapping_id: str = PROOF_MAPPING_ID
    midi_note_mapping: dict[str, int] = Field(default_factory=lambda: dict(MIDI_NOTE_BY_ROLE))
    velocity_mapping: dict[str, Any]
    events: list[DrumReconstructionEventV1]
    deferred_source_event_ids: list[str]
    blockers: list[str]
    musical_writes: int = 0


def _role_velocity_ranges(events: list[DrumEventV1]) -> dict[str, tuple[float, float]]:
    ranges: dict[str, tuple[float, float]] = {}
    for role in MIDI_NOTE_BY_ROLE:
        values = sorted(
            event.accent_rms_dbfs for event in events
            if event.effective_role == role and event.accent_rms_dbfs is not None
            and math.isfinite(event.accent_rms_dbfs)
        )
        if values:
            ranges[role] = (
                float(statistics.quantiles(values, n=10, method="inclusive")[0]) if len(values) > 1 else values[0],
                float(statistics.quantiles(values, n=10, method="inclusive")[-1]) if len(values) > 1 else values[0],
            )
    return ranges


def _velocity(accent_dbfs: float | None, bounds: tuple[float, float] | None) -> tuple[float | None, int | None, str]:
    if accent_dbfs is None or bounds is None or not math.isfinite(accent_dbfs):
        return None, None, "UNAVAILABLE_NO_VALID_ACCENT_MEASUREMENT"
    low, high = bounds
    if high - low < 1.0:
        return 0.5, 64, "DERIVED_CENTERED_LOW_ACCENT_SPREAD"
    normalized = min(1.0, max(0.0, (accent_dbfs - low) / (high - low)))
    velocity = min(127, max(1, round(1 + normalized * 126)))
    return normalized, velocity, "DERIVED_ROLE_RELATIVE_P10_P90_CLAMPED"


def build_drum_reconstruction(event_set: DrumEventSetV1) -> DrumReconstructionV1:
    """Translate supported event roles to an editable symbolic proposal only."""
    ranges = _role_velocity_ranges(event_set.events)
    mapped: list[DrumReconstructionEventV1] = []
    deferred: list[str] = []
    for event in event_set.events:
        role = event.effective_role
        midi_note = MIDI_NOTE_BY_ROLE.get(role)
        if midi_note is None:
            deferred.append(event.event_id)
            continue
        normalized, velocity, velocity_status = _velocity(event.accent_rms_dbfs, ranges.get(role))
        position = event.musical_position
        mapped.append(DrumReconstructionEventV1(
            event_id=f"recon_{event.event_id}",
            source_event_id=event.event_id,
            role=role,
            role_status="HUMAN_VERIFIED" if event.human_correction else event.role_hypothesis.status,
            midi_note=midi_note,
            onset_qn=position.onset_qn,
            bar=position.bar,
            beat_in_bar=position.beat_in_bar,
            subdivision=position.subdivision,
            observed_onset_seconds=event.onset_seconds,
            tempo_bpm=position.tempo_bpm,
            tempo_status=position.tempo_status,
            micro_offset_ms=position.micro_offset_ms,
            accent_rms_dbfs=event.accent_rms_dbfs,
            accent_normalized=normalized,
            velocity=velocity,
            velocity_status=velocity_status,
            note_duration_qn=None,
            note_duration_status="NOT_DERIVED_FROM_ONSET_EVIDENCE",
            selected_sample_asset_id=event.selected_sample_asset_id,
        ))

    blockers: list[str] = []
    if any(event.selected_sample_asset_id is None for event in mapped):
        blockers.append("SAMPLE_SELECTION_REQUIRED")
    if any(event.role_status != "HUMAN_VERIFIED" for event in mapped):
        blockers.append("ROLE_HYPOTHESES_NOT_HUMAN_VERIFIED")
    if event_set.tempo_status not in {"HUMAN_VERIFIED", "VERIFIED"} or event_set.meter_status != "HUMAN_VERIFIED":
        blockers.append("MUSICAL_GRID_NOT_HUMAN_VERIFIED")
    blockers.append("MIDI_NOTE_DURATION_POLICY_NOT_DEFINED")
    if deferred:
        blockers.append("UNMAPPED_SOURCE_EVENTS_PRESENT")
    blockers.append("SAFEWRITE_AND_LIVE_READBACK_NOT_PERFORMED")

    return DrumReconstructionV1(
        status="SYMBOLIC_PROPOSAL_NOT_EXECUTABLE",
        source_asset_id=event_set.source_asset_id,
        source_sha256=event_set.source_sha256,
        source_region_seconds={"start": event_set.region_start_seconds, "end": event_set.region_end_seconds},
        velocity_mapping={
            "method": "per-role accent dBFS percentile normalization; p10→1, p90→127, clamped",
            "source_measurement": "accent_rms_dbfs; remains preserved and is not MIDI velocity",
            "role_bounds_dbfs": {role: {"p10": low, "p90": high} for role, (low, high) in ranges.items()},
            "low_spread_fallback": "velocity 64 when role-local p90-p10 spread is under 1 dB",
        },
        events=mapped,
        deferred_source_event_ids=deferred,
        blockers=list(dict.fromkeys(blockers)),
    )


__all__ = [
    "DRUM_RECONSTRUCTION_VERSION",
    "DrumReconstructionEventV1",
    "DrumReconstructionV1",
    "build_drum_reconstruction",
]
