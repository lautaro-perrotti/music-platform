"""RELATIONAL DSP: measured overlap between two sources. Not muddy/masking."""

from __future__ import annotations

from typing import Any

import numpy as np

from copilot.audio.lowend import (
    band_energy_over_time,
    detect_transients,
    low_frequency_envelope,
    overlap_at_attacks,
    spectral_overlap_over_time,
)
from copilot.audio.physical_dsp_v2.audio import AudioBuffer, frame_signal
from copilot.audio.physical_dsp_v2.cache import cache_key, load_cached, store_cached
from copilot.audio.physical_dsp_v2.contract import (
    ANALYZER_VERSION,
    ONSET_COINCIDENCE_S,
    RELATIONAL_JOINT_SHARE_MIN,
    RELATIONAL_ONSET_MIN,
    RELATIONAL_TEMPORAL_MIN,
    SPECTRAL_BANDS_V1,
    TRANSIENT_MIN_DISTANCE_S,
)
from copilot.audio.physical_dsp_v2.observation import build_observation, merge_quality
from copilot.audio.physical_dsp_v2.providers import numpy_status, scipy_status
from copilot.audio.physical_dsp_v2.signal import band_energies, stft_power
from copilot.audio.physical_dsp_v2.stereo import analyze_stereo
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspLimitation,
    DspQuality,
    DspSubject,
    DspSubjectKind,
    MeasuredValue,
    TimeSpan,
)


