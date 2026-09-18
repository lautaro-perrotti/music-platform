"""STEREO: L/R, correlation, M/S, width, frequency-dependent width, mono compatibility."""

from __future__ import annotations

from typing import Any

import numpy as np

from copilot.audio.physical_dsp_v2.audio import AudioBuffer, db, frame_signal
from copilot.audio.physical_dsp_v2.cache import cache_key, load_cached, store_cached
from copilot.audio.physical_dsp_v2.contract import (
    ANALYZER_VERSION,
    FREQ_WIDTH_MIN_ENERGY,
    HOP_S,
    SPECTRAL_BANDS_V1,
    STFT_NPERSEG,
    WINDOW_S,
)
from copilot.audio.physical_dsp_v2.observation import build_observation, merge_quality
from copilot.audio.physical_dsp_v2.providers import numpy_status, scipy_status
from copilot.audio.physical_dsp_v2.signal import stft_power
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspLimitation,
    DspQuality,
    DspSubject,
    MeasuredValue,
    TimeSpan,
)


def analyze_stereo(
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
    params.setdefault("spectral_bands", SPECTRAL_BANDS_V1)
    key = cache_key(buffer.artifact_hash, AnalyzerFamily.STEREO.value, ANALYZER_VERSION, {
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
    if buffer.channels < 2 or buffer.right is None:
        limitations.append(DspLimitation("MONO_OR_SINGLE_CHANNEL", "stereo facts require two channels"))
        obs = build_observation(
            observation_id=key,
            analyzer_id=AnalyzerFamily.STEREO.value,
            subject=subject,
            time_span=time_span,
            values=[
                MeasuredValue("channel_count", buffer.channels, unit="count"),
                MeasuredValue("correlation", None),
                MeasuredValue("width", None),
            ],
            quality=DspQuality.LIMITED,
            limitations=limitations,
            provider_id="numpy",
            provider_version=np_status.version,
            method="channel_count",
            params=params,
            source_artifact_hash=buffer.artifact_hash,
            cache_key=key,
        )
        if use_cache:
            store_cached(obs, cache_dir)
        return obs

    left = buffer.left
    right = buffer.right
    l_rms = float(np.sqrt(np.mean(left**2))) if len(left) else 0.0
    r_rms = float(np.sqrt(np.mean(right**2))) if len(right) else 0.0
    balance_db = db(l_rms, r_rms) if (l_rms > 1e-12 or r_rms > 1e-12) else 0.0
    if np.std(left) > 1e-12 and np.std(right) > 1e-12:
        corr = float(np.corrcoef(left, right)[0, 1])
    else:
        corr = None
        limitations.append(DspLimitation("CORRELATION_UNDEFINED_LOW_VARIANCE", ""))
        quality = DspQuality.LIMITED
    mid = 0.5 * (left + right)
    side = 0.5 * (left - right)
    mid_e = float(np.mean(mid * mid)) if len(mid) else 0.0
    side_e = float(np.mean(side * side)) if len(side) else 0.0
    ms_ratio = (side_e / mid_e) if mid_e > 1e-18 else None
    width = (np.sqrt(side_e) / (np.sqrt(mid_e) + 1e-12)) if (mid_e + side_e) > 0 else None
    mono_compat = None if corr is None else float(max(-1.0, min(1.0, corr)))
    if corr is not None and corr < 0:
        limitations.append(
            DspLimitation(
                "NEGATIVE_CORRELATION_MONO_SUM_CANCELLATION",
                "L/R correlation is negative; mono sum may cancel. Fact only.",
            )
        )
        quality = merge_quality(quality, DspQuality.LIMITED)

    times, l_frames, _ = frame_signal(left, buffer.sample_rate, params["window_s"], params["hop_s"])
    _, r_frames, _ = frame_signal(right, buffer.sample_rate, params["window_s"], params["hop_s"])
    hop = max(1, int(round(params["hop_s"] * buffer.sample_rate)))
    win = max(1, int(round(params["window_s"] * buffer.sample_rate)))
    corr_series: list[float | None] = []
    for i in range(len(times)):
        a = i * hop
        sl_l = left[a : a + win]
        sl_r = right[a : a + win]
        if len(sl_l) and len(sl_r) and np.std(sl_l) > 1e-12 and np.std(sl_r) > 1e-12:
            corr_series.append(float(np.corrcoef(sl_l, sl_r)[0, 1]))
        else:
            corr_series.append(None)
    present = [c for c in corr_series if c is not None]
    stereo_change = float(np.std(present)) if len(present) >= 2 else None

    freq_width, freq_lims, freq_q = _frequency_width(left, right, buffer.sample_rate, params["spectral_bands"])
    limitations.extend(freq_lims)
    quality = merge_quality(quality, freq_q)

    values = [
        MeasuredValue("left_rms", l_rms, unit="linear"),
        MeasuredValue("right_rms", r_rms, unit="linear"),
        MeasuredValue("lr_balance_db", balance_db, unit="dB"),
        MeasuredValue("correlation", corr, unit="ratio"),
        MeasuredValue("mid_energy", mid_e, unit="linear"),
        MeasuredValue("side_energy", side_e, unit="linear"),
        MeasuredValue("ms_ratio", ms_ratio, unit="ratio"),
        MeasuredValue("width", None if width is None else float(width), unit="ratio"),
        MeasuredValue("mono_compatibility", mono_compat, unit="ratio"),
        MeasuredValue("frequency_dependent_width", freq_width, unit="ratio"),
        MeasuredValue("stereo_change_correlation_std", stereo_change, unit="ratio"),
        MeasuredValue(
            "stereo_over_time",
            [
                {"t_s": float(t), "left_rms": float(l), "right_rms": float(r), "correlation": c}
                for t, l, r, c in zip(times, l_frames, r_frames, corr_series)
            ],
            unit="series",
        ),
    ]
    obs = build_observation(
        observation_id=key,
        analyzer_id=AnalyzerFamily.STEREO.value,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=quality,
        limitations=limitations,
        provider_id="numpy",
        provider_version=np_status.version,
        method="lr_ms_correlation+banded_ms_width",
        params=params,
        source_artifact_hash=buffer.artifact_hash,
        cache_key=key,
    )
    if use_cache:
        store_cached(obs, cache_dir)
    return obs


def _frequency_width(
    left: np.ndarray,
    right: np.ndarray,
    sample_rate: int,
    bands: dict[str, tuple[float, float]],
) -> tuple[dict[str, float | None], list[DspLimitation], DspQuality]:
    limitations: list[DspLimitation] = []
    quality = DspQuality.OK
    if not scipy_status().available:
        return {}, [scipy_status().limitation], DspQuality.UNSUPPORTED
    lf, _, lp, ll, lq = stft_power(left, sample_rate, nperseg=STFT_NPERSEG)
    rf, _, rp, rl, rq = stft_power(right, sample_rate, nperseg=STFT_NPERSEG)
    limitations.extend(ll)
    limitations.extend(rl)
    quality = merge_quality(lq, rq)
    if lp.shape != rp.shape:
        return {}, [DspLimitation("FREQ_WIDTH_STFT_SHAPE_MISMATCH", "")], DspQuality.LIMITED
    mid = 0.5 * (lp + rp)
    # side approximation from channel difference of magnitudes (not a complex M/S STFT)
    side = 0.5 * np.abs(lp - rp)
    limitations.append(
        DspLimitation(
            "FREQ_WIDTH_MAGNITUDE_MS_APPROXIMATION",
            "Per-band width uses STFT magnitude mid/side, not a complex M/S transform.",
        )
    )
    quality = merge_quality(quality, DspQuality.LIMITED)
    out: dict[str, float | None] = {}
    for name, (lo, hi) in bands.items():
        mask = (lf >= lo) & (lf < hi)
        if not np.any(mask):
            out[name] = None
            continue
        mid_e = float(np.mean(mid[mask]))
        side_e = float(np.mean(side[mask]))
        if mid_e + side_e < FREQ_WIDTH_MIN_ENERGY:
            out[name] = None
            continue
        out[name] = float(np.sqrt(side_e) / (np.sqrt(mid_e) + 1e-12))
    return out, limitations, quality
