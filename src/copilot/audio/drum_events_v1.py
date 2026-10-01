"""Evidence-linked drum transient events for a bounded source region.

This layer reuses the existing RMS-flux transient detector.  It records
observations and a conditional musical-grid projection; it does not claim
kick/snare/hat classification, MIDI velocity, or DAW authorization.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field, model_validator

from copilot.audio.lowend import detect_transients


DRUM_EVENT_ANALYSIS_VERSION = "drum-event-representation-v1"
DETECTOR_ID = "copilot.audio.lowend.detect_transients:rms_flux"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DrumSourceSliceRef(BaseModel):
    source_asset_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    kind: Literal["AUDITION_CONTEXT_WINDOW"] = "AUDITION_CONTEXT_WINDOW"

    @model_validator(mode="after")
    def valid_span(self) -> "DrumSourceSliceRef":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("source slice must have positive duration")
        return self


class DrumMusicalPosition(BaseModel):
    tempo_bpm: float = Field(gt=0)
    tempo_source: str
    tempo_status: Literal["PROVISIONAL", "SUPPORTED", "HUMAN_VERIFIED"]
    alternate_tempos_bpm: list[float] = Field(default_factory=list)
    meter_numerator: int = Field(gt=0)
    meter_denominator: int = Field(gt=0)
    meter_status: Literal["ASSUMED", "SUPPORTED", "HUMAN_VERIFIED"]
    grid_origin_seconds: float
    onset_qn: float = Field(ge=0)
    bar: int = Field(ge=1)
    beat_in_bar: float = Field(ge=1)
    nearest_grid_qn: float = Field(ge=0)
    subdivision: Literal["quarter", "eighth", "sixteenth"]
    micro_offset_ms: float


class DrumRoleHypothesis(BaseModel):
    role: Literal[
        "KICK", "SNARE", "CLAP", "CLOSED_HAT", "OPEN_HAT", "PERCUSSION",
        "OTHER", "UNKNOWN",
    ] = "UNKNOWN"
    status: Literal["UNCLASSIFIED", "INFERRED", "HUMAN_VERIFIED"] = "UNCLASSIFIED"
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DrumEventV1(BaseModel):
    event_id: str
    source_asset_id: str
    source_sha256: str
    detector_id: str
    detector_sample_index: int = Field(ge=0)
    onset_seconds: float = Field(ge=0)
    strength: float = Field(ge=0)
    accent_rms_dbfs: float | None = None
    accent_measurement: str = "LOCAL_POST_ONSET_RMS_50MS"
    musical_position: DrumMusicalPosition
    source_slice: DrumSourceSliceRef
    role_hypothesis: DrumRoleHypothesis = Field(default_factory=DrumRoleHypothesis)
    realized_midi_velocity: int | None = Field(default=None, ge=1, le=127)
    human_correction: str | None = None


class DrumEventSetV1(BaseModel):
    schema_version: str = DRUM_EVENT_ANALYSIS_VERSION
    source_asset_id: str
    source_sha256: str
    source_path: str
    source_role: Literal["DRUMS"] = "DRUMS"
    detector_id: str = DETECTOR_ID
    detector_hop_ms: float = 5.0
    onset_latency_calibrated: Literal[False] = False
    region_start_seconds: float = Field(ge=0)
    region_end_seconds: float = Field(gt=0)
    tempo_label_hint_bpm: float | None = Field(default=None, gt=0)
    tempo_bpm: float = Field(gt=0)
    tempo_source: str
    tempo_status: Literal["PROVISIONAL", "SUPPORTED", "HUMAN_VERIFIED"]
    alternate_tempos_bpm: list[float] = Field(default_factory=list)
    meter_numerator: int = Field(gt=0)
    meter_denominator: int = Field(gt=0)
    meter_status: Literal["ASSUMED", "SUPPORTED", "HUMAN_VERIFIED"]
    events: list[DrumEventV1] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    no_ableton_access: Literal[True] = True
    no_model_calls: Literal[True] = True

    @model_validator(mode="after")
    def valid_region(self) -> "DrumEventSetV1":
        if self.region_end_seconds <= self.region_start_seconds:
            raise ValueError("analysis region must have positive duration")
        if any(event.source_asset_id != self.source_asset_id for event in self.events):
            raise ValueError("event source identity differs from event-set source")
        if any(event.source_sha256 != self.source_sha256 for event in self.events):
            raise ValueError("event source hash differs from event-set source")
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("event identities must be unique")
        return self


def _event_id(source_sha256: str, detector_id: str, sample_index: int) -> str:
    canonical = f"{source_sha256.lower()}|{detector_id}|{sample_index}".encode("utf-8")
    return "drum_evt_" + hashlib.sha256(canonical).hexdigest()[:24]


def _grid_position(
    onset_seconds: float,
    *,
    tempo_bpm: float,
    tempo_source: str,
    tempo_status: Literal["PROVISIONAL", "SUPPORTED", "HUMAN_VERIFIED"],
    alternate_tempos_bpm: list[float],
    meter_numerator: int,
    meter_denominator: int,
    meter_status: Literal["ASSUMED", "SUPPORTED", "HUMAN_VERIFIED"],
    grid_origin_seconds: float,
) -> DrumMusicalPosition:
    qn = max(0.0, (onset_seconds - grid_origin_seconds) * tempo_bpm / 60.0)
    steps = ((1.0, "quarter"), (0.5, "eighth"), (0.25, "sixteenth"))
    step, subdivision = min(steps, key=lambda item: abs(qn / item[0] - round(qn / item[0])))
    nearest_qn = max(0.0, round(qn / step) * step)
    qn_per_bar = meter_numerator * 4.0 / meter_denominator
    beat_length_qn = 4.0 / meter_denominator
    bar = int(math.floor(qn / qn_per_bar)) + 1
    beat_in_bar = ((qn % qn_per_bar) / beat_length_qn) + 1.0
    micro_offset_ms = (qn - nearest_qn) * 60.0 / tempo_bpm * 1000.0
    return DrumMusicalPosition(
        tempo_bpm=tempo_bpm,
        tempo_source=tempo_source,
        tempo_status=tempo_status,
        alternate_tempos_bpm=alternate_tempos_bpm,
        meter_numerator=meter_numerator,
        meter_denominator=meter_denominator,
        meter_status=meter_status,
        grid_origin_seconds=grid_origin_seconds,
        onset_qn=qn,
        bar=bar,
        beat_in_bar=beat_in_bar,
        nearest_grid_qn=nearest_qn,
        subdivision=subdivision,  # type: ignore[arg-type]
        micro_offset_ms=micro_offset_ms,
    )


def build_drum_event_set(
    audio_path: Path,
    *,
    source_asset_id: str,
    source_sha256: str,
    tempo_bpm: float,
    tempo_source: str,
    tempo_status: Literal["PROVISIONAL", "SUPPORTED", "HUMAN_VERIFIED"] = "PROVISIONAL",
    alternate_tempos_bpm: list[float] | None = None,
    tempo_label_hint_bpm: float | None = None,
    meter_numerator: int = 4,
    meter_denominator: int = 4,
    meter_status: Literal["ASSUMED", "SUPPORTED", "HUMAN_VERIFIED"] = "ASSUMED",
    grid_origin_seconds: float = 0.0,
    region_start_seconds: float = 0.0,
    region_end_seconds: float | None = None,
    min_distance_seconds: float = 0.08,
) -> DrumEventSetV1:
    """Create event evidence for an explicit region of an immutable drum stem.

    Onset times and measured strengths are observations.  Grid placement is a
    projection under the supplied tempo/meter hypotheses, and role labels are
    deliberately UNKNOWN until a calibrated classifier or human correction is
    available.
    """
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    actual_sha256 = _sha256_file(path)
    if actual_sha256.casefold() != source_sha256.casefold():
        raise ValueError("DRUM_SOURCE_SHA256_MISMATCH")
    audio, sample_rate = sf.read(str(path), always_2d=True, dtype="float32")
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("DRUM_SOURCE_AUDIO_INVALID")
    duration_seconds = len(audio) / sample_rate
    region_end = duration_seconds if region_end_seconds is None else float(region_end_seconds)
    if region_start_seconds < 0 or region_end <= region_start_seconds or region_end > duration_seconds:
        raise ValueError("DRUM_ANALYSIS_REGION_OUT_OF_BOUNDS")
    mono = np.mean(audio.astype(np.float64), axis=1)
    detection = detect_transients(
        mono,
        int(sample_rate),
        min_distance_s=min_distance_seconds,
        role="drums",
    )
    attacks = [
        attack for attack in detection.get("attacks") or []
        if region_start_seconds <= float(attack["time_s"]) < region_end
    ]
    events: list[DrumEventV1] = []
    for attack in attacks:
        onset = float(attack["time_s"])
        sample_index = int(round(onset * sample_rate))
        accent_start = min(len(mono), max(0, sample_index))
        accent_end = min(len(mono), accent_start + int(round(sample_rate * 0.05)))
        accent_rms = (
            float(np.sqrt(np.mean(mono[accent_start:accent_end] ** 2)))
            if accent_end > accent_start else 0.0
        )
        accent_dbfs = 20.0 * math.log10(max(accent_rms, 1e-12))
        event_id = _event_id(source_sha256, DETECTOR_ID, sample_index)
        slice_start = max(region_start_seconds, onset - 0.02)
        slice_end = min(region_end, onset + 0.22, duration_seconds)
        if slice_end <= onset:
            continue
        grid = _grid_position(
            onset,
            tempo_bpm=tempo_bpm,
            tempo_source=tempo_source,
            tempo_status=tempo_status,
            alternate_tempos_bpm=list(alternate_tempos_bpm or []),
            meter_numerator=meter_numerator,
            meter_denominator=meter_denominator,
            meter_status=meter_status,
            grid_origin_seconds=grid_origin_seconds,
        )
        events.append(DrumEventV1(
            event_id=event_id,
            source_asset_id=source_asset_id,
            source_sha256=source_sha256,
            detector_id=DETECTOR_ID,
            detector_sample_index=sample_index,
            onset_seconds=onset,
            strength=max(0.0, float(attack.get("strength") or 0.0)),
            accent_rms_dbfs=accent_dbfs,
            musical_position=grid,
            source_slice=DrumSourceSliceRef(
                source_asset_id=source_asset_id,
                start_seconds=slice_start,
                end_seconds=slice_end,
            ),
            role_hypothesis=DrumRoleHypothesis(
                limitations=["TRANSIENT_DETECTOR_DOES_NOT_CLASSIFY_KICK_SNARE_OR_HAT"]
            ),
        ))
    limitations = [
        "MUSICAL_POSITIONS_ARE_CONDITIONAL_ON_SUPPLIED_TEMPO_METER_AND_GRID_ORIGIN",
        "ROLE_CLASSIFICATION_NOT_PERFORMED_TRANSIENTS_REMAIN_UNKNOWN",
        "ACCENT_RMS_IS_AUDIO_MEASUREMENT_NOT_MIDI_VELOCITY",
        "SOURCE_SLICE_IS_AUDITION_CONTEXT_WINDOW_NOT_SEGMENTED_INSTRUMENT_AUDIO",
        "RMS_FLUX_ONSET_TIME_RESOLUTION_APPROX_5MS",
        "RMS_FLUX_DETECTOR_LATENCY_NOT_CALIBRATED_MICROTIMING_IS_PROVISIONAL",
    ]
    if tempo_label_hint_bpm is not None and abs(tempo_label_hint_bpm - tempo_bpm) > 0.1:
        limitations.append("TEMPO_LABEL_HINT_CONFLICTS_WITH_SELECTED_TEMPO_HYPOTHESIS")
    if alternate_tempos_bpm:
        limitations.append("ALTERNATE_TEMPO_HYPOTHESES_REMAIN_UNRESOLVED")
    return DrumEventSetV1(
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        source_path=str(path.resolve()),
        detector_id=DETECTOR_ID,
        region_start_seconds=region_start_seconds,
        region_end_seconds=region_end,
        tempo_label_hint_bpm=tempo_label_hint_bpm,
        tempo_bpm=tempo_bpm,
        tempo_source=tempo_source,
        tempo_status=tempo_status,
        alternate_tempos_bpm=list(alternate_tempos_bpm or []),
        meter_numerator=meter_numerator,
        meter_denominator=meter_denominator,
        meter_status=meter_status,
        events=events,
        limitations=limitations,
    )


__all__ = [
    "DETECTOR_ID",
    "DRUM_EVENT_ANALYSIS_VERSION",
    "DrumEventSetV1",
    "DrumEventV1",
    "DrumMusicalPosition",
    "DrumRoleHypothesis",
    "DrumSourceSliceRef",
    "build_drum_event_set",
]
