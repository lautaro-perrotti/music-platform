"""Wrap frozen FullMix / LowEnd functions. Do not copy their implementations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import soundfile as sf

from copilot.audio.fullmix import compute_fullmix_observation
from copilot.audio.lowend_features import compute_lowend_features
from copilot.audio.physical_dsp_v2.cache import cache_key
from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION
from copilot.audio.physical_dsp_v2.observation import build_observation
from copilot.schemas.dsp import (
    AnalyzerFamily,
    DspLimitation,
    DspQuality,
    DspSubject,
    DspSubjectKind,
    MeasuredValue,
    TimeSpan,
)


def wrap_fullmix(
    path: Path | str,
    *,
    subject: DspSubject,
    time_span: TimeSpan,
    region_id: str = "PHYSICAL_DSP_V2",
    use_cache: bool = True,
) -> Any:
    obs = compute_fullmix_observation(path, region_id=region_id, use_cache=use_cache)
    artifact = obs.audio_sha256
    key = cache_key(artifact, AnalyzerFamily.FULLMIX_WRAP.value, ANALYZER_VERSION, {
        "region_id": region_id,
        "fullmix_configuration_hash": obs.configuration_hash,
        "start_s": time_span.start_s,
        "end_s": time_span.end_s,
    })
    events = [
        {
            "kind": ev.kind.value,
            "start_s": ev.start_s,
            "end_s": ev.end_s,
            "relative_drop_db": ev.relative_drop_db,
        }
        for ev in obs.energy_events
    ]
    values = [
        MeasuredValue("fullmix_analyzer_id", obs.analyzer_id),
        MeasuredValue("energy_event_count", len(obs.energy_events), unit="count"),
        MeasuredValue("energy_events", events),
        MeasuredValue("spectral_event_count", len(obs.spectral_events), unit="count"),
        MeasuredValue("transient_count", None if obs.transient is None else obs.transient.transient_count, unit="count"),
        MeasuredValue("stereo_frame_count", len(obs.stereo_frames), unit="count"),
        MeasuredValue("dynamics_window_count", len(obs.dynamics), unit="count"),
        MeasuredValue("fullmix_limitations", list(obs.limitations)),
    ]
    return build_observation(
        observation_id=key,
        analyzer_id=AnalyzerFamily.FULLMIX_WRAP.value,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=DspQuality.OK,
        limitations=[
            DspLimitation("WRAPPED_FULLMIX_OBS_1", "Canonical compute_fullmix_observation. MEASURE only."),
        ],
        provider_id="fullmix-obs-1",
        provider_version=obs.analyzer_sha256[:12],
        method="compute_fullmix_observation",
        params={"region_id": region_id, "configuration_hash": obs.configuration_hash},
        source_artifact_hash=artifact,
        cache_key=key,
        cache_hit=bool(obs.cache_hit),
    )


def wrap_lowend(
    views: dict[str, Any],
    *,
    subject: DspSubject | None = None,
    time_span: TimeSpan,
) -> Any:
    features = compute_lowend_features(views)
    subject = subject or DspSubject(kind=DspSubjectKind.SOURCE_PAIR, label="lowend_wrap")
    hashes = []
    for asset in views.values():
        path = getattr(asset, "file_path", None)
        hashes.append(str(path or id(asset)))
    artifact = "|".join(sorted(hashes))
    key = cache_key(artifact, AnalyzerFamily.LOWEND_WRAP.value, ANALYZER_VERSION, {
        "ok": features.get("ok"),
        "start_s": time_span.start_s,
        "end_s": time_span.end_s,
    })
    if not features.get("ok"):
        return build_observation(
            observation_id=key,
            analyzer_id=AnalyzerFamily.LOWEND_WRAP.value,
            subject=subject,
            time_span=time_span,
            values=[],
            quality=DspQuality.LIMITED,
            limitations=[
                DspLimitation("LOWEND_VIEWS_MISSING", str(features.get("missing"))),
            ],
            provider_id="lowend-obs-1",
            provider_version="lowend-obs-1",
            method="compute_lowend_features",
            params={"missing": features.get("missing")},
            source_artifact_hash=artifact,
            cache_key=key,
        )
    values = [
        MeasuredValue("lowend_analyzer_id", features.get("analyzer_id")),
        MeasuredValue("kick_event_source", features.get("kick_event_source")),
        MeasuredValue("attack_count", (features.get("attacks") or {}).get("count"), unit="count"),
        MeasuredValue("temporal_events_with_overlap", (features.get("temporal") or {}).get("events_with_overlap")),
        MeasuredValue("spectral_band_mean_joint", (features.get("spectral") or {}).get("band_mean_joint")),
        MeasuredValue("kick_f0", features.get("kick_f0")),
        MeasuredValue("bass_f0", features.get("bass_f0")),
        MeasuredValue("kick_rms", features.get("kick_rms"), unit="linear"),
        MeasuredValue("bass_rms", features.get("bass_rms"), unit="linear"),
        MeasuredValue("master_rms", features.get("master_rms"), unit="linear"),
    ]
    return build_observation(
        observation_id=key,
        analyzer_id=AnalyzerFamily.LOWEND_WRAP.value,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=DspQuality.OK,
        limitations=[
            DspLimitation("WRAPPED_LOWEND_OBS_1", "Canonical compute_lowend_features. Descriptive overlap only."),
        ],
        provider_id="lowend-obs-1",
        provider_version="lowend-obs-1",
        method="compute_lowend_features",
        params={"analyzer_id": features.get("analyzer_id")},
        source_artifact_hash=artifact,
        cache_key=key,
    )


def audio_asset_from_wav(path: Path | str, *, start: float = 0.0, end: float | None = None):
    """Build the frozen AudioAsset shape without talking to Live."""
    from copilot.audio.live_capture import AudioAsset
    from copilot.schemas.observation import CaptureView, SignalPoint

    wav = Path(path)
    data, sr = sf.read(str(wav), always_2d=True)
    duration = len(data) / float(sr) if sr else 0.0
    end_b = duration if end is None else float(end)
    return AudioAsset(
        capture_id=wav.stem,
        raw_file_path=str(wav),
        analysis_file_path=str(wav),
        file_path=str(wav),
        requested_start_beat=start,
        requested_end_beat=end_b,
        start_beat=start,
        end_beat=end_b,
        sample_rate=int(sr),
        channels=int(data.shape[1]),
        raw_duration=duration,
        analysis_duration=duration,
        duration=duration,
        session_revision=1,
        capture_view=CaptureView.TRACK_ISOLATED,
        signal_point=SignalPoint.TRACK_POST_MIXER,
        analysis_start_beat=start,
        analysis_end_beat=end_b,
    )
