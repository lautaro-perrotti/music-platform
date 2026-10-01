"""Evidence-linked drum transient events for a bounded source region.

This layer reuses the existing RMS-flux transient detector. It records
observations, bounded uncalibrated role hypotheses, and a conditional
musical-grid projection; it does not claim calibrated confidence, MIDI
velocity, or DAW authorization.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field, model_validator

from copilot.audio.lowend import detect_transients


DRUM_EVENT_ANALYSIS_VERSION = "drum-event-representation-v2"
DETECTOR_ID = "copilot.audio.lowend.detect_transients:rms_flux"
ROLE_CLASSIFIER_VERSION = "drum-role-rules-v1"

DrumRole = Literal[
    "KICK", "SNARE", "CLAP", "CLOSED_HAT", "OPEN_HAT", "PERCUSSION",
    "OTHER", "UNKNOWN",
]


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
    tempo_status: Literal["PROVISIONAL", "SUPPORTED", "HUMAN_VERIFIED", "VERIFIED"]
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


class DrumAttackFeatures(BaseModel):
    measured_window_start_seconds: float = Field(ge=0)
    measured_window_end_seconds: float = Field(gt=0)
    rms: float | None = Field(default=None, ge=0)
    peak: float | None = Field(default=None, ge=0)
    spectral_centroid_hz: float | None = Field(default=None, ge=0)
    low_band_energy_fraction_20_150_hz: float | None = Field(default=None, ge=0, le=1)
    mid_band_energy_fraction_150_2000_hz: float | None = Field(default=None, ge=0, le=1)
    high_band_energy_fraction_2000_12000_hz: float | None = Field(default=None, ge=0, le=1)
    late_to_early_rms_ratio: float | None = Field(default=None, ge=0)


class DrumRoleHypothesis(BaseModel):
    role: DrumRole = "UNKNOWN"
    status: Literal["UNCLASSIFIED", "INFERRED", "HUMAN_VERIFIED"] = "UNCLASSIFIED"
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_basis: Literal["UNAVAILABLE", "UNCALIBRATED_RULES"] = "UNAVAILABLE"
    classifier_version: str | None = None
    rule_id: str | None = None
    features: DrumAttackFeatures | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DrumHumanCorrection(BaseModel):
    role: DrumRole
    reviewer_id: str = Field(min_length=1)
    recorded_at_utc: str = Field(min_length=1)
    note: str | None = None


class DrumEventChange(BaseModel):
    revision: int = Field(ge=2)
    field: Literal["role"]
    previous_effective_role: DrumRole
    new_role: DrumRole
    actor: Literal["HUMAN"] = "HUMAN"
    reviewer_id: str = Field(min_length=1)
    recorded_at_utc: str = Field(min_length=1)


class DrumTempoDecisionV1(BaseModel):
    previous_tempo_bpm: float = Field(gt=0)
    previous_tempo_source: str
    previous_tempo_status: str
    confirmed_tempo_bpm: float = Field(gt=0)
    authority: Literal["HUMAN"] = "HUMAN"
    source: Literal["DIRECT_USER_INSTRUCTION"] = "DIRECT_USER_INSTRUCTION"
    reviewer_id: str = Field(min_length=1)
    note: str = Field(min_length=1)
    recorded_at_utc: str = Field(min_length=1)


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
    human_correction: DrumHumanCorrection | None = None
    selected_sample_asset_id: str | None = None
    ableton_realization_ref: dict | None = None
    knowledge_links: list[str] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    change_history: list[DrumEventChange] = Field(default_factory=list)

    @property
    def effective_role(self) -> DrumRole:
        if self.human_correction is not None:
            return self.human_correction.role
        return self.role_hypothesis.role


class DrumEventSetV1(BaseModel):
    schema_version: str = DRUM_EVENT_ANALYSIS_VERSION
    source_asset_id: str
    source_sha256: str
    source_path: str
    source_role: Literal["DRUMS"] = "DRUMS"
    detector_id: str = DETECTOR_ID
    role_classifier_version: str = ROLE_CLASSIFIER_VERSION
    detector_calibration_id: str | None = None
    detector_hop_ms: float = 5.0
    onset_latency_calibrated: Literal[False] = False
    region_start_seconds: float = Field(ge=0)
    region_end_seconds: float = Field(gt=0)
    tempo_label_hint_bpm: float | None = Field(default=None, gt=0)
    tempo_bpm: float = Field(gt=0)
    tempo_source: str
    tempo_status: Literal["PROVISIONAL", "SUPPORTED", "HUMAN_VERIFIED", "VERIFIED"]
    alternate_tempos_bpm: list[float] = Field(default_factory=list)
    meter_numerator: int = Field(gt=0)
    meter_denominator: int = Field(gt=0)
    meter_status: Literal["ASSUMED", "SUPPORTED", "HUMAN_VERIFIED"]
    tempo_decisions: list[DrumTempoDecisionV1] = Field(default_factory=list)
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


def _attack_features(
    mono: np.ndarray,
    sample_rate: int,
    *,
    start_seconds: float,
    end_seconds: float,
) -> DrumAttackFeatures:
    start = max(0, int(math.floor(start_seconds * sample_rate)))
    end = min(len(mono), int(math.ceil(end_seconds * sample_rate)))
    segment = np.asarray(mono[start:end], dtype=np.float64)
    values: dict = {
        "measured_window_start_seconds": start / sample_rate,
        "measured_window_end_seconds": end / sample_rate,
    }
    if not len(segment):
        return DrumAttackFeatures(**values)
    rms = float(np.sqrt(np.mean(segment**2)))
    values.update(rms=rms, peak=float(np.max(np.abs(segment))))
    spectrum = np.abs(np.fft.rfft(segment)) ** 2
    freqs = np.fft.rfftfreq(len(segment), 1.0 / sample_rate)
    total = float(spectrum.sum())
    if total > 0:
        values["spectral_centroid_hz"] = float(np.dot(freqs, spectrum) / total)
        bands = ((20.0, 150.0), (150.0, 2_000.0), (2_000.0, 12_000.0))
        names = (
            "low_band_energy_fraction_20_150_hz",
            "mid_band_energy_fraction_150_2000_hz",
            "high_band_energy_fraction_2000_12000_hz",
        )
        nyquist = sample_rate / 2.0
        for name, (lo, hi) in zip(names, bands):
            mask = (freqs >= lo) & (freqs < min(hi, nyquist))
            values[name] = float(spectrum[mask].sum() / total)
    early_end = min(len(segment), int(round(sample_rate * 0.025)))
    late_start = max(0, len(segment) - int(round(sample_rate * 0.025)))
    if early_end and late_start < len(segment):
        early_rms = float(np.sqrt(np.mean(segment[:early_end] ** 2)))
        late_rms = float(np.sqrt(np.mean(segment[late_start:] ** 2)))
        values["late_to_early_rms_ratio"] = late_rms / max(early_rms, 1e-12)
    return DrumAttackFeatures(**values)


def _classify_attack(features: DrumAttackFeatures) -> DrumRoleHypothesis:
    """Emit only wide-margin kick/closed-hat hypotheses; scores are not probabilities."""
    low = features.low_band_energy_fraction_20_150_hz
    high = features.high_band_energy_fraction_2000_12000_hz
    centroid = features.spectral_centroid_hz
    role: DrumRole = "UNKNOWN"
    rule_id: str | None = None
    if low is not None and centroid is not None and low >= 0.60 and centroid <= 300.0:
        role, rule_id = "KICK", "LOW_BAND_DOMINANT_AND_LOW_CENTROID"
    elif (
        low is not None and high is not None and centroid is not None
        and high >= 0.85 and low <= 0.05 and centroid >= 3_500.0
    ):
        role, rule_id = "CLOSED_HAT", "HIGH_BAND_DOMINANT_LOW_LF_AND_HIGH_CENTROID"
    if role == "UNKNOWN":
        return DrumRoleHypothesis(
            features=features,
            classifier_version=ROLE_CLASSIFIER_VERSION,
            evidence_refs=["features.spectral_centroid_hz", "features.band_energy"],
            limitations=["NO_ROLE_RULE_MET_CONSERVATIVE_THRESHOLDS"],
        )
    return DrumRoleHypothesis(
        role=role,
        status="INFERRED",
        confidence=None,
        confidence_basis="UNCALIBRATED_RULES",
        classifier_version=ROLE_CLASSIFIER_VERSION,
        rule_id=rule_id,
        features=features,
        evidence_refs=["features.spectral_centroid_hz", "features.band_energy"],
        limitations=[
            "HEURISTIC_ROLE_IS_NOT_HUMAN_VERIFIED",
            "NUMERIC_CONFIDENCE_NOT_CALIBRATED",
            "SOURCE_STEM_PURITY_NOT_ESTABLISHED_BY_THIS_CLASSIFIER",
        ],
    )


def _grid_position(
    onset_seconds: float,
    *,
    tempo_bpm: float,
    tempo_source: str,
    tempo_status: Literal["PROVISIONAL", "SUPPORTED", "HUMAN_VERIFIED", "VERIFIED"],
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


def apply_human_tempo_confirmation(
    event_set: DrumEventSetV1,
    *,
    tempo_bpm: float,
    reviewer_id: str,
    note: str,
    recorded_at_utc: str | None = None,
) -> DrumEventSetV1:
    """Return a new event set with a human-authorized operating grid.

    Original automatic tempo/source/alternates are preserved in the decision
    history; only the derived grid projection is recalculated. The input model
    is not mutated, so callers can persist this as a distinct derived artifact.
    """
    if not reviewer_id.strip() or not note.strip():
        raise ValueError("DRUM_TEMPO_CONFIRMATION_PROVENANCE_REQUIRED")
    if not math.isfinite(tempo_bpm) or tempo_bpm <= 0:
        raise ValueError("DRUM_TEMPO_CONFIRMATION_INVALID_BPM")
    confirmed = event_set.model_copy(deep=True)
    timestamp = recorded_at_utc or datetime.now(timezone.utc).isoformat()
    confirmed.tempo_decisions.append(DrumTempoDecisionV1(
        previous_tempo_bpm=event_set.tempo_bpm,
        previous_tempo_source=event_set.tempo_source,
        previous_tempo_status=event_set.tempo_status,
        confirmed_tempo_bpm=tempo_bpm,
        reviewer_id=reviewer_id,
        note=note,
        recorded_at_utc=timestamp,
    ))
    confirmed.tempo_bpm = tempo_bpm
    confirmed.tempo_source = "HUMAN_CONFIRMED"
    confirmed.tempo_status = "VERIFIED"
    confirmed.limitations = [
        limitation for limitation in confirmed.limitations
        if limitation != "TEMPO_LABEL_HINT_CONFLICTS_WITH_SELECTED_TEMPO_HYPOTHESIS"
    ]
    confirmed.limitations.append("AUTOMATIC_TEMPO_HYPOTHESES_RETAINED_IN_TEMPO_DECISION_HISTORY")
    for event in confirmed.events:
        position = event.musical_position
        event.musical_position = _grid_position(
            event.onset_seconds,
            tempo_bpm=tempo_bpm,
            tempo_source="HUMAN_CONFIRMED",
            tempo_status="VERIFIED",
            alternate_tempos_bpm=list(event_set.alternate_tempos_bpm),
            meter_numerator=position.meter_numerator,
            meter_denominator=position.meter_denominator,
            meter_status=position.meter_status,
            grid_origin_seconds=position.grid_origin_seconds,
        )
    return confirmed


def build_drum_event_set(
    audio_path: Path,
    *,
    source_asset_id: str,
    source_sha256: str,
    tempo_bpm: float,
    tempo_source: str,
    tempo_status: Literal["PROVISIONAL", "SUPPORTED", "HUMAN_VERIFIED", "VERIFIED"] = "PROVISIONAL",
    alternate_tempos_bpm: list[float] | None = None,
    tempo_label_hint_bpm: float | None = None,
    meter_numerator: int = 4,
    meter_denominator: int = 4,
    meter_status: Literal["ASSUMED", "SUPPORTED", "HUMAN_VERIFIED"] = "ASSUMED",
    grid_origin_seconds: float = 0.0,
    region_start_seconds: float = 0.0,
    region_end_seconds: float | None = None,
    min_distance_seconds: float = 0.08,
    detector_calibration_id: str | None = None,
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
            role_hypothesis=_classify_attack(_attack_features(
                mono,
                int(sample_rate),
                start_seconds=max(region_start_seconds, onset - 0.005),
                end_seconds=min(region_end, onset + 0.100),
            )),
        ))
    limitations = [
        "MUSICAL_POSITIONS_ARE_CONDITIONAL_ON_SUPPLIED_TEMPO_METER_AND_GRID_ORIGIN",
        "ROLE_HYPOTHESES_USE_UNCALIBRATED_CONSERVATIVE_RULES_AND_REMAIN_INFERRED",
        "ACCENT_RMS_IS_AUDIO_MEASUREMENT_NOT_MIDI_VELOCITY",
        "SOURCE_SLICE_IS_AUDITION_CONTEXT_WINDOW_NOT_SEGMENTED_INSTRUMENT_AUDIO",
        "RMS_FLUX_ONSET_TIME_RESOLUTION_APPROX_5MS",
        "DETECTOR_LATENCY_CALIBRATED_ON_SYNTHETIC_PROTOTYPES_ONLY_NOT_VALIDATED_ON_SOURCE_AUDIO_MICROTIMING_PROVISIONAL",
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
        role_classifier_version=ROLE_CLASSIFIER_VERSION,
        detector_calibration_id=detector_calibration_id,
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


def apply_human_role_correction(
    event_set: DrumEventSetV1,
    *,
    event_id: str,
    role: DrumRole,
    reviewer_id: str,
    note: str | None = None,
    recorded_at_utc: str | None = None,
) -> DrumEventSetV1:
    """Return a revised copy; machine inference remains intact for provenance."""
    if not reviewer_id.strip():
        raise ValueError("DRUM_HUMAN_REVIEWER_REQUIRED")
    corrected = event_set.model_copy(deep=True)
    event = next((item for item in corrected.events if item.event_id == event_id), None)
    if event is None:
        raise ValueError("DRUM_EVENT_NOT_FOUND")
    timestamp = recorded_at_utc or datetime.now(timezone.utc).isoformat()
    previous_role = event.effective_role
    if (
        event.human_correction is not None
        and event.human_correction.role == role
        and event.human_correction.reviewer_id == reviewer_id
        and event.human_correction.note == note
    ):
        return corrected
    revision = event.revision + 1
    event.human_correction = DrumHumanCorrection(
        role=role,
        reviewer_id=reviewer_id,
        recorded_at_utc=timestamp,
        note=note,
    )
    event.change_history.append(DrumEventChange(
        revision=revision,
        field="role",
        previous_effective_role=previous_role,
        new_role=role,
        reviewer_id=reviewer_id,
        recorded_at_utc=timestamp,
    ))
    event.revision = revision
    return corrected


__all__ = [
    "DETECTOR_ID",
    "DRUM_EVENT_ANALYSIS_VERSION",
    "ROLE_CLASSIFIER_VERSION",
    "DrumAttackFeatures",
    "DrumEventSetV1",
    "DrumEventV1",
    "DrumHumanCorrection",
    "DrumMusicalPosition",
    "DrumRoleHypothesis",
    "DrumSourceSliceRef",
    "DrumTempoDecisionV1",
    "apply_human_tempo_confirmation",
    "apply_human_role_correction",
    "build_drum_event_set",
]
