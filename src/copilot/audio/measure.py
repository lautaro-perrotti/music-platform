from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pyloudnorm as pyln

from copilot.schemas.observation import (
    CaptureView,
    Claim,
    ClaimKind,
    MusicObservation,
    ObservationSource,
    SignalFeatures,
    SignalPoint,
    TailPolicy,
)

MIN_LUFS_SECONDS = 0.4
BPM_CONFIDENCE_FLOOR = 0.65
KEY_CONFIDENCE_FLOOR = 0.7


def measure_audio(
    samples: np.ndarray,
    sample_rate: int,
    source: str,
    region: str,
    tempo_hint_bpm: float | None = None,
    *,
    capture_view: CaptureView | None = None,
    observation_source: ObservationSource | None = None,
    signal_point: SignalPoint | None = None,
    signal_point_label: str | None = None,
    tail_policy: TailPolicy = TailPolicy.STRICT_REGION,
) -> MusicObservation:
    if samples.ndim == 1:
        mono = samples.astype(np.float64)
        channels = 1
        stereo_width = None
    else:
        channels = samples.shape[0] if samples.shape[0] <= 8 else samples.shape[1]
        if samples.shape[0] <= 8:
            audio = samples.astype(np.float64)
        else:
            audio = samples.T.astype(np.float64)
        mono = np.mean(audio, axis=0)
        if audio.shape[0] >= 2:
            mid = (audio[0] + audio[1]) / 2.0
            side = (audio[0] - audio[1]) / 2.0
            stereo_width = float(np.sqrt(np.mean(side**2)) / (np.sqrt(np.mean(mid**2)) + 1e-12))
        else:
            stereo_width = None

    duration = float(len(mono) / sample_rate)
    rms = float(np.sqrt(np.mean(mono**2)))
    peak = float(np.max(np.abs(mono)))
    crest = float(peak / (rms + 1e-12))
    lufs = _safe_lufs(mono, sample_rate)
    centroid = _spectral_centroid(mono, sample_rate)
    bass_ratio = _bass_energy_ratio(mono, sample_rate)

    claims = [
        Claim(kind=ClaimKind.MEASURED, name="duration_seconds", value=duration, unit="s"),
        Claim(kind=ClaimKind.MEASURED, name="rms", value=rms),
        Claim(kind=ClaimKind.MEASURED, name="peak", value=peak),
        Claim(kind=ClaimKind.MEASURED, name="crest_factor", value=crest),
    ]
    if lufs is not None:
        claims.append(Claim(kind=ClaimKind.MEASURED, name="lufs", value=lufs, unit="LUFS"))
    if centroid is not None:
        claims.append(
            Claim(
                kind=ClaimKind.MEASURED,
                name="spectral_centroid_hz",
                value=centroid,
                unit="Hz",
            )
        )
    if bass_ratio is not None:
        claims.append(
            Claim(
                kind=ClaimKind.MEASURED,
                name="bass_energy_ratio",
                value=bass_ratio,
            )
        )

    tempo = None
    tempo_confidence = 0.0
    if tempo_hint_bpm is not None:
        tempo = tempo_hint_bpm
        tempo_confidence = 0.4
        claims.append(
            Claim(
                kind=ClaimKind.INFERRED,
                name="tempo_bpm",
                value=tempo,
                confidence=tempo_confidence,
                notes="Session tempo hint only; not beat-tracked from audio.",
            )
        )

    return MusicObservation(
        source=source,
        region=region,
        timestamp=datetime.now(timezone.utc).isoformat(),
        signal=SignalFeatures(
            duration_seconds=duration,
            sample_rate=sample_rate,
            channels=channels,
            rms=rms,
            peak=peak,
            true_peak=peak,
            lufs=lufs,
            crest_factor=crest,
            spectral_centroid_hz=centroid,
            bass_energy_ratio=bass_ratio,
            stereo_width=stereo_width,
        ),
        claims=claims,
        tempo_bpm=tempo if tempo_confidence >= BPM_CONFIDENCE_FLOOR else None,
        key=None,
        confidence={"tempo": tempo_confidence, "key": 0.0},
        capture_view=capture_view,
        observation_source=observation_source,
        signal_point=signal_point,
        signal_point_label=signal_point_label,
        tail_policy=tail_policy,
    )


def _safe_lufs(mono: np.ndarray, sample_rate: int) -> float | None:
    duration = len(mono) / sample_rate
    if duration < MIN_LUFS_SECONDS:
        return None
    meter = pyln.Meter(sample_rate)
    try:
        return float(meter.integrated_loudness(mono))
    except Exception:  # pyloudnorm can fail on silence
        return None


def _spectral_centroid(mono: np.ndarray, sample_rate: int) -> float | None:
    if len(mono) < 16:
        return None
    windowed = mono * np.hanning(len(mono))
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)
    power = spectrum**2
    denom = float(np.sum(power))
    if denom <= 0:
        return None
    return float(np.sum(freqs * power) / denom)


def _bass_energy_ratio(mono: np.ndarray, sample_rate: int, cutoff_hz: float = 120.0) -> float | None:
    if len(mono) < 16:
        return None
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)
    total = float(np.sum(spectrum))
    if total <= 0:
        return None
    bass = float(np.sum(spectrum[freqs <= cutoff_hz]))
    return bass / total
