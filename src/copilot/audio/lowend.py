from __future__ import annotations

from typing import Any

import numpy as np
from scipy.signal import find_peaks, stft

LOWEND_BANDS_HZ = (
    (20, 40),
    (40, 60),
    (60, 80),
    (80, 120),
    (120, 200),
    (200, 300),
)

KICK_WINDOWS_S = (-0.100, -0.050, 0.0, 0.050, 0.100, 0.200)


def _mono(samples: np.ndarray) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim == 1:
        return data
    if data.shape[0] <= 8 and data.shape[0] < data.shape[1]:
        return np.mean(data, axis=0)
    return np.mean(data, axis=1)


def band_energy_over_time(
    samples: np.ndarray,
    sample_rate: int,
    *,
    nperseg: int = 2048,
) -> dict[str, Any]:
    mono = _mono(samples)
    noverlap = nperseg // 2
    freqs, times, zxx = stft(mono, fs=sample_rate, nperseg=nperseg, noverlap=noverlap)
    power = np.abs(zxx) ** 2
    bands: dict[str, list[float]] = {}
    for lo, hi in LOWEND_BANDS_HZ:
        mask = (freqs >= lo) & (freqs < hi)
        if not np.any(mask):
            bands[f"{lo}-{hi}"] = [0.0] * len(times)
            continue
        energy = np.sum(power[mask], axis=0)
        bands[f"{lo}-{hi}"] = [float(x) for x in energy]
    return {
        "times": [float(t) for t in times],
        "bands": bands,
        "nperseg": nperseg,
        "sample_rate": int(sample_rate),
    }


def detect_transients(
    samples: np.ndarray,
    sample_rate: int,
    *,
    min_distance_s: float = 0.35,
    role: str = "unknown",
) -> dict[str, Any]:
    mono = _mono(samples)
    hop = max(1, int(0.005 * sample_rate))
    win = max(hop * 2, int(0.02 * sample_rate))
    if len(mono) < win * 2:
        return {"attacks": [], "role": role, "method": "rms_flux"}
    n = 1 + (len(mono) - win) // hop
    env = np.empty(n, dtype=np.float64)
    for i in range(n):
        sl = mono[i * hop : i * hop + win]
        env[i] = float(np.sqrt(np.mean(sl**2)))
    flux = np.maximum(np.diff(env, prepend=env[0]), 0.0)
    height = max(float(np.percentile(flux, 85)) * 0.6, 1e-5)
    distance = max(1, int(min_distance_s * sample_rate / hop))
    peaks, props = find_peaks(flux, height=height, distance=distance)
    attacks: list[dict[str, float | int]] = []
    for idx in peaks:
        start = max(0, idx - 2)
        end = min(len(env), idx + 8)
        duration = (end - start) * hop / sample_rate
        attacks.append(
            {
                "time_s": float(idx * hop / sample_rate),
                "strength": float(flux[idx]),
                "approx_duration_s": float(duration),
            }
        )
    return {
        "attacks": attacks,
        "role": role,
        "method": "rms_flux",
        "count": len(attacks),
        "note": (
            "Attacks are energy onsets, not identified kicks "
            if role != "kick"
            else "Attacks taken from the kick-labeled isolated view"
        ),
    }


def prune_kick_attacks(
    attacks: list[dict[str, float | int]],
    *,
    duration_s: float,
    min_dist_s: float = 0.40,
) -> list[dict[str, float | int]]:
    ordered = sorted(attacks, key=lambda item: float(item.get("strength") or 0.0), reverse=True)
    kept: list[dict[str, float | int]] = []
    for attack in ordered:
        t0 = float(attack["time_s"])
        if t0 < 0 or t0 > duration_s:
            continue
        if all(abs(t0 - float(prev["time_s"])) >= min_dist_s for prev in kept):
            kept.append(attack)
    kept.sort(key=lambda item: float(item["time_s"]))
    return kept


def low_frequency_envelope(
    samples: np.ndarray,
    sample_rate: int,
    *,
    lo_hz: float = 20.0,
    hi_hz: float = 120.0,
    hop_s: float = 0.01,
) -> dict[str, Any]:
    spec = band_energy_over_time(samples, sample_rate)
    times = spec["times"]
    energy = np.zeros(len(times), dtype=np.float64)
    for name, series in spec["bands"].items():
        lo, hi = (int(p) for p in name.split("-"))
        if hi <= lo_hz or lo >= hi_hz:
            continue
        energy += np.asarray(series, dtype=np.float64)
    peak = float(np.max(energy)) if len(energy) else 0.0
    norm = energy / (peak + 1e-12)
    return {
        "times": times,
        "energy": [float(x) for x in energy],
        "normalized": [float(x) for x in norm],
        "lo_hz": lo_hz,
        "hi_hz": hi_hz,
    }


def _window_mean(times: list[float], values: list[float], start: float, end: float) -> float:
    hits = [v for t, v in zip(times, values) if start <= t < end]
    if not hits:
        return 0.0
    return float(np.mean(hits))


