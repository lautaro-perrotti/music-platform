"""Run PHYSICAL_DSP_V2 analyzer families. Independent analyzers share no mutable state."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from copilot.audio.physical_dsp_v2.audio import AudioBuffer, load_audio
from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION, DEFAULT_PARAMS
from copilot.audio.physical_dsp_v2.granularity import iter_spans, region_span
from copilot.audio.physical_dsp_v2.judgment import assert_bundle_no_judgment
from copilot.audio.physical_dsp_v2.level_dynamics import analyze_level_dynamics
from copilot.audio.physical_dsp_v2.observation import build_observation
from copilot.audio.physical_dsp_v2.providers import provider_inventory
from copilot.audio.physical_dsp_v2.relational import analyze_relational
from copilot.audio.physical_dsp_v2.rhythm import analyze_rhythm
from copilot.audio.physical_dsp_v2.spectrum import analyze_spectrum
from copilot.audio.physical_dsp_v2.stereo import analyze_stereo
from copilot.audio.physical_dsp_v2.timbre import analyze_timbre
from copilot.audio.physical_dsp_v2.tonal import analyze_tonal
from copilot.audio.physical_dsp_v2.transients import analyze_transients
from copilot.schemas.dsp import (
    AnalyzerFamily,
    CAPABILITY_ID,
    DspBundle,
    DspGranularity,
    DspLimitation,
    DspObservation,
    DspQuality,
    DspSubject,
    DspSubjectKind,
    MeasuredValue,
)

SINGLE_SOURCE: dict[str, Callable[..., DspObservation]] = {
    AnalyzerFamily.LEVEL_DYNAMICS.value: analyze_level_dynamics,
    AnalyzerFamily.SPECTRUM.value: analyze_spectrum,
    AnalyzerFamily.TRANSIENTS.value: analyze_transients,
    AnalyzerFamily.STEREO.value: analyze_stereo,
    AnalyzerFamily.RHYTHM.value: analyze_rhythm,
    AnalyzerFamily.TONAL.value: analyze_tonal,
    AnalyzerFamily.TIMBRE.value: analyze_timbre,
}


def analyze_buffer(
    buffer: AudioBuffer,
    *,
    subject: DspSubject | None = None,
    analyzers: Sequence[str] | None = None,
    granularities: Sequence[DspGranularity] = (DspGranularity.REGION,),
    start_s: float | None = None,
    end_s: float | None = None,
    start_qn: float | None = None,
    end_qn: float | None = None,
    tempo_bpm: float | None = None,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
    cache_dir: Any = None,
    include_canonical_wraps: bool = False,
) -> DspBundle:
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    subject = subject or DspSubject(kind=DspSubjectKind.SOURCE, source_id=buffer.path, label=buffer.path)
    params = {**DEFAULT_PARAMS, **(params or {})}
    wanted = list(analyzers or SINGLE_SOURCE.keys())
    onset_times: list[float] = []
    if DspGranularity.EVENT in granularities:
        probe_span = region_span(
            buffer, start_s=start_s, end_s=end_s, start_qn=start_qn, end_qn=end_qn, tempo_bpm=tempo_bpm
        )
        trans = analyze_transients(
            buffer, subject=subject, time_span=probe_span, params=params, use_cache=use_cache, cache_dir=cache_dir
        )
        onset_times = list(trans.get("onset_times_s").value or []) if trans.get("onset_times_s") else []
    spans, gran_lims = iter_spans(
        buffer,
        granularities=list(granularities),
        start_s=start_s,
        end_s=end_s,
        start_qn=start_qn,
        end_qn=end_qn,
        tempo_bpm=tempo_bpm,
        onset_times_s=onset_times,
    )
    observations: list[DspObservation] = []
    hits = 0
    misses = 0
    for span in spans:
        sliced = buffer.slice_seconds(span.start_s, span.end_s)
        report_span = span
        if sliced.n_samples == 0:
            continue
        for analyzer_id in wanted:
            fn = SINGLE_SOURCE.get(analyzer_id)
            if fn is None:
                continue
            obs = fn(
                sliced,
                subject=subject,
                time_span=report_span,
                params=params,
                use_cache=use_cache,
                cache_dir=cache_dir,
            )
            if obs.provenance.cache_hit:
                hits += 1
            else:
                misses += 1
            observations.append(obs)
    if include_canonical_wraps and buffer.path:
        from copilot.audio.physical_dsp_v2.wrappers import wrap_fullmix

        region = spans[0] if spans else region_span(buffer, start_s=start_s, end_s=end_s)
        observations.append(
            wrap_fullmix(buffer.path, subject=subject, time_span=region, use_cache=use_cache)
        )
        misses += 1
    if gran_lims:
        observations.append(
            build_observation(
                observation_id=f"granularity:{buffer.artifact_hash}",
                analyzer_id=AnalyzerFamily.GRANULARITY.value,
                subject=subject,
                time_span=spans[0] if spans else region_span(buffer),
                values=[
                    MeasuredValue("requested", [g.value for g in granularities]),
                    MeasuredValue("span_count", len(spans), unit="count"),
                ],
                quality=DspQuality.LIMITED,
                limitations=gran_lims,
                provider_id="physical-dsp-v2",
                provider_version=ANALYZER_VERSION,
                method="iter_spans",
                params={"granularities": [g.value for g in granularities]},
                source_artifact_hash=buffer.artifact_hash,
                cache_key=f"granularity:{buffer.artifact_hash}",
            )
        )
    quality = DspQuality.OK
    if any(obs.quality is DspQuality.UNSUPPORTED for obs in observations):
        quality = DspQuality.UNSUPPORTED
    elif any(obs.quality is DspQuality.LIMITED for obs in observations):
        quality = DspQuality.LIMITED
    bundle = DspBundle(
        capability_id=CAPABILITY_ID,
        analyzer_version=ANALYZER_VERSION,
        observations=observations,
        wall_s=time.perf_counter() - wall0,
        cpu_s=time.process_time() - cpu0,
        cache_hits=hits,
        cache_misses=misses,
        quality=quality,
        limitations=_inventory_limitations(),
    )
    assert_bundle_no_judgment(bundle)
    return bundle


def analyze_path(path: Path | str, **kwargs: Any) -> DspBundle:
    return analyze_buffer(load_audio(path), **kwargs)


def analyze_pair(
    path_a: Path | str | AudioBuffer,
    path_b: Path | str | AudioBuffer,
    *,
    subject: DspSubject | None = None,
    start_s: float | None = None,
    end_s: float | None = None,
    tempo_bpm: float | None = None,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
    cache_dir: Any = None,
    include_lowend_wrap: bool = False,
) -> DspBundle:
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    buf_a = path_a if isinstance(path_a, AudioBuffer) else load_audio(path_a)
    buf_b = path_b if isinstance(path_b, AudioBuffer) else load_audio(path_b)
    span = region_span(buf_a, start_s=start_s, end_s=end_s, tempo_bpm=tempo_bpm)
    span = span.model_copy(update={"granularity": DspGranularity.SOURCE_PAIR})
    subject = subject or DspSubject(
        kind=DspSubjectKind.SOURCE_PAIR,
        source_id=buf_a.path,
        source_id_b=buf_b.path,
        label="source_pair",
    )
    obs = analyze_relational(
        buf_a,
        buf_b,
        subject=subject,
        time_span=span,
        params=params,
        use_cache=use_cache,
        cache_dir=cache_dir,
    )
    observations = [obs]
    if include_lowend_wrap and buf_a.path and buf_b.path:
        from copilot.audio.physical_dsp_v2.wrappers import audio_asset_from_wav, wrap_lowend

        views = {
            "kick": audio_asset_from_wav(buf_a.path),
            "bass": audio_asset_from_wav(buf_b.path),
            "master": audio_asset_from_wav(buf_a.path),
        }
        observations.append(wrap_lowend(views, subject=subject, time_span=span))
    bundle = DspBundle(
        capability_id=CAPABILITY_ID,
        analyzer_version=ANALYZER_VERSION,
        observations=observations,
        wall_s=time.perf_counter() - wall0,
        cpu_s=time.process_time() - cpu0,
        cache_hits=int(obs.provenance.cache_hit),
        cache_misses=int(not obs.provenance.cache_hit),
        quality=obs.quality,
        limitations=_inventory_limitations(),
    )
    assert_bundle_no_judgment(bundle)
    return bundle


def _inventory_limitations() -> list[DspLimitation]:
    out: list[DspLimitation] = []
    for status in provider_inventory():
        if status.limitation and not status.available:
            out.append(status.limitation)
    return out