def analyze_relational(
    buffer_a: AudioBuffer,
    buffer_b: AudioBuffer,
    *,
    subject: DspSubject | None = None,
    time_span: TimeSpan,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
    cache_dir=None,
) -> Any:
    params = dict(params or {})
    params.setdefault("spectral_bands", SPECTRAL_BANDS_V1)
    params.setdefault("onset_coincidence_s", ONSET_COINCIDENCE_S)
    pair_hash = f"{buffer_a.artifact_hash}:{buffer_b.artifact_hash}"
    subject = subject or DspSubject(
        kind=DspSubjectKind.SOURCE_PAIR,
        source_id=buffer_a.path,
        source_id_b=buffer_b.path,
        label="source_pair",
    )
    key = cache_key(pair_hash, AnalyzerFamily.RELATIONAL.value, ANALYZER_VERSION, {
        **params,
        "start_s": time_span.start_s,
        "end_s": time_span.end_s,
        "granularity": time_span.granularity.value,
    })
    if use_cache:
        hit = load_cached(key, cache_dir)
        if hit is not None:
            return hit

    limitations = [
        DspLimitation(
            "POTENTIAL_OVERLAP_IS_MEASURED_RELATIONSHIP",
            "Measured relationship only. Not a corrective suggestion.",
        ),
        DspLimitation(
            "PAIR_ALIGNED_BY_MIN_LENGTH",
            "Sources are compared on the shared leading duration. Cross-capture alignment remains LIMITED.",
        ),
    ]
    quality = DspQuality.OK
    if not numpy_status().available or not scipy_status().available:
        quality = DspQuality.UNSUPPORTED
        if numpy_status().limitation:
            limitations.append(numpy_status().limitation)
        if scipy_status().limitation:
            limitations.append(scipy_status().limitation)

    n = min(buffer_a.n_samples, buffer_b.n_samples)
    sr = min(buffer_a.sample_rate, buffer_b.sample_rate)
    if buffer_a.sample_rate != buffer_b.sample_rate:
        limitations.append(DspLimitation("SAMPLE_RATE_MISMATCH_USES_LEADING_SAMPLES", ""))
        quality = merge_quality(quality, DspQuality.LIMITED)
    a = buffer_a.samples[:n]
    b = buffer_b.samples[:n]
    mono_a = np.mean(a, axis=1) if a.ndim == 2 else a
    mono_b = np.mean(b, axis=1) if b.ndim == 2 else b

    freqs_a, _, power_a, la, qa = stft_power(mono_a, sr)
    freqs_b, _, power_b, lb, qb = stft_power(mono_b, sr)
    limitations.extend(la)
    limitations.extend(lb)
    quality = merge_quality(quality, qa, qb)
    bands_a = band_energies(freqs_a, power_a, params["spectral_bands"])
    bands_b = band_energies(freqs_b, power_b, params["spectral_bands"])
    occupancy: dict[str, float] = {}
    joint: dict[str, float] = {}
    for name in params["spectral_bands"]:
        ea = float(np.mean(bands_a[name]))
        eb = float(np.mean(bands_b[name]))
        tot_a = sum(float(np.mean(v)) for v in bands_a.values()) + 1e-12
        tot_b = sum(float(np.mean(v)) for v in bands_b.values()) + 1e-12
        sa = ea / tot_a
        sb = eb / tot_b
        joint[name] = float(min(sa, sb))
        occupancy[name] = float(sa * sb)

    times, rms_a, _ = frame_signal(mono_a, sr)
    _, rms_b, _ = frame_signal(mono_b, sr)
    n_fr = min(len(rms_a), len(rms_b))
    thr_a = max(float(np.percentile(rms_a[:n_fr], 50)) * 0.25, 1e-6) if n_fr else 1.0
    thr_b = max(float(np.percentile(rms_b[:n_fr], 50)) * 0.25, 1e-6) if n_fr else 1.0
    both = (rms_a[:n_fr] >= thr_a) & (rms_b[:n_fr] >= thr_b)
    temporal_frac = float(np.mean(both)) if n_fr else 0.0
    if n_fr >= 4 and float(np.std(rms_a[:n_fr])) > 0 and float(np.std(rms_b[:n_fr])) > 0:
        energy_corr = float(np.corrcoef(rms_a[:n_fr], rms_b[:n_fr])[0, 1])
    else:
        energy_corr = None

    onsets_a = [float(x["time_s"]) for x in detect_transients(a, sr, min_distance_s=TRANSIENT_MIN_DISTANCE_S)["attacks"]]
    onsets_b = [float(x["time_s"]) for x in detect_transients(b, sr, min_distance_s=TRANSIENT_MIN_DISTANCE_S)["attacks"]]
    coinc = 0
    window = float(params["onset_coincidence_s"])
    for ta in onsets_a:
        if any(abs(ta - tb) <= window for tb in onsets_b):
            coinc += 1
    denom = max(1, min(len(onsets_a), len(onsets_b)))
    onset_frac = float(coinc / denom) if (onsets_a and onsets_b) else 0.0

    low_a = band_energy_over_time(a, sr)
    low_b = band_energy_over_time(b, sr)
    spectral_low = spectral_overlap_over_time(low_a, low_b)
    env_b = low_frequency_envelope(b, sr)
    attacks_a = detect_transients(a, sr, min_distance_s=0.35, role="unknown")["attacks"]
    temporal_low = overlap_at_attacks(attacks_a, env_b)

    stereo_overlap = None
    if buffer_a.channels >= 2 and buffer_b.channels >= 2:
        st_a = analyze_stereo(buffer_a, subject=subject, time_span=time_span, use_cache=use_cache, cache_dir=cache_dir)
        st_b = analyze_stereo(buffer_b, subject=subject, time_span=time_span, use_cache=use_cache, cache_dir=cache_dir)
        wa = st_a.get("width")
        wb = st_b.get("width")
        if wa and wb and wa.value is not None and wb.value is not None:
            stereo_overlap = float(min(float(wa.value), float(wb.value)))

    mean_joint = float(np.mean(list(joint.values()))) if joint else 0.0
    potential = bool(
        mean_joint >= RELATIONAL_JOINT_SHARE_MIN
        or temporal_frac >= RELATIONAL_TEMPORAL_MIN
        or onset_frac >= RELATIONAL_ONSET_MIN
    )
    values = [
        MeasuredValue("frequency_overlap_joint_share", joint, unit="ratio"),
        MeasuredValue("spectral_occupancy_product", occupancy, unit="ratio"),
        MeasuredValue("mean_joint_share", mean_joint, unit="ratio"),
        MeasuredValue("temporal_both_active_fraction", temporal_frac, unit="ratio"),
        MeasuredValue("energy_envelope_correlation", energy_corr, unit="ratio"),
        MeasuredValue("transient_coincidence_fraction", onset_frac, unit="ratio"),
        MeasuredValue("onset_count_a", len(onsets_a), unit="count"),
        MeasuredValue("onset_count_b", len(onsets_b), unit="count"),
        MeasuredValue("lowend_band_mean_joint", spectral_low.get("band_mean_joint"), unit="ratio"),
        MeasuredValue("lowend_events_with_overlap", temporal_low.get("events_with_overlap"), unit="count"),
        MeasuredValue("stereo_min_width", stereo_overlap, unit="ratio"),
        MeasuredValue("potential_overlap", potential),
        MeasuredValue("relationship", "POTENTIAL_OVERLAP" if potential else "LOW_MEASURED_OVERLAP"),
    ]
    obs = build_observation(
        observation_id=key,
        analyzer_id=AnalyzerFamily.RELATIONAL.value,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=quality,
        limitations=limitations,
        provider_id="numpy+lowend.spectral_overlap+overlap_at_attacks",
        provider_version="lowend-obs-1",
        method="banded_joint_share+envelope_coincidence+wrapped_lowend_overlap",
        params=params,
        source_artifact_hash=pair_hash,
        cache_key=key,
    )
    if use_cache:
        store_cached(obs, cache_dir)
    return obs
