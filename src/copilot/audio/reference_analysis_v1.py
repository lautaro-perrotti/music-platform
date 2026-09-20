"""Deterministic, read-only reference-track windowing.

This module only schedules evidence windows and packages already-measured
features. It does not open Ableton, capture audio, or mutate a target project.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from copilot.schemas.reference_analysis import (
    ReferenceAnalysisPack,
    ReferenceStateTokens,
    ReferenceWindowEvidence,
)

BEATS_PER_BAR = 4.0
REFERENCE_WINDOW_BARS = 32
REFERENCE_WINDOW_BEATS = BEATS_PER_BAR * REFERENCE_WINDOW_BARS


def pack_from_fullmix_observation(
    observation: Any,
    *,
    reference_state_token: str,
    target_state_token: str,
    tempo_bpm: float,
    window_beats: float = REFERENCE_WINDOW_BEATS,
) -> ReferenceAnalysisPack:
    """Project frozen FullMix frames into musical windows.

    The FullMix analyzer remains the measurement authority. This adapter only
    aggregates its factual frames; it performs no diagnosis and no writes.
    """
    if tempo_bpm <= 0:
        raise ValueError("tempo_bpm must be positive")
    total_beats = float(observation.duration_s) * tempo_bpm / 60.0
    spans = reference_window_spans(total_beats, window_beats=window_beats)
    rows: list[dict[str, Any]] = []
    seconds_per_beat = 60.0 / tempo_bpm
    for start_beat, end_beat in spans:
        start_s, end_s = start_beat * seconds_per_beat, end_beat * seconds_per_beat
        frames = [
            frame for frame in observation.energy_frames
            if start_s <= float(frame.t_s) < end_s
        ]
        dynamics = [
            item for item in observation.dynamics
            if start_s <= float(item.window_start_s) < end_s
        ]
        spectral = [
            item for item in observation.spectral_trajectory
            if start_s <= float(item.t_s) < end_s
        ]
        bands = [point.bands for point in spectral if "LOW" in point.bands or "SUB" in point.bands]
        low_values = [float(b.get("LOW", b.get("SUB", 0.0))) for b in bands]
        rows.append({
            "energy_db": (sum(float(f.relative_db) for f in frames) / len(frames)) if frames else None,
            "crest_factor_db": (sum(float(d.crest_factor or 0.0) for d in dynamics) / len(dynamics)) if dynamics else None,
            "low_band_energy": (sum(low_values) / len(low_values)) if low_values else None,
        })
    return build_reference_analysis_pack(
        reference_state_token=reference_state_token,
        target_state_token=target_state_token,
        total_beats=total_beats,
        measurements=rows,
        window_beats=window_beats,
    )


def reference_window_spans(
    total_beats: float,
    *,
    window_beats: float = REFERENCE_WINDOW_BEATS,
) -> list[tuple[float, float]]:
    """Return complete consecutive windows plus a final bounded tail."""
    if total_beats < 0 or window_beats <= 0:
        raise ValueError("total_beats must be non-negative and window_beats positive")
    spans: list[tuple[float, float]] = []
    start = 0.0
    while start < total_beats:
        end = min(start + window_beats, total_beats)
        spans.append((start, end))
        start = end
    return spans


def build_reference_analysis_pack(
    *,
    reference_state_token: str,
    target_state_token: str,
    total_beats: float,
    measurements: Iterable[Mapping[str, Any]] = (),
    window_beats: float = REFERENCE_WINDOW_BEATS,
) -> ReferenceAnalysisPack:
    """Package factual per-window measurements; missing fields stay unknown."""
    spans = reference_window_spans(total_beats, window_beats=window_beats)
    rows = list(measurements)
    if len(rows) > len(spans):
        raise ValueError("more measurement rows than reference windows")
    windows = []
    for index, (start, end) in enumerate(spans):
        row = dict(rows[index]) if index < len(rows) else {}
        row.update(start_beat=start, end_beat=end)
        windows.append(ReferenceWindowEvidence.model_validate(row))
    return ReferenceAnalysisPack(
        tokens=ReferenceStateTokens(
            reference_state_token=reference_state_token,
            target_state_token=target_state_token,
        ),
        window_beats=window_beats,
        windows=windows,
        no_write=True,
        raw_audio_included=False,
    )