def _off_kick_mean(
    times: list[float],
    values: list[float],
    kick_times: list[float],
    *,
    min_dist_s: float = 0.25,
) -> float:
    off = [
        v
        for t, v in zip(times, values)
        if all(abs(t - kick) >= min_dist_s for kick in kick_times)
    ]
    if not off:
        return 0.0
    return float(np.mean(off))


def overlap_at_attacks(
    kick_attacks: list[dict[str, float | int]],
    bass_envelope: dict[str, Any],
    *,
    band_lo: int = 40,
    band_hi: int = 120,
) -> dict[str, Any]:
    times = bass_envelope["times"]
    values = bass_envelope["normalized"]
    raw_energy = bass_envelope.get("energy") or values
    kick_times = [float(attack["time_s"]) for attack in kick_attacks]
    off_kick = _off_kick_mean(times, values, kick_times)
    off_raw = _off_kick_mean(times, raw_energy, kick_times)
    peak_raw = float(max(raw_energy) if raw_energy else 0.0)
    per_attack: list[dict[str, Any]] = []
    edges = list(KICK_WINDOWS_S)
    for attack in kick_attacks:
        t0 = float(attack["time_s"])
        windows = {}
        for a, b in zip(edges, edges[1:]):
            windows[f"{int(a * 1000)}:{int(b * 1000)}"] = _window_mean(
                times, values, t0 + a, t0 + b
            )
        on_attack = _window_mean(times, values, t0, t0 + 0.150)
        on_raw = _window_mean(times, raw_energy, t0, t0 + 0.150)
        persist = _window_mean(times, values, t0 + 0.050, t0 + 0.200)
        pre = _window_mean(times, values, t0 - 0.100, t0)
        # Sustain that does not yield at kicks, relative to this region.
        # Attack-aligned short notes (on >> off) are not decay collisions.
        sustain_across = (
            off_raw > peak_raw * 0.20
            and on_raw >= 0.70 * off_raw
            and on_raw <= 1.45 * off_raw
        )
        per_attack.append(
            {
                "kick_time_s": t0,
                "windows": windows,
                "pre_attack": pre,
                "on_attack_0_150ms": on_attack,
                "on_attack_raw": on_raw,
                "persist_50_200ms": persist,
                "off_kick_mean": off_kick,
                "off_kick_raw": off_raw,
                "off_kick_raw": off_raw,
                "persists_across_attack": sustain_across,
            }
        )
    collisions = sum(1 for item in per_attack if item["persists_across_attack"])
    persist_vals = [item["persist_50_200ms"] for item in per_attack]
    on_vals = [item["on_attack_0_150ms"] for item in per_attack]
    return {
        "kick_events": len(per_attack),
        "collisions": collisions,
        "events_with_overlap": collisions,
        "band": [band_lo, band_hi],
        "median_persist": float(np.median(persist_vals)) if persist_vals else 0.0,
        "median_on_attack": float(np.median(on_vals)) if on_vals else 0.0,
        "off_kick_mean": off_kick,
        "per_attack": per_attack,
        "note": (
            "Overlap is descriptive. persists_across_attack means bass energy "
            "at the kick is similar to bass energy away from kicks in this region."
        ),
    }


