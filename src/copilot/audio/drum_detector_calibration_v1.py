"""Deterministic synthetic calibration for the existing RMS-flux detector.

The result characterizes detector behavior on explicit synthetic waveform
families. It is not a calibration claim for arbitrary musical recordings.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from copilot.audio.drum_events_v1 import DETECTOR_ID
from copilot.audio.lowend import detect_transients


CALIBRATION_VERSION = "drum-detector-synthetic-calibration-v1"
WAVEFORM_KINDS = ("IMPULSE", "DECAYED_TONE", "DECAYED_NOISE")


class DetectorCalibrationTrial(BaseModel):
    waveform_kind: Literal["IMPULSE", "DECAYED_TONE", "DECAYED_NOISE"]
    sample_rate: int = Field(gt=0)
    expected_onsets_seconds: list[float]
    detected_onsets_seconds: list[float]
    signed_errors_ms: list[float]
    unmatched_expected_count: int = Field(ge=0)

    @property
    def median_signed_error_ms(self) -> float | None:
        if not self.signed_errors_ms:
            return None
        return float(np.median(self.signed_errors_ms))

    @property
    def p95_absolute_error_ms(self) -> float | None:
        if not self.signed_errors_ms:
            return None
        return float(np.percentile(np.abs(self.signed_errors_ms), 95))


class DetectorCalibrationReport(BaseModel):
    schema_version: str = CALIBRATION_VERSION
    calibration_id: str
    detector_id: str = DETECTOR_ID
    applicability: Literal["SYNTHETIC_PROTOTYPES_ONLY"] = "SYNTHETIC_PROTOTYPES_ONLY"
    onset_definition: str = "FIRST_NONZERO_SYNTHETIC_SAMPLE"
    trials: list[DetectorCalibrationTrial]
    source_audio_calibrated: Literal[False] = False
    correction_applied: Literal[False] = False
    limitations: list[str] = Field(default_factory=list)


def _synthetic_signal(kind: str, sample_rate: int, onsets: list[float]) -> np.ndarray:
    samples = np.zeros(sample_rate * 3, dtype=np.float64)
    for index, onset in enumerate(onsets):
        start = int(round(onset * sample_rate))
        if kind == "IMPULSE":
            samples[start] = 1.0
            continue
        count = int(round(0.03 * sample_rate))
        time = np.arange(count, dtype=np.float64) / sample_rate
        envelope = np.exp(-time / 0.008)
        if kind == "DECAYED_TONE":
            wave = np.sin(2.0 * np.pi * 130.0 * time)
        else:
            rng = np.random.default_rng(73_001 + sample_rate + index)
            wave = rng.standard_normal(count)
        samples[start : start + count] = 0.8 * wave * envelope
    return samples


def _match_onsets(expected: list[float], detected: list[float]) -> tuple[list[float], int]:
    remaining = list(detected)
    errors_ms: list[float] = []
    for onset in expected:
        if not remaining:
            break
        nearest = min(range(len(remaining)), key=lambda i: abs(remaining[i] - onset))
        candidate = remaining[nearest]
        if abs(candidate - onset) <= 0.05:
            errors_ms.append((candidate - onset) * 1000.0)
            remaining.pop(nearest)
    return errors_ms, len(expected) - len(errors_ms)


def calibrate_rms_flux_detector(
    sample_rates: tuple[int, ...] = (44_100, 48_000),
) -> DetectorCalibrationReport:
    """Measure expected-versus-detected offsets on deterministic prototypes."""
    if not sample_rates or any(rate < 8_000 for rate in sample_rates):
        raise ValueError("DRUM_CALIBRATION_SAMPLE_RATES_INVALID")
    expected = [0.25, 0.75, 1.25, 1.75]
    trials: list[DetectorCalibrationTrial] = []
    for rate in sample_rates:
        for kind in WAVEFORM_KINDS:
            signal = _synthetic_signal(kind, rate, expected)
            detected = [
                float(item["time_s"])
                for item in detect_transients(signal, rate, min_distance_s=0.08, role="drums")["attacks"]
            ]
            errors, unmatched = _match_onsets(expected, detected)
            trials.append(DetectorCalibrationTrial(
                waveform_kind=kind, sample_rate=rate,
                expected_onsets_seconds=expected,
                detected_onsets_seconds=detected,
                signed_errors_ms=[round(value, 6) for value in errors],
                unmatched_expected_count=unmatched,
            ))
    identity_payload = [
        CALIBRATION_VERSION,
        DETECTOR_ID,
        *[
            f"{trial.sample_rate}:{trial.waveform_kind}:"
            f"{','.join(f'{value:.6f}' for value in trial.signed_errors_ms)}"
            for trial in trials
        ],
    ]
    calibration_id = "detcal_" + hashlib.sha256(
        json.dumps(identity_payload, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return DetectorCalibrationReport(
        calibration_id=calibration_id,
        trials=trials,
        limitations=[
            "SYNTHETIC_PROTOTYPES_DO_NOT_COVER_ALL_MUSICAL_TRANSIENT_ENVELOPES",
            "CALIBRATION_DOES_NOT_APPLY_TO_REFERENCE_SOURCE_WITHOUT_SOURCE_VALIDATION",
            "NO_OFFSET_CORRECTION_IS_APPLIED",
            "MUSICAL_MICROTIMING_REMAINS_PROVISIONAL",
        ],
    )


__all__ = [
    "CALIBRATION_VERSION",
    "DetectorCalibrationReport",
    "DetectorCalibrationTrial",
    "calibrate_rms_flux_detector",
]
