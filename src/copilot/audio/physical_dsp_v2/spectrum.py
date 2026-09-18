"""SPECTRUM: versioned bands, centroid, rolloff, slope, flux, change over time."""

from __future__ import annotations

from typing import Any

import numpy as np

from copilot.audio.physical_dsp_v2.audio import AudioBuffer
from copilot.audio.physical_dsp_v2.cache import cache_key, load_cached, store_cached
from copilot.audio.physical_dsp_v2.contract import (
    ANALYZER_VERSION,
    SPECTRAL_BAND_DEFINITION_ID,
    SPECTRAL_BANDS_V1,
    STFT_NPERSEG,
)
from copilot.audio.physical_dsp_v2.observation import build_observation, merge_quality
from copilot.audio.physical_dsp_v2.providers import numpy_status, scipy_status
from copilot.audio.physical_dsp_v2.signal import (
    band_energies,
    spectral_centroid,
    spectral_flux,
    spectral_rolloff,
    spectral_slope,
    stft_power,
)
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspLimitation,
    DspQuality,
    DspSubject,
    MeasuredValue,
    TimeSpan,
)


def analyze_spectrum(
    buffer: AudioBuffer,
    *,
    subject: DspSubject,
    time_span: TimeSpan,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
    cache_dir=None,
) -> Any:
    params = dict(params or {})
    bands = params.get("spectral_bands") or SPECTRAL_BANDS_V1
    params["spectral_bands"] = bands
    params.setdefault("spectral_band_definition_id", SPECTRAL_BAND_DEFINITION_ID)
    params.setdefault("nperseg", STFT_NPERSEG)
    key = cache_key(buffer.artifact_hash, AnalyzerFamily.SPECTRUM.value, ANALYZER_VERSION, {
        **params,
        "start_s": time_span.start_s,
        "end_s": time_span.end_s,
        "granularity": time_span.granularity.value,
    })
    if use_cache:
        hit = load_cached(key, cache_dir)
        if hit is not None:
            return hit

    np_status = numpy_status()
    sc_status = scipy_status()
    limitations: list[DspLimitation] = []
    quality = DspQuality.OK
    if not np_status.available:
        limitations.append(np_status.limitation)
        quality = DspQuality.UNSUPPORTED
    if not sc_status.available:
        limitations.append(sc_status.limitation)
        quality = DspQuality.UNSUPPORTED
    if quality is DspQuality.UNSUPPORTED:
        return build_observation(
            observation_id=key,
            analyzer_id=AnalyzerFamily.SPECTRUM.value,
            subject=subject,
            time_span=time_span,
            values=[],
            quality=quality,
            limitations=limitations,
            provider_id="scipy",
            provider_version=sc_status.version,
            method="unavailable",
            params=params,
            source_artifact_hash=buffer.artifact_hash,
            cache_key=key,
        )

    freqs, times, power, stft_lims, stft_q = stft_power(buffer.mono, buffer.sample_rate, nperseg=params["nperseg"])
    limitations.extend(stft_lims)
    quality = merge_quality(quality, stft_q)
    series = band_energies(freqs, power, bands)
    totals = {name: float(np.sum(vals)) for name, vals in series.items()}
    all_e = sum(totals.values())
    ratios = {name: (energy / all_e if all_e > 0 else 0.0) for name, energy in totals.items()}
    centroid = spectral_centroid(freqs, power)
    rolloff = spectral_rolloff(freqs, power)
    slope = spectral_slope(freqs, power)
    flux = spectral_flux(power)
    flux_mean = float(np.mean(flux)) if len(flux) else 0.0
    change = {
        name: float(np.std(vals) / (np.mean(vals) + 1e-12)) if len(vals) else 0.0
        for name, vals in series.items()
    }
    if all_e <= 0:
        limitations.append(DspLimitation("SPECTRAL_ENERGY_NEAR_ZERO", ""))
        quality = merge_quality(quality, DspQuality.LIMITED)
    nyquist = buffer.sample_rate / 2.0
    if nyquist < 20000.0 and bands.get("air", (0, 0))[1] > nyquist:
        limitations.append(
            DspLimitation("AIR_BAND_ABOVE_NYQUIST", f"nyquist={nyquist:.1f}Hz; air band truncated")
        )
        quality = merge_quality(quality, DspQuality.LIMITED)

    values = [
        MeasuredValue("band_definition_id", params["spectral_band_definition_id"]),
        MeasuredValue("band_energy", totals, unit="linear"),
        MeasuredValue("band_energy_ratio", ratios, unit="ratio"),
        MeasuredValue("spectral_centroid_hz", centroid, unit="Hz"),
        MeasuredValue("spectral_rolloff_hz", rolloff, unit="Hz"),
        MeasuredValue("spectral_slope", slope, unit="log-log"),
        MeasuredValue("spectral_flux_mean", flux_mean, unit="ratio"),
        MeasuredValue("band_energy_variation", change, unit="ratio"),
        MeasuredValue(
            "band_energy_over_time",
            {
                "times_s": [float(t) for t in times],
                "bands": {name: [float(v) for v in vals] for name, vals in series.items()},
            },
            unit="series",
        ),
    ]
    obs = build_observation(
        observation_id=key,
        analyzer_id=AnalyzerFamily.SPECTRUM.value,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=quality,
        limitations=limitations,
        provider_id="scipy.signal.stft",
        provider_version=sc_status.version,
        method="stft_power+versioned_bands+centroid_rolloff_slope_flux",
        params=params,
        source_artifact_hash=buffer.artifact_hash,
        cache_key=key,
    )
    if use_cache:
        store_cached(obs, cache_dir)
    return obs