def spectral_overlap_over_time(
    kick_bands: dict[str, Any],
    bass_bands: dict[str, Any],
    *,
    kick_attacks: list[dict[str, float | int]] | None = None,
) -> dict[str, Any]:
    times = kick_bands["times"]
    names = list(kick_bands["bands"])
    kick_mat = np.vstack(
        [np.asarray(kick_bands["bands"][name], dtype=np.float64) for name in names]
    )
    bass_mat = np.vstack(
        [
            np.asarray(bass_bands["bands"].get(name, [0.0] * kick_mat.shape[1]), dtype=np.float64)
            for name in names
        ]
    )
    n = min(kick_mat.shape[1], bass_mat.shape[1], len(times))
    kick_mat = kick_mat[:, :n]
    bass_mat = bass_mat[:, :n]
    times = times[:n]
    kick_share = kick_mat / (np.sum(kick_mat, axis=0, keepdims=True) + 1e-12)
    bass_share = bass_mat / (np.sum(bass_mat, axis=0, keepdims=True) + 1e-12)
    simultaneous: list[dict[str, Any]] = []
    band_scores: dict[str, float] = {}
    for i, name in enumerate(names):
        both_share = np.minimum(kick_share[i], bass_share[i])
        band_scores[name] = float(np.mean(both_share))
        strong = np.where((kick_share[i] > 0.22) & (bass_share[i] > 0.22))[0]
        occupancy = float(len(strong) / n) if n else 0.0
        if len(strong) >= 8 and occupancy >= 0.08:
            simultaneous.append(
                {
                    "band": name,
                    "frames": int(len(strong)),
                    "occupancy": occupancy,
                    "duration_s": float(
                        times[min(n - 1, int(strong[-1]))] - times[int(strong[0])]
                    ),
                    "mean_joint": float(np.mean(both_share[strong])),
                }
            )
    events: list[dict[str, Any]] = []
    if kick_attacks:
        t_arr = np.asarray(times, dtype=np.float64)
        for attack in kick_attacks:
            t0 = float(attack["time_s"])
            mask = (t_arr >= t0) & (t_arr < t0 + 0.180)
            if not np.any(mask):
                continue
            kick_win = np.mean(kick_share[:, mask], axis=1)
            bass_win = np.mean(bass_share[:, mask], axis=1)
            joint = np.minimum(kick_win, bass_win)
            band_i = int(np.argmax(joint))
            kick_top = set(np.argsort(kick_win)[-2:].tolist())
            bass_top = set(np.argsort(bass_win)[-2:].tolist())
            events.append(
                {
                    "kick_time_s": t0,
                    "band": names[band_i],
                    "kick_share": float(kick_win[band_i]),
                    "bass_share": float(bass_win[band_i]),
                    "joint_share": float(joint[band_i]),
                    "co_concentrated": bool(
                        band_i in kick_top
                        and band_i in bass_top
                        and kick_win[band_i] >= 0.22
                        and bass_win[band_i] >= 0.22
                    ),
                }
            )
    co = [item for item in events if item["co_concentrated"]]
    dominant = None
    if band_scores:
        dominant = max(band_scores, key=band_scores.get)
    top_event_band = None
    if co:
        bands = [item["band"] for item in co]
        top_event_band = max(set(bands), key=bands.count)
    return {
        "band_mean_joint": band_scores,
        "simultaneous": simultaneous,
        "dominant_shared_band": dominant,
        "attack_events": events,
        "events_with_co_concentration": len(co),
        "kick_events": len(events),
        "dominant_attack_band": top_event_band,
    }


def estimate_fundamental(
    samples: np.ndarray,
    sample_rate: int,
    *,
    lo_hz: float = 28.0,
    hi_hz: float = 160.0,
) -> dict[str, Any]:
    mono = _mono(samples)
    if len(mono) < sample_rate // 10:
        return {"status": "UNRESOLVED", "hz": None, "reason": "too_short"}
    window = mono * np.hanning(len(mono))
    spec = np.abs(np.fft.rfft(window)) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)
    mask = (freqs >= lo_hz) & (freqs <= hi_hz)
    if not np.any(mask):
        return {"status": "UNRESOLVED", "hz": None, "reason": "no_bins"}
    band = spec[mask]
    band_f = freqs[mask]
    peak_i = int(np.argmax(band))
    peak = float(band[peak_i])
    median = float(np.median(band) + 1e-12)
    if peak < median * 8:
        return {
            "status": "UNRESOLVED",
            "hz": float(band_f[peak_i]),
            "reason": "no_stable_peak",
            "peak_to_median": peak / median,
        }
    return {
        "status": "MEASURED",
        "hz": float(band_f[peak_i]),
        "peak_to_median": peak / median,
    }


def context_change(
    full: np.ndarray,
    without: np.ndarray,
    sample_rate: int,
) -> dict[str, Any]:
    full_b = band_energy_over_time(full, sample_rate)
    mute_b = band_energy_over_time(without, sample_rate)
    changes: dict[str, float] = {}
    for name, series in full_b["bands"].items():
        a = float(np.mean(series)) if series else 0.0
        b = float(np.mean(mute_b["bands"].get(name, [0.0])))
        changes[name] = (a - b) / (a + 1e-12)
    full_rms = float(np.sqrt(np.mean(_mono(full) ** 2)))
    mute_rms = float(np.sqrt(np.mean(_mono(without) ** 2)))
    return {
        "band_energy_drop_ratio": changes,
        "rms_full": full_rms,
        "rms_without": mute_rms,
        "rms_drop_ratio": (full_rms - mute_rms) / (full_rms + 1e-12),
        "note": "Contextual change only. Not a stem. Nonlinear master processing may move.",
    }


def context_around_kicks(
    master: np.ndarray,
    without_bass: np.ndarray,
    sample_rate: int,
    kick_times: list[float],
) -> dict[str, Any]:
    full = low_frequency_envelope(master, sample_rate)
    muted = low_frequency_envelope(without_bass, sample_rate)
    at_full = [_window_mean(full["times"], full["energy"], t, t + 0.150) for t in kick_times]
    at_mute = [_window_mean(muted["times"], muted["energy"], t, t + 0.150) for t in kick_times]
    off_full = _off_kick_mean(full["times"], full["energy"], kick_times)
    off_mute = _off_kick_mean(muted["times"], muted["energy"], kick_times)
    at_drop = []
    for a, b in zip(at_full, at_mute):
        at_drop.append((a - b) / (a + 1e-12))
    return {
        "median_drop_at_kicks": float(np.median(at_drop)) if at_drop else 0.0,
        "drop_off_kick": (off_full - off_mute) / (off_full + 1e-12),
        "note": "Main low-end change when bass is muted. Not a stem.",
    }
