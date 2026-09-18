"""Shared numpy/scipy measurements. Library objects stay inside this module."""

from __future__ import annotations

from typing import Any

import numpy as np

from copilot.audio.physical_dsp_v2.contract import (
    MIN_LUFS_S,
    ROLLOFF_PERCENT,
    SPECTRAL_BANDS_V1,
    STFT_NPERSEG,
    TRUE_PEAK_OVERSAMPLE,
)
from copilot.audio.physical_dsp_v2.providers import pyloudnorm_status, scipy_status
from copilot.schemas.dsp import DspLimitation, DspQuality


def stft_power(
    mono: np.ndarray,
    sample_rate: int,
    *,
    nperseg: int = STFT_NPERSEG,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[DspLimitation], DspQuality]:
    status = scipy_status()
    if not status.available:
        empty = np.zeros(0, dtype=np.float64)
        return empty, empty, np.zeros((0, 0)), [status.limitation], DspQuality.UNSUPPORTED
    from scipy.signal import stft

    nperseg = min(int(nperseg), max(256, len(mono)))
    noverlap = nperseg // 2
    freqs, times, zxx = stft(mono, fs=sample_rate, nperseg=nperseg, noverlap=noverlap)
    power = np.abs(zxx) ** 2
    return freqs.astype(np.float64), times.astype(np.float64), power.astype(np.float64), [], DspQuality.OK


def band_energies(
    freqs: np.ndarray,
    power: np.ndarray,
    bands: dict[str, tuple[float, float]] | None = None,
) -> dict[str, np.ndarray]:
    bands = bands or SPECTRAL_BANDS_V1
    out: dict[str, np.ndarray] = {}
    n = power.shape[1] if power.ndim == 2 else 0
    for name, (lo, hi) in bands.items():
        mask = (freqs >= lo) & (freqs < hi)
        if power.ndim != 2 or not np.any(mask):
            out[name] = np.zeros(n, dtype=np.float64)
        else:
            out[name] = np.sum(power[mask], axis=0).astype(np.float64)
    return out


def spectral_centroid(freqs: np.ndarray, power: np.ndarray) -> float | None:
    mag = np.sum(power, axis=1) if power.ndim == 2 else power
    denom = float(np.sum(mag))
    if denom <= 0:
        return None
    return float(np.sum(freqs * mag) / denom)


def spectral_rolloff(
    freqs: np.ndarray,
    power: np.ndarray,
    percent: float = ROLLOFF_PERCENT,
) -> float | None:
    mag = np.sum(power, axis=1) if power.ndim == 2 else np.asarray(power)
    total = float(np.sum(mag))
    if total <= 0:
        return None
    csum = np.cumsum(mag)
    idx = int(np.searchsorted(csum, percent * total))
    idx = min(idx, len(freqs) - 1)
    return float(freqs[idx])


def spectral_slope(freqs: np.ndarray, power: np.ndarray) -> float | None:
    mag = np.sum(power, axis=1) if power.ndim == 2 else np.asarray(power)
    mask = (freqs > 1.0) & (mag > 0)
    if int(np.count_nonzero(mask)) < 4:
        return None
    x = np.log10(freqs[mask])
    y = np.log10(mag[mask] + 1e-20)
    coef = np.polyfit(x, y, 1)
    return float(coef[0])


def spectral_flux(power: np.ndarray) -> np.ndarray:
    if power.ndim != 2 or power.shape[1] < 2:
        return np.zeros(0, dtype=np.float64)
    frames = power / (np.sum(power, axis=0, keepdims=True) + 1e-12)
    diff = np.diff(frames, axis=1)
    return np.sum(np.maximum(diff, 0.0), axis=0).astype(np.float64)


def spectral_flatness(power: np.ndarray) -> float | None:
    mag = np.sum(power, axis=1) if power.ndim == 2 else np.asarray(power)
    mag = mag[mag > 0]
    if len(mag) < 4:
        return None
    geo = float(np.exp(np.mean(np.log(mag + 1e-20))))
    arith = float(np.mean(mag))
    if arith <= 0:
        return None
    return geo / arith


def zero_crossing_rate(mono: np.ndarray) -> float:
    if len(mono) < 2:
        return 0.0
    signs = np.signbit(mono)
    return float(np.mean(signs[1:] != signs[:-1]))


def true_peak_4x(channel: np.ndarray, sample_rate: int) -> tuple[float | None, list[DspLimitation], DspQuality]:
    del sample_rate
    status = scipy_status()
    if not status.available:
        return None, [status.limitation], DspQuality.UNSUPPORTED
    if len(channel) < 8:
        return None, [DspLimitation("TRUE_PEAK_SIGNAL_TOO_SHORT", "")], DspQuality.LIMITED
    from scipy.signal import resample_poly

    up = resample_poly(np.asarray(channel, dtype=np.float64), TRUE_PEAK_OVERSAMPLE, 1)
    peak = float(np.max(np.abs(up))) if len(up) else 0.0
    return (
        peak,
        [
            DspLimitation(
                "TRUE_PEAK_4X_POLYPHASE_APPROXIMATION",
                "4x scipy.signal.resample_poly peak, not a certified ITU-R BS.1770 true-peak meter.",
            )
        ],
        DspQuality.LIMITED,
    )


