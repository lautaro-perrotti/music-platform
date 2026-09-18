"""TRANSIENTS: wrap copilot.audio.lowend.detect_transients plus envelope facts."""

from __future__ import annotations

from typing import Any

import numpy as np

from copilot.audio.lowend import detect_transients
from copilot.audio.physical_dsp_v2.audio import AudioBuffer
from copilot.audio.physical_dsp_v2.cache import cache_key, load_cached, store_cached
from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION, TRANSIENT_MIN_DISTANCE_S
from copilot.audio.physical_dsp_v2.observation import build_observation, merge_quality
from copilot.audio.physical_dsp_v2.providers import numpy_status, scipy_status
from copilot.audio.physical_dsp_v2.signal import envelope_attack_decay
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspLimitation,
    DspQuality,
    DspSubject,
    MeasuredValue,
    TimeSpan,
)


def analyze_transients(
    buffer: AudioBuffer,
    *,
    subject: DspSubject,
    time_span: TimeSpan,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
    cache_dir=None,
) -> Any:
    params = dict(params or {})
    params.setdefault("min_distance_s", TRANSIENT_MIN_DISTANCE_S)
    key = cache_key(buffer.artifact_hash, AnalyzerFamily.TRANSIENTS.value, ANALYZER_VERSION, {
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
            "ONSETS_FROM_LOWEND_DETECT_TRANSIENTS",
            "Reuses copilot.audio.lowend.detect_transients (rms flux). Energy onsets, not instrument IDs.",
        )
    ]
    quality = DspQuality.OK
    np_status = numpy_status()
    sc_status = scipy_status()
    if not np_status.available or not sc_status.available:
        quality = DspQuality.UNSUPPORTED
        if np_status.limitation:
            limitations.append(np_status.limitation)
        if sc_status.limitation:
            limitations.append(sc_status.limitation)
        return build_observation(
            observation_id=key,
            analyzer_id=AnalyzerFamily.TRANSIENTS.value,
            subject=subject,
            time_span=time_span,
            values=[],
            quality=quality,
            limitations=limitations,
            provider_id="lowend.detect_transients",
            provider_version="lowend-obs-1",
            method="unavailable",
            params=params,
            source_artifact_hash=buffer.artifact_hash,
            cache_key=key,
        )

    detected = detect_transients(
        buffer.samples,
        buffer.sample_rate,
        min_distance_s=float(params["min_distance_s"]),
        role="unknown",
    )
    attacks = list(detected.get("attacks") or [])
    times = [float(item["time_s"]) + float(buffer.origin_s) for item in attacks]
    strengths = [float(item.get("strength") or 0.0) for item in attacks]
    duration = max(time_span.duration_s, buffer.duration_s, 1e-9)
    ioi = np.diff(np.asarray(times, dtype=np.float64)) if len(times) > 1 else np.asarray([], dtype=np.float64)
    attack_s, decay_s = envelope_attack_decay(buffer.mono, buffer.sample_rate)
    if attack_s is None:
        limitations.append(DspLimitation("ATTACK_DECAY_UNRESOLVED", "envelope 10/90% points not found"))
        quality = merge_quality(quality, DspQuality.LIMITED)
    micro = None
    if strengths and float(np.mean(strengths)) > 0:
        micro = float(np.std(strengths) / (np.mean(strengths) + 1e-12))
    hop = 0.5
    bins = max(1, int(np.ceil(duration / hop)))
    activity = [0] * bins
    for t in times:
        idx = min(bins - 1, max(0, int(t / hop)))
        activity[idx] += 1
    if buffer.duration_s < 0.04:
        limitations.append(DspLimitation("SIGNAL_TOO_SHORT_FOR_TRANSIENT_GRID", ""))
        quality = merge_quality(quality, DspQuality.LIMITED)

    values = [
        MeasuredValue("onset_times_s", times, unit="s"),
        MeasuredValue("onset_count", int(len(times)), unit="count"),
        MeasuredValue("onset_density_per_s", float(len(times) / duration), unit="1/s"),
        MeasuredValue("onset_strengths", strengths, unit="linear"),
        MeasuredValue("mean_ioi_s", float(np.mean(ioi)) if len(ioi) else None, unit="s"),
        MeasuredValue("median_ioi_s", float(np.median(ioi)) if len(ioi) else None, unit="s"),
        MeasuredValue("ioi_std_s", float(np.std(ioi)) if len(ioi) else None, unit="s"),
        MeasuredValue("attack_s", attack_s, unit="s"),
        MeasuredValue("decay_s", decay_s, unit="s"),
        MeasuredValue("microdynamic_variation", micro, unit="ratio"),
        MeasuredValue(
            "event_activity_over_time",
            [{"t_s": i * hop, "onset_count": c} for i, c in enumerate(activity)],
            unit="series",
        ),
        MeasuredValue("method", str(detected.get("method") or "rms_flux")),
    ]
    obs = build_observation(
        observation_id=key,
        analyzer_id=AnalyzerFamily.TRANSIENTS.value,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=quality,
        limitations=limitations,
        provider_id="lowend.detect_transients",
        provider_version="lowend-obs-1",
        method="rms_flux_onsets+envelope_attack_decay",
        params=params,
        source_artifact_hash=buffer.artifact_hash,
        cache_key=key,
    )
    if use_cache:
        store_cached(obs, cache_dir)
    return obs
