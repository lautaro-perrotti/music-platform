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
