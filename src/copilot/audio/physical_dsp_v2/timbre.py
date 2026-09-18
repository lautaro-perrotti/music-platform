"""TIMBRE FACTS: envelope, brightness, roughness, noisiness, harmonicity, ZCR."""

from __future__ import annotations

from typing import Any

import numpy as np

from copilot.audio.physical_dsp_v2.audio import AudioBuffer
from copilot.audio.physical_dsp_v2.cache import cache_key, load_cached, store_cached
from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION, STFT_NPERSEG
from copilot.audio.physical_dsp_v2.observation import build_observation, merge_quality
from copilot.audio.physical_dsp_v2.providers import scipy_status
from copilot.audio.physical_dsp_v2.signal import (
    envelope_attack_decay,
    peak_pair_roughness,
    spectral_centroid,
    spectral_flatness,
    stft_power,
    zero_crossing_rate,
)
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspLimitation,
    DspQuality,
    DspSubject,
    MeasuredValue,
    TimeSpan,
)


def analyze_timbre(
    buffer: AudioBuffer,
    *,
    subject: DspSubject,
    time_span: TimeSpan,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
    cache_dir=None,
) -> Any:
    params = dict(params or {})
    params.setdefault("nperseg", STFT_NPERSEG)
    params.setdefault("brightness_cutoff_hz", 1500.0)
    key = cache_key(buffer.artifact_hash, AnalyzerFamily.TIMBRE.value, ANALYZER_VERSION, {
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
    sc = scipy_status()
    if not sc.available:
        return build_observation(
            observation_id=key,
            analyzer_id=AnalyzerFamily.TIMBRE.value,
            subject=subject,
            time_span=time_span,
            values=[],
            quality=DspQuality.UNSUPPORTED,
            limitations=[sc.limitation],
            provider_id="scipy",
            provider_version=sc.version,
            method="unavailable",
            params=params,
            source_artifact_hash=buffer.artifact_hash,
            cache_key=key,
        )

    freqs, times, power, stft_lims, stft_q = stft_power(buffer.mono, buffer.sample_rate, nperseg=params["nperseg"])
    limitations.extend(stft_lims)
    quality = merge_quality(quality, stft_q)
    mag = np.sum(power, axis=1) if power.ndim == 2 else np.asarray(power)
    env = _spectral_envelope(freqs, mag)
    centroid = spectral_centroid(freqs, power)
    cutoff = float(params["brightness_cutoff_hz"])
    hi = float(np.sum(mag[freqs >= cutoff]))
    tot = float(np.sum(mag))
    brightness = (hi / tot) if tot > 0 else None
    flat = spectral_flatness(power)
    noisiness = flat
    harmonicity = None if flat is None else float(max(0.0, min(1.0, 1.0 - flat)))
    roughness = peak_pair_roughness(freqs, mag)
    if roughness is None:
        limitations.append(DspLimitation("ROUGHNESS_PEAKS_UNRESOLVED", "need >=2 spectral peaks"))
        quality = merge_quality(quality, DspQuality.LIMITED)
    attack_s, decay_s = envelope_attack_decay(buffer.mono, buffer.sample_rate)
    zcr = zero_crossing_rate(buffer.mono)
    centroids = []
    if power.ndim == 2:
        for i in range(power.shape[1]):
            c = spectral_centroid(freqs, power[:, i : i + 1])
            if c is not None:
                centroids.append(c)
    centroid_std = float(np.std(centroids)) if len(centroids) >= 2 else None
    if centroid_std is None:
        limitations.append(DspLimitation("TEMPORAL_TIMBRE_VARIATION_SPARSE", ""))
        quality = merge_quality(quality, DspQuality.LIMITED)

    values = [
        MeasuredValue("spectral_envelope", env, unit="dB"),
        MeasuredValue("spectral_centroid_hz", centroid, unit="Hz"),
        MeasuredValue("brightness", brightness, unit="ratio"),
        MeasuredValue("roughness", roughness, unit="ratio"),
        MeasuredValue("noisiness", noisiness, unit="ratio"),
        MeasuredValue("harmonicity", harmonicity, unit="ratio"),
        MeasuredValue("attack_s", attack_s, unit="s"),
        MeasuredValue("decay_s", decay_s, unit="s"),
        MeasuredValue("zero_crossing_rate", zcr, unit="ratio"),
        MeasuredValue("temporal_centroid_std_hz", centroid_std, unit="Hz"),
        MeasuredValue(
            "centroid_over_time",
            [{"t_s": float(t), "centroid_hz": c} for t, c in zip(times[: len(centroids)], centroids)],
            unit="series",
        ),
    ]
    obs = build_observation(
        observation_id=key,
        analyzer_id=AnalyzerFamily.TIMBRE.value,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=quality,
        limitations=limitations,
        provider_id="numpy+scipy",
        provider_version=sc.version,
        method="stft_envelope+flatness+zcr+sethres_like_roughness",
        params=params,
        source_artifact_hash=buffer.artifact_hash,
        cache_key=key,
    )
    if use_cache:
        store_cached(obs, cache_dir)
    return obs


def _spectral_envelope(freqs: np.ndarray, mag: np.ndarray, *, points: int = 32) -> list[dict[str, float]]:
    if len(freqs) == 0:
        return []
    db = 10.0 * np.log10(np.maximum(mag, 1e-20))
    win = max(1, len(db) // points)
    out: list[dict[str, float]] = []
    for i in range(0, len(db), win):
        sl = db[i : i + win]
        fr = freqs[i : i + win]
        out.append({"hz": float(np.mean(fr)), "db": float(np.mean(sl))})
        if len(out) >= points:
            break
    return out
