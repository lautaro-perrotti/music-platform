"""RHYTHMIC FACTS: onset grid, IOI, periodicity, timing vs authoritative beat grid."""

from __future__ import annotations

from typing import Any

import numpy as np

from copilot.audio.physical_dsp_v2.audio import AudioBuffer, qn_to_seconds
from copilot.audio.physical_dsp_v2.cache import cache_key, load_cached, store_cached
from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION
from copilot.audio.physical_dsp_v2.observation import build_observation, merge_quality
from copilot.audio.physical_dsp_v2.providers import numpy_status
from copilot.audio.physical_dsp_v2.transients import analyze_transients
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspLimitation,
    DspQuality,
    DspSubject,
    MeasuredValue,
    TimeSpan,
)


def analyze_rhythm(
    buffer: AudioBuffer,
    *,
    subject: DspSubject,
    time_span: TimeSpan,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
    cache_dir=None,
) -> Any:
    params = dict(params or {})
    params.setdefault("tempo_bpm", time_span.tempo_bpm)
    params.setdefault("grid_start_qn", time_span.start_qn if time_span.start_qn is not None else 0.0)
    key = cache_key(buffer.artifact_hash, AnalyzerFamily.RHYTHM.value, ANALYZER_VERSION, {
        **params,
        "start_s": time_span.start_s,
        "end_s": time_span.end_s,
        "granularity": time_span.granularity.value,
    })
    if use_cache:
        hit = load_cached(key, cache_dir)
        if hit is not None:
            return hit

    trans = analyze_transients(
        buffer, subject=subject, time_span=time_span, params=params, use_cache=use_cache, cache_dir=cache_dir
    )
    times = list(trans.get("onset_times_s").value or []) if trans.get("onset_times_s") else []
    ioi = np.diff(np.asarray(times, dtype=np.float64)) if len(times) > 1 else np.asarray([], dtype=np.float64)
    duration = max(time_span.duration_s, 1e-9)
    limitations = [
        DspLimitation("RHYTHM_FROM_ONSET_FACTS", "Periodicity is IOI/autocorr of energy onsets, not a groove label.")
    ]
    quality = DspQuality.OK
    period = None
    periodicity_strength = None
    if len(ioi) >= 2:
        period = float(np.median(ioi))
        if period > 1e-6:
            hits = sum(1 for d in ioi if abs(d - period) <= max(0.03, 0.12 * period))
            periodicity_strength = float(hits / len(ioi))
    elif len(times) < 2:
        limitations.append(DspLimitation("INSUFFICIENT_ONSETS_FOR_PERIODICITY", "need >=2 onsets"))
        quality = DspQuality.LIMITED

    tempo = params.get("tempo_bpm")
    offsets: list[float] = []
    phases: list[float] = []
    off_beat = None
    if tempo and float(tempo) > 0:
        beat_s = 60.0 / float(tempo)
        grid0 = 0.0
        if time_span.start_qn is not None:
            grid0 = qn_to_seconds(float(params.get("grid_start_qn") or 0.0), float(tempo))
        for t in times:
            rel = t - grid0
            nearest = round(rel / beat_s) * beat_s
            offsets.append(rel - nearest)
            phase = (rel / beat_s) % 1.0
            phases.append(phase)
        if phases:
            off_beat = float(np.mean([1.0 if 0.25 <= p <= 0.75 else 0.0 for p in phases]))
        limitations.append(
            DspLimitation(
                "SYNCOPATION_IS_ONSET_PHASE_VS_GRID",
                "off_beat_onset_fraction is onset phase vs provided tempo grid, not a musical syncopation score.",
            )
        )
    else:
        limitations.append(
            DspLimitation("AUTHORITATIVE_BEAT_GRID_NOT_PROVIDED", "timing offsets require tempo_bpm")
        )
        quality = merge_quality(quality, DspQuality.LIMITED)

    values = [
        MeasuredValue("onset_times_s", times, unit="s"),
        MeasuredValue("onset_count", int(len(times)), unit="count"),
        MeasuredValue("onset_density_per_s", float(len(times) / duration), unit="1/s"),
        MeasuredValue("ioi_s", [float(x) for x in ioi], unit="s"),
        MeasuredValue("mean_ioi_s", float(np.mean(ioi)) if len(ioi) else None, unit="s"),
        MeasuredValue("median_ioi_s", float(np.median(ioi)) if len(ioi) else None, unit="s"),
        MeasuredValue("periodicity_s", period, unit="s"),
        MeasuredValue("periodicity_strength", periodicity_strength, unit="ratio"),
        MeasuredValue("repetition_ioi_consistency", periodicity_strength, unit="ratio"),
        MeasuredValue("timing_offsets_s", offsets, unit="s"),
        MeasuredValue(
            "timing_offset_abs_mean_s",
            float(np.mean(np.abs(offsets))) if offsets else None,
            unit="s",
        ),
        MeasuredValue("beat_phases", phases, unit="ratio"),
        MeasuredValue("off_beat_onset_fraction", off_beat, unit="ratio"),
    ]
    obs = build_observation(
        observation_id=key,
        analyzer_id=AnalyzerFamily.RHYTHM.value,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=quality,
        limitations=limitations,
        provider_id="numpy+lowend.detect_transients",
        provider_version=numpy_status().version,
        method="ioi_median_period+optional_qn_grid_offsets",
        params=params,
        source_artifact_hash=buffer.artifact_hash,
        cache_key=key,
    )
    if use_cache:
        store_cached(obs, cache_dir)
    return obs