def integrated_loudness(mono: np.ndarray, sample_rate: int) -> tuple[float | None, list[DspLimitation], DspQuality]:
    status = pyloudnorm_status()
    if not status.available:
        return None, [status.limitation], DspQuality.UNSUPPORTED
    duration = len(mono) / float(sample_rate) if sample_rate else 0.0
    if duration < MIN_LUFS_S:
        return None, [DspLimitation("LUFS_DURATION_BELOW_MIN", f"need>={MIN_LUFS_S}s")], DspQuality.LIMITED
    import pyloudnorm as pyln

    try:
        meter = pyln.Meter(sample_rate)
        value = float(meter.integrated_loudness(mono))
        if not np.isfinite(value):
            return None, [DspLimitation("LUFS_NON_FINITE", "pyloudnorm returned non-finite")], DspQuality.LIMITED
        return (
            value,
            [DspLimitation("LUFS_MONO_DOWNMIX", "Integrated loudness computed on mono downmix.")],
            DspQuality.OK,
        )
    except Exception as exc:  # noqa: BLE001
        return None, [DspLimitation("LUFS_MEASUREMENT_FAILED", str(exc))], DspQuality.LIMITED


def chroma_vector(freqs: np.ndarray, power: np.ndarray, sample_rate: int) -> np.ndarray:
    del sample_rate
    chroma = np.zeros(12, dtype=np.float64)
    mag = np.sum(power, axis=1) if power.ndim == 2 else np.asarray(power, dtype=np.float64)
    for freq, energy in zip(freqs, mag):
        if freq < 20.0 or energy <= 0:
            continue
        midi = 69.0 + 12.0 * np.log2(float(freq) / 440.0)
        pc = int(np.round(midi)) % 12
        chroma[pc] += float(energy)
    total = float(np.sum(chroma))
    if total > 0:
        chroma /= total
    return chroma


KRUMHANSL_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    dtype=np.float64,
)
KRUMHANSL_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    dtype=np.float64,
)
PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def key_candidates(chroma: np.ndarray, *, top_n: int = 5) -> list[dict[str, Any]]:
    if float(np.sum(chroma)) <= 0:
        return []
    rows: list[dict[str, Any]] = []
    for mode, profile in (("major", KRUMHANSL_MAJOR), ("minor", KRUMHANSL_MINOR)):
        prof = profile / (np.linalg.norm(profile) + 1e-12)
        for shift in range(12):
            rot = np.roll(chroma, -shift)
            rot = rot / (np.linalg.norm(rot) + 1e-12)
            score = float(np.dot(rot, prof))
            rows.append(
                {
                    "tonic": PITCH_CLASS_NAMES[shift],
                    "mode": mode,
                    "label": f"{PITCH_CLASS_NAMES[shift]} {mode}",
                    "score": score,
                }
            )
    rows.sort(key=lambda item: item["score"], reverse=True)
    best = rows[0]["score"] if rows else 1.0
    out = []
    for item in rows[:top_n]:
        confidence = float(item["score"] / best) if best > 0 else 0.0
        out.append({**item, "confidence": confidence})
    return out


def envelope_attack_decay(mono: np.ndarray, sample_rate: int) -> tuple[float | None, float | None]:
    if len(mono) < 8 or sample_rate <= 0:
        return None, None
    win = max(1, int(0.005 * sample_rate))
    env = np.convolve(np.abs(mono), np.ones(win) / win, mode="same")
    peak = float(np.max(env))
    if peak < 1e-8:
        return None, None
    hi = 0.9 * peak
    lo = 0.1 * peak
    above_lo = np.where(env >= lo)[0]
    above_hi = np.where(env >= hi)[0]
    if len(above_lo) == 0 or len(above_hi) == 0:
        return None, None
    attack = max(0.0, (int(above_hi[0]) - int(above_lo[0])) / float(sample_rate))
    decay_start = int(above_hi[-1])
    after = np.where(env[decay_start:] <= lo)[0]
    decay = None if len(after) == 0 else float(after[0]) / float(sample_rate)
    return float(attack), decay


def peak_pair_roughness(freqs: np.ndarray, mag: np.ndarray, *, max_peaks: int = 12) -> float | None:
    if len(freqs) < 4:
        return None
    order = np.argsort(mag)[::-1]
    peaks = []
    for idx in order:
        f = float(freqs[idx])
        a = float(mag[idx])
        if f < 20.0 or a <= 0:
            continue
        if all(abs(f - p[0]) > 30.0 for p in peaks):
            peaks.append((f, a))
        if len(peaks) >= max_peaks:
            break
    if len(peaks) < 2:
        return None
    score = 0.0
    weight = 0.0
    for i, (f1, a1) in enumerate(peaks):
        for f2, a2 in peaks[i + 1 :]:
            cbw = 0.24 * ((f1 + f2) / 2.0) + 1.0
            x = abs(f2 - f1) / max(cbw, 1e-6)
            s = np.exp(-3.5 * x) - np.exp(-5.75 * x)
            w = np.sqrt(a1 * a2)
            score += float(max(s, 0.0) * w)
            weight += float(w)
    if weight <= 0:
        return None
    return score / weight
