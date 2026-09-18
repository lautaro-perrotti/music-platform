"""LEVEL/DYNAMICS: peak, RMS, crest, loudness, silence, dips, level change."""

from __future__ import annotations

from typing import Any

import numpy as np

from copilot.audio.physical_dsp_v2.audio import AudioBuffer, amp_dbfs, db, frame_signal
from copilot.audio.physical_dsp_v2.cache import cache_key, load_cached, store_cached
from copilot.audio.physical_dsp_v2.contract import (
    ANALYZER_VERSION,
    HOP_S,
    MACRO_S,
    NEAR_SILENCE_RMS,
    SHORT_TERM_S,
    SILENCE_RMS,
    WINDOW_S,
)
from copilot.audio.physical_dsp_v2.observation import build_observation, merge_quality
from copilot.audio.physical_dsp_v2.providers import numpy_status, pyloudnorm_status
from copilot.audio.physical_dsp_v2.signal import integrated_loudness, true_peak_4x
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspLimitation,
    DspQuality,
    DspSubject,
    MeasuredValue,
    TimeSpan,
)


def analyze_level_dynamics(
    buffer: AudioBuffer,
    *,
    subject: DspSubject,
    time_span: TimeSpan,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
    cache_dir=None,
) -> Any:
    params = dict(params or {})
    params.setdefault("window_s", WINDOW_S)
    params.setdefault("hop_s", HOP_S)
    params.setdefault("short_term_s", SHORT_TERM_S)
    params.setdefault("macro_s", MACRO_S)
    key = cache_key(buffer.artifact_hash, AnalyzerFamily.LEVEL_DYNAMICS.value, ANALYZER_VERSION, {
        **params,
        "start_s": time_span.start_s,
        "end_s": time_span.end_s,
        "granularity": time_span.granularity.value,
    })
    if use_cache:
        hit = load_cached(key, cache_dir)
        if hit is not None:
            return hit

    limitations: list[DspLimitation] = []
    quality = DspQuality.OK
    np_status = numpy_status()
    if not np_status.available:
        obs = build_observation(
            observation_id=key,
            analyzer_id=AnalyzerFamily.LEVEL_DYNAMICS.value,
            subject=subject,
            time_span=time_span,
            values=[],
            quality=DspQuality.UNSUPPORTED,
            limitations=[np_status.limitation],
            provider_id="numpy",
            provider_version=np_status.version,
            method="unavailable",
            params=params,
            source_artifact_hash=buffer.artifact_hash,
            cache_key=key,
        )
        return obs

    mono = buffer.mono
    sr = buffer.sample_rate
    sample_peak = float(np.max(np.abs(buffer.samples))) if buffer.n_samples else 0.0
    rms = float(np.sqrt(np.mean(mono**2))) if len(mono) else 0.0
    crest = (sample_peak / rms) if rms > 1e-12 else None
    times, frame_rms, frame_peak = frame_signal(mono, sr, params["window_s"], params["hop_s"])
    short_times, short_rms, _ = frame_signal(mono, sr, params["short_term_s"], params["short_term_s"] / 2.0)
    macro_times, macro_rms, _ = frame_signal(mono, sr, params["macro_s"], max(params["hop_s"], params["macro_s"] / 4.0))

    silence_frac = float(np.mean(frame_rms <= SILENCE_RMS)) if len(frame_rms) else 1.0
    near_frac = float(np.mean((frame_rms > SILENCE_RMS) & (frame_rms <= NEAR_SILENCE_RMS))) if len(frame_rms) else 0.0
    ref = float(np.percentile(frame_rms, 75)) if len(frame_rms) else 0.0
    dip_mask = (frame_rms > NEAR_SILENCE_RMS) & (frame_rms <= ref * (10 ** (-12.0 / 20.0)))
    energy_dip_count = _run_count(dip_mask)

    if len(short_rms) >= 2:
        level_change_db = db(float(short_rms[-1]), float(short_rms[0]))
        st_range = db(float(np.percentile(short_rms, 95)), float(np.percentile(short_rms, 5)))
    else:
        level_change_db = 0.0
        st_range = None
        limitations.append(DspLimitation("SHORT_TERM_LEVEL_SPARSE", "fewer than 2 short-term windows"))
        quality = DspQuality.LIMITED

    micro = float(np.mean(frame_peak / np.maximum(frame_rms, 1e-12))) if len(frame_rms) else None
    if len(macro_rms) >= 2:
        macro_range = db(float(np.max(macro_rms)), max(float(np.min(macro_rms)), 1e-12))
    else:
        macro_range = None
        limitations.append(DspLimitation("MACRO_DYNAMICS_WINDOW_LONGER_THAN_SIGNAL", ""))
        quality = merge_quality(quality, DspQuality.LIMITED)

    tp_vals = []
    tp_quality = DspQuality.LIMITED
    for ch in range(buffer.channels):
        channel = buffer.samples[:, ch] if buffer.samples.ndim == 2 else buffer.samples
        peak, tp_lims, tp_q = true_peak_4x(channel, sr)
        limitations.extend(tp_lims)
        tp_quality = merge_quality(tp_quality, tp_q)
        if peak is not None:
            tp_vals.append(peak)
    true_peak = max(tp_vals) if tp_vals else None
    quality = merge_quality(quality, tp_quality)

    lufs, lufs_lims, lufs_q = integrated_loudness(mono, sr)
    limitations.extend(lufs_lims)
    quality = merge_quality(quality, lufs_q)
    loud_status = pyloudnorm_status()
    limitations.append(
        DspLimitation(
            "ITU_LRA_NOT_IMPLEMENTED",
            "pyloudnorm exposes integrated loudness only. Official loudness range is unsupported.",
        )
    )
    quality = merge_quality(quality, DspQuality.LIMITED)

    values = [
        MeasuredValue("sample_peak", sample_peak, unit="linear"),
        MeasuredValue("sample_peak_dbfs", amp_dbfs(sample_peak), unit="dBFS"),
        MeasuredValue("rms", rms, unit="linear"),
        MeasuredValue("rms_dbfs", amp_dbfs(rms), unit="dBFS"),
        MeasuredValue("crest_factor", crest, unit="ratio"),
        MeasuredValue("true_peak", true_peak, unit="linear"),
        MeasuredValue("true_peak_dbfs", None if true_peak is None else amp_dbfs(true_peak), unit="dBFS"),
        MeasuredValue("integrated_loudness", lufs, unit="LUFS"),
        MeasuredValue("loudness_range", None, unit="LU"),
        MeasuredValue("short_term_rms_mean", float(np.mean(short_rms)) if len(short_rms) else rms, unit="linear"),
        MeasuredValue("short_term_level_range_db", st_range, unit="dB"),
        MeasuredValue("micro_dynamics_crest_mean", micro, unit="ratio"),
        MeasuredValue("macro_dynamics_rms_range_db", macro_range, unit="dB"),
        MeasuredValue("silence_fraction", silence_frac, unit="ratio"),
        MeasuredValue("near_silence_fraction", near_frac, unit="ratio"),
        MeasuredValue("energy_dip_count", energy_dip_count, unit="count"),
        MeasuredValue("level_change_db", level_change_db, unit="dB"),
        MeasuredValue(
            "short_term_rms_series",
            [{"t_s": float(t), "rms": float(r)} for t, r in zip(short_times, short_rms)],
            unit="series",
        ),
    ]
    obs = build_observation(
        observation_id=key,
        analyzer_id=AnalyzerFamily.LEVEL_DYNAMICS.value,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=quality,
        limitations=_dedupe(limitations),
        provider_id="numpy+pyloudnorm+scipy",
        provider_version=f"{np_status.version}/{loud_status.version}",
        method="sample_peak+rms+4x_true_peak+pyloudnorm_integrated+framed_dynamics",
        params=params,
        source_artifact_hash=buffer.artifact_hash,
        cache_key=key,
    )
    if use_cache:
        store_cached(obs, cache_dir)
    return obs


def _run_count(mask: np.ndarray) -> int:
    if len(mask) == 0:
        return 0
    padded = np.concatenate([[False], mask.astype(bool), [False]])
    return int(np.sum((~padded[:-1]) & padded[1:]))


def _dedupe(items: list[DspLimitation]) -> list[DspLimitation]:
    seen: set[str] = set()
    out: list[DspLimitation] = []
    for item in items:
        if item.code in seen:
            continue
        seen.add(item.code)
        out.append(item)
    return out
