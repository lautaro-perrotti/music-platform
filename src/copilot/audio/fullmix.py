from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import math
import re

import numpy as np
import soundfile as sf
from scipy.signal import find_peaks, stft

from copilot.audio.file_hash import sha256_file
from copilot.schemas.fullmix import (
    DynamicObservation,
    EnergyEvent,
    EnergyEventKind,
    EnergyFrame,
    FullMixBand,
    FullMixObservation,
    SpectralBandPoint,
    SpectralEvent,
    StereoObservation,
    TransientObservation,
)

ANALYZER_ID = "fullmix-obs-1"
ANALYZER_FILES = (
    "src/copilot/audio/fullmix.py",
    "src/copilot/schemas/fullmix.py",
    "src/copilot/reasoning/from_fullmix.py",
)

# Versioned deterministic configuration. Changing these bumps configuration_hash.
# FREEZE: fullmix-obs-1 V1 is frozen after RUN 1 calibration. Do not retune
# thresholds using REGION_A/B/C. Next song/regions are holdout.
FROZEN = True
FROZEN_LABEL = "fullmix-obs-1-v1"
WINDOW_S = 0.05
HOP_S = 0.025
ROLLING_MEDIAN_S = 2.0
REFERENCE_PERCENTILE = 75.0
SILENCE_RMS = 1.0e-4
NEAR_SILENCE_RMS = 1.0e-3
STRONG_DIP_DB = -12.0
MIN_EVENT_S = 0.05
SPECTRAL_CHANGE_DB = 6.0
REPEAT_TOLERANCE_S = 0.15
REPEAT_PERIOD_MIN_S = 0.2
REPEAT_PERIOD_MAX_S = 4.0
STFT_NPERSEG = 2048
TRANSIENT_MIN_DISTANCE_S = 0.08
DYNAMICS_WINDOW_S = 0.5

BANDS_HZ: dict[str, tuple[float, float]] = {
    FullMixBand.SUB.value: (20.0, 60.0),
    FullMixBand.LOW.value: (60.0, 200.0),
    FullMixBand.LOW_MID.value: (200.0, 800.0),
    FullMixBand.MID.value: (800.0, 3000.0),
    FullMixBand.HIGH_MID.value: (3000.0, 8000.0),
    FullMixBand.HIGH.value: (8000.0, 16000.0),
}

FORBIDDEN_DIAGNOSIS_TERMS = (
    "bad",
    "good",
    "groove problem",
    "dropout problem",
    "needs fixing",
    "masking",
    "boring",
    "weak drop",
)

FORBIDDEN_DIAGNOSIS_RE = re.compile(
    r"\b(bad|good|groove problem|dropout problem|needs fixing|masking|boring|weak drop)\b",
    re.IGNORECASE,
)

CACHE_DIR = Path("logs") / "fullmix_cache"


def analyzer_fingerprint(root: Path | None = None) -> str:
    base = root or Path(__file__).resolve().parents[3]
    digest = hashlib.sha256()
    digest.update(ANALYZER_ID.encode("utf-8"))
    for rel in ANALYZER_FILES:
        path = base / rel
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(rel.encode("utf-8"))
    return digest.hexdigest()


def configuration_hash() -> str:
    payload = {
        "window_s": WINDOW_S,
        "hop_s": HOP_S,
        "rolling_median_s": ROLLING_MEDIAN_S,
        "reference_percentile": REFERENCE_PERCENTILE,
        "silence_rms": SILENCE_RMS,
        "near_silence_rms": NEAR_SILENCE_RMS,
        "strong_dip_db": STRONG_DIP_DB,
        "min_event_s": MIN_EVENT_S,
        "spectral_change_db": SPECTRAL_CHANGE_DB,
        "bands_hz": BANDS_HZ,
        "stft_nperseg": STFT_NPERSEG,
        "transient_min_distance_s": TRANSIENT_MIN_DISTANCE_S,
        "dynamics_window_s": DYNAMICS_WINDOW_S,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def thresholds_public() -> dict[str, Any]:
    return {
        "window_s": WINDOW_S,
        "hop_s": HOP_S,
        "rolling_median_s": ROLLING_MEDIAN_S,
        "reference_percentile": REFERENCE_PERCENTILE,
        "silence_rms": SILENCE_RMS,
        "near_silence_rms": NEAR_SILENCE_RMS,
        "strong_dip_db": STRONG_DIP_DB,
        "min_event_s": MIN_EVENT_S,
        "spectral_change_db": SPECTRAL_CHANGE_DB,
        "bands_hz": BANDS_HZ,
        "stft_nperseg": STFT_NPERSEG,
        "transient_min_distance_s": TRANSIENT_MIN_DISTANCE_S,
        "dynamics_window_s": DYNAMICS_WINDOW_S,
    }


def freeze_fullmix_v1(
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
) -> dict[str, Any]:
    """Record frozen fullmix-obs-1 configuration. No threshold retune from A/B/C."""
    evidence = Path(evidence or "logs")
    evidence.mkdir(parents=True, exist_ok=True)
    config_sha = configuration_hash()
    analyzer_sha = analyzer_fingerprint()
    payload = {
        "status": "FULLMIX V1 FROZEN",
        "analyzer_id": ANALYZER_ID,
        "frozen_label": FROZEN_LABEL,
        "frozen": FROZEN,
        "energy_reference_method": "percentile_75",
        "reference_percentile": REFERENCE_PERCENTILE,
        "configuration": thresholds_public(),
        "configuration_hash": config_sha,
        "analyzer_hash": analyzer_sha,
        "analyzer_sha256": analyzer_sha,
        "source_run": source_run,
        "run_classification": "DEVELOPMENT_CALIBRATION_SET",
        "independent_validation": False,
        "threshold_tuning_from_regions": [],
        "note": (
            "RUN 1 REGION_A/B/C calibrated reference_percentile=75 and MIN_EVENT_S. "
            "Configuration is now frozen. Next song/regions are holdout."
        ),
        "MUSICAL WRITES": 0,
    }
    out = evidence / "fullmix_v1_freeze.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    # Mark the session report without mutating musical state or DSP packs.
    report_path = evidence / f"{source_run}.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["run_classification"] = "DEVELOPMENT_CALIBRATION_SET"
        report["independent_validation"] = False
        report["fullmix_freeze"] = {
            "analyzer_id": ANALYZER_ID,
            "frozen_label": FROZEN_LABEL,
            "configuration_hash": config_sha,
            "analyzer_hash": analyzer_sha,
            "energy_reference_method": "percentile_75",
            "artifact": str(out),
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload



def _db(num: float, den: float) -> float:
    n = max(float(num), 1e-12)
    d = max(float(den), 1e-12)
    return 20.0 * math.log10(n / d)


def _mono(samples: np.ndarray) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim == 1:
        return data
    if data.shape[0] <= 8 and data.shape[0] < data.shape[1]:
        return np.mean(data, axis=0)
    return np.mean(data, axis=1)


def _frame_stats(mono: np.ndarray, sample_rate: int, window_s: float, hop_s: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    win = max(1, int(round(window_s * sample_rate)))
    hop = max(1, int(round(hop_s * sample_rate)))
    if len(mono) < win:
        rms = np.array([float(np.sqrt(np.mean(mono**2))) if len(mono) else 0.0], dtype=np.float64)
        peak = np.array([float(np.max(np.abs(mono))) if len(mono) else 0.0], dtype=np.float64)
        times = np.array([0.0], dtype=np.float64)
        return times, rms, peak
    n = 1 + (len(mono) - win) // hop
    shape = (n, win)
    strides = (mono.strides[0] * hop, mono.strides[0])
    frames = np.lib.stride_tricks.as_strided(mono, shape=shape, strides=strides)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    peak = np.max(np.abs(frames), axis=1)
    times = (np.arange(n, dtype=np.float64) * hop) / float(sample_rate)
    return times, rms.astype(np.float64), peak.astype(np.float64)


def _rolling_stat(values: np.ndarray, window_frames: int, q: float = 50.0) -> np.ndarray:
    if len(values) == 0:
        return values.copy()
    w = max(1, int(window_frames))
    if w == 1 or len(values) == 1:
        return values.copy()
    pad = w // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    shape = (len(values), w)
    strides = (padded.strides[0], padded.strides[0])
    windows = np.lib.stride_tricks.as_strided(padded, shape=shape, strides=strides)
    return np.percentile(windows, q, axis=1)


def _rolling_median(values: np.ndarray, window_frames: int) -> np.ndarray:
    return _rolling_stat(values, window_frames, q=50.0)


def _merge_runs(mask: np.ndarray, times: np.ndarray, hop_s: float) -> list[tuple[float, float, slice]]:
    events: list[tuple[float, float, slice]] = []
    if len(mask) == 0:
        return events
    i = 0
    n = len(mask)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i + 1
        while j < n and mask[j]:
            j += 1
        start = float(times[i])
        end = float(times[j - 1] + hop_s)
        if end - start >= MIN_EVENT_S - 1e-9:
            events.append((start, end, slice(i, j)))
        i = j
    return events


def _classify_energy_events(
    times: np.ndarray,
    rms: np.ndarray,
    reference: np.ndarray,
    hop_s: float,
) -> list[EnergyEvent]:
    relative_db = np.array([_db(r, ref) for r, ref in zip(rms, reference)], dtype=np.float64)
    silence_mask = rms <= SILENCE_RMS
    near_mask = (rms > SILENCE_RMS) & (rms <= NEAR_SILENCE_RMS)
    dip_mask = (~silence_mask) & (~near_mask) & (relative_db <= STRONG_DIP_DB)

    events: list[EnergyEvent] = []
    for kind, mask in (
        (EnergyEventKind.SILENCE, silence_mask),
        (EnergyEventKind.NEAR_SILENCE, near_mask),
        (EnergyEventKind.STRONG_ENERGY_DIP, dip_mask),
    ):
        for start, end, sl in _merge_runs(mask, times, hop_s):
            seg_rms = rms[sl]
            seg_ref = reference[sl]
            min_rms = float(np.min(seg_rms)) if len(seg_rms) else 0.0
            ref = float(np.median(seg_ref)) if len(seg_ref) else 0.0
            events.append(
                EnergyEvent(
                    kind=kind,
                    start_s=start,
                    end_s=end,
                    duration_s=end - start,
                    minimum_rms=min_rms,
                    reference_rms=ref,
                    relative_drop_db=_db(min_rms, ref),
                    quality="OK",
                    limitations=[],
                )
            )
    return _annotate_repetition(events)


def _annotate_repetition(events: list[EnergyEvent]) -> list[EnergyEvent]:
    if not events:
        return events
    by_kind: dict[EnergyEventKind, list[EnergyEvent]] = {}
    for event in events:
        by_kind.setdefault(event.kind, []).append(event)
    out: list[EnergyEvent] = []
    for kind, group in by_kind.items():
        starts = np.array([item.start_s for item in group], dtype=np.float64)
        for idx, event in enumerate(group):
            similar = 1
            deltas: list[float] = []
            for jdx, other in enumerate(group):
                if idx == jdx:
                    continue
                # similar duration and depth
                dur_ok = abs(other.duration_s - event.duration_s) <= max(0.05, 0.35 * event.duration_s)
                drop_ok = abs(other.relative_drop_db - event.relative_drop_db) <= 4.0
                if dur_ok and drop_ok:
                    similar += 1
                    deltas.append(abs(other.start_s - event.start_s))
            period = None
            strength = 0.0
            if deltas:
                candidates = [d for d in deltas if REPEAT_PERIOD_MIN_S <= d <= REPEAT_PERIOD_MAX_S]
                if candidates:
                    period = float(np.median(candidates))
                    # how many other starts fall near k*period
                    hits = 0
                    for other_start in starts:
                        if abs(other_start - event.start_s) < 1e-9:
                            continue
                        if period <= 1e-9:
                            continue
                        k = round(abs(other_start - event.start_s) / period)
                        if k >= 1 and abs(abs(other_start - event.start_s) - k * period) <= REPEAT_TOLERANCE_S:
                            hits += 1
                    strength = float(hits) / float(max(1, len(group) - 1))
            out.append(
                event.model_copy(
                    update={
                        "event_similarity_count": similar,
                        "approx_period_s": period,
                        "repetition_strength": strength if similar > 1 else 0.0,
                    }
                )
            )
    return sorted(out, key=lambda item: (item.start_s, item.kind.value))


def _spectral_trajectory(mono: np.ndarray, sample_rate: int) -> tuple[list[SpectralBandPoint], list[SpectralEvent]]:
    nperseg = min(STFT_NPERSEG, max(256, len(mono)))
    noverlap = nperseg // 2
    freqs, times, zxx = stft(mono, fs=sample_rate, nperseg=nperseg, noverlap=noverlap)
    power = np.abs(zxx) ** 2
    band_series: dict[str, np.ndarray] = {}
    for name, (lo, hi) in BANDS_HZ.items():
        mask = (freqs >= lo) & (freqs < hi)
        if not np.any(mask):
            band_series[name] = np.zeros(len(times), dtype=np.float64)
        else:
            band_series[name] = np.sum(power[mask], axis=0).astype(np.float64)
    points: list[SpectralBandPoint] = []
    for i, t in enumerate(times):
        points.append(
            SpectralBandPoint(
                t_s=float(t),
                bands={name: float(series[i]) for name, series in band_series.items()},
            )
        )
    events: list[SpectralEvent] = []
    if len(times) == 0:
        return points, events
    hop = float(times[1] - times[0]) if len(times) > 1 else float(nperseg / sample_rate)
    for name, series in band_series.items():
        ref = _rolling_stat(series, max(1, int(round(ROLLING_MEDIAN_S / max(hop, 1e-6)))), q=REFERENCE_PERCENTILE)
        rel = np.array([_db(v, r) for v, r in zip(series, ref)], dtype=np.float64)
        low_mask = rel <= -SPECTRAL_CHANGE_DB
        high_mask = rel >= SPECTRAL_CHANGE_DB
        for sign, mask in ((-1.0, low_mask), (1.0, high_mask)):
            for start, end, sl in _merge_runs(mask, times.astype(np.float64), hop):
                seg = series[sl]
                seg_ref = ref[sl]
                measured = float(np.median(seg))
                reference = float(np.median(seg_ref))
                events.append(
                    SpectralEvent(
                        start_s=start,
                        end_s=end,
                        band=name,
                        relative_change_db=_db(measured, reference) if sign < 0 else _db(measured, reference),
                        reference_energy=reference,
                        measured_energy=measured,
                        quality="OK",
                    )
                )
    return points, sorted(events, key=lambda item: (item.start_s, item.band))


def _transient_obs(mono: np.ndarray, sample_rate: int, duration_s: float) -> TransientObservation:
    hop = max(1, int(0.005 * sample_rate))
    win = max(hop * 2, int(0.02 * sample_rate))
    precision_ms = 1000.0 * hop / float(sample_rate)
    limitations = [
        f"MAIN_INTERNAL_ONSET_PRECISION_MS={precision_ms:.3f}",
        "Main-only onset times are internal to this asset; cross-source alignment still LIMITED ±52 ms.",
    ]
    if len(mono) < win * 2:
        return TransientObservation(
            window_start_s=0.0,
            window_end_s=duration_s,
            transient_count=0,
            transient_density_per_s=0.0,
            precision_ms=precision_ms,
            quality="LIMITED",
            limitations=limitations + ["SIGNAL_TOO_SHORT_FOR_TRANSIENT_GRID"],
        )
    n = 1 + (len(mono) - win) // hop
    # vectorized framing
    shape = (n, win)
    strides = (mono.strides[0] * hop, mono.strides[0])
    frames = np.lib.stride_tricks.as_strided(mono, shape=shape, strides=strides)
    env = np.sqrt(np.mean(frames * frames, axis=1))
    flux = np.maximum(np.diff(env, prepend=env[0]), 0.0)
    height = max(float(np.percentile(flux, 85)) * 0.6, 1e-5)
    distance = max(1, int(TRANSIENT_MIN_DISTANCE_S * sample_rate / hop))
    peaks, _ = find_peaks(flux, height=height, distance=distance)
    times = peaks.astype(np.float64) * hop / float(sample_rate)
    ioi = np.diff(times) if len(times) > 1 else np.array([], dtype=np.float64)
    dens = float(len(times) / duration_s) if duration_s > 0 else 0.0
    return TransientObservation(
        window_start_s=0.0,
        window_end_s=duration_s,
        transient_count=int(len(times)),
        transient_density_per_s=dens,
        mean_ioi_s=float(np.mean(ioi)) if len(ioi) else None,
        median_ioi_s=float(np.median(ioi)) if len(ioi) else None,
        precision_ms=precision_ms,
        quality="OK",
        limitations=limitations,
    )


def _stereo_frames(data: np.ndarray, sample_rate: int, times: np.ndarray) -> list[StereoObservation]:
    if data.ndim == 1 or data.shape[1] < 2:
        return [
            StereoObservation(
                t_s=float(t),
                left_rms=0.0,
                right_rms=0.0,
                quality="LIMITED",
                limitations=["MONO_OR_SINGLE_CHANNEL"],
            )
            for t in times[:1]
        ]
    left = data[:, 0].astype(np.float64)
    right = data[:, 1].astype(np.float64)
    win = max(1, int(round(WINDOW_S * sample_rate)))
    hop = max(1, int(round(HOP_S * sample_rate)))
    n = min(len(times), 1 + max(0, (len(left) - win) // hop))
    out: list[StereoObservation] = []
    for i in range(n):
        a = i * hop
        b = a + win
        l = left[a:b]
        r = right[a:b]
        l_rms = float(np.sqrt(np.mean(l * l))) if len(l) else 0.0
        r_rms = float(np.sqrt(np.mean(r * r))) if len(r) else 0.0
        if len(l) and len(r) and (np.std(l) > 1e-12 and np.std(r) > 1e-12):
            corr = float(np.corrcoef(l, r)[0, 1])
        else:
            corr = None
        mid = 0.5 * (l + r)
        side = 0.5 * (l - r)
        mid_e = float(np.mean(mid * mid)) if len(mid) else 0.0
        side_e = float(np.mean(side * side)) if len(side) else 0.0
        ratio = (side_e / mid_e) if mid_e > 1e-18 else None
        out.append(
            StereoObservation(
                t_s=float(times[i]),
                left_rms=l_rms,
                right_rms=r_rms,
                correlation=corr,
                mid_energy=mid_e,
                side_energy=side_e,
                side_mid_ratio=ratio,
                quality="OK",
            )
        )
    return out


def _dynamics(times: np.ndarray, rms: np.ndarray, peak: np.ndarray) -> list[DynamicObservation]:
    if len(times) == 0:
        return []
    hop = float(times[1] - times[0]) if len(times) > 1 else HOP_S
    win = max(1, int(round(DYNAMICS_WINDOW_S / max(hop, 1e-6))))
    out: list[DynamicObservation] = []
    for i in range(0, len(times), win):
        sl = slice(i, min(len(times), i + win))
        seg_rms = rms[sl]
        seg_peak = peak[sl]
        if len(seg_rms) == 0:
            continue
        mean_rms = float(np.mean(seg_rms))
        max_peak = float(np.max(seg_peak))
        crest = (max_peak / mean_rms) if mean_rms > 1e-12 else None
        p95 = float(np.percentile(seg_rms, 95))
        p5 = float(np.percentile(seg_rms, 5))
        dyn = _db(p95, p5) if p5 > 1e-12 else None
        quality = "OK" if mean_rms > 1e-8 else "LIMITED"
        out.append(
            DynamicObservation(
                window_start_s=float(times[sl][0]),
                window_end_s=float(times[sl][-1] + hop),
                crest_factor=crest,
                short_term_dynamic_range_db=dyn,
                peak=max_peak,
                rms=mean_rms,
                quality=quality,
                limitations=[] if quality == "OK" else ["LOW_ENERGY_DYNAMICS_LIMITED"],
            )
        )
    return out


def _cache_key(audio_sha256: str, analyzer_sha: str, config_sha: str) -> str:
    raw = f"{audio_sha256}:{analyzer_sha}:{config_sha}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def compute_fullmix_observation(
    path: Path | str,
    *,
    region_id: str,
    region_label: str | None = None,
    audio_sha256: str | None = None,
    use_cache: bool = True,
    root: Path | None = None,
) -> FullMixObservation:
    """Deterministic Main-mix MEASURE layer. No musical judgment terms."""
    wav_path = Path(path)
    if not wav_path.is_file():
        raise FileNotFoundError(wav_path)
    digest = audio_sha256 or sha256_file(wav_path) or ""
    if not digest:
        raise ValueError(f"audio hash missing: {wav_path}")
    analyzer_sha = analyzer_fingerprint(root)
    config_sha = configuration_hash()
    key = _cache_key(digest, analyzer_sha, config_sha)
    cache_file = _cache_path(key)
    if use_cache and cache_file.is_file():
        obs = FullMixObservation.model_validate(json.loads(cache_file.read_text(encoding="utf-8")))
        obs.cache_hit = True
        _assert_no_diagnosis_language(obs)
        return obs

    data, sample_rate = sf.read(str(wav_path), always_2d=True)
    mono = _mono(data)
    duration_s = float(len(mono) / float(sample_rate)) if sample_rate else 0.0
    times, rms, peak = _frame_stats(mono, int(sample_rate), WINDOW_S, HOP_S)
    median_frames = max(1, int(round(ROLLING_MEDIAN_S / HOP_S)))
    # p75 resists frequent valleys better than median while remaining deterministic.
    reference = _rolling_stat(rms, median_frames, q=75.0)
    frames = [
        EnergyFrame(
            t_s=float(t),
            rms=float(r),
            peak=float(p),
            relative_db=_db(float(r), float(ref)),
            reference_rms=float(ref),
            crest_factor=(float(p) / float(r)) if r > 1e-12 else None,
        )
        for t, r, p, ref in zip(times, rms, peak, reference)
    ]
    energy_events = _classify_energy_events(times, rms, reference, HOP_S)
    spectral_points, spectral_events = _spectral_trajectory(mono, int(sample_rate))
    transient = _transient_obs(mono, int(sample_rate), duration_s)
    stereo = _stereo_frames(data, int(sample_rate), times)
    dynamics = _dynamics(times, rms, peak)
    limitations = [
        "MEASURE_ONLY",
        "NO_MUSICAL_JUDGMENT",
        "THRESHOLDS_VERSIONED_IN_configuration_hash",
        "Cross-source timing claims remain ALIGNMENT_LIMITED ±52 ms; Main-internal grid is separate.",
    ]
    if data.shape[1] >= 2:
        left_rms = float(np.sqrt(np.mean(data[:, 0].astype(np.float64) ** 2)))
        right_rms = float(np.sqrt(np.mean(data[:, 1].astype(np.float64) ** 2)))
        if left_rms > 1e-8 and right_rms <= 1e-8:
            limitations.append("RIGHT_CHANNEL_NEAR_SILENT_IN_SOURCE")
        elif right_rms > 1e-8 and left_rms <= 1e-8:
            limitations.append("LEFT_CHANNEL_NEAR_SILENT_IN_SOURCE")

    obs = FullMixObservation(
        analyzer_id=ANALYZER_ID,
        analyzer_sha256=analyzer_sha,
        configuration_hash=config_sha,
        audio_sha256=digest,
        audio_path=str(wav_path),
        region_id=region_id,
        region_label=region_label or region_id,
        sample_rate=int(sample_rate),
        channels=int(data.shape[1]),
        duration_s=duration_s,
        window_s=WINDOW_S,
        hop_s=HOP_S,
        thresholds=thresholds_public(),
        energy_frames=frames,
        energy_events=energy_events,
        spectral_trajectory=spectral_points,
        spectral_events=spectral_events,
        transient=transient,
        stereo_frames=stereo,
        dynamics=dynamics,
        limitations=limitations,
        cache_hit=False,
    )
    _assert_no_diagnosis_language(obs)
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(obs.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return obs


def _assert_no_diagnosis_language(obs: FullMixObservation) -> None:
    # Scan human-facing text fields only; hashes may contain hex substrings like "bad".
    parts = [
        obs.analyzer_id,
        obs.region_id,
        obs.region_label,
        *obs.limitations,
        *[ev.kind.value for ev in obs.energy_events],
        *[ev.band for ev in obs.spectral_events],
        *[(t.limitations and " ".join(t.limitations)) or "" for t in ([obs.transient] if obs.transient else [])],
    ]
    blob = " ".join(str(p) for p in parts)
    hit = FORBIDDEN_DIAGNOSIS_RE.search(blob)
    if hit:
        raise ValueError(f"fullmix observation leaked diagnosis language: {hit.group(0)}")


def run_session_fullmix(
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    report_path = evidence / f"{source_run}.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    listen = list(report.get("HUMAN LISTEN MAIN") or [])
    captures = {
        (row.get("region") or {}).get("id"): row
        for row in report.get("CAPTURE ASSETS") or []
        if isinstance(row.get("region"), dict)
    }
    rows = []
    for item in listen:
        region_id = str(item["region"])
        path = Path(str(item["path"]))
        cap = captures.get(region_id) or {}
        hashes = cap.get("hashes") or {}
        expected = hashes.get("master")
        actual = sha256_file(path)
        if expected and actual and expected != actual:
            # listen file is a copy of master; hash must match master provenance
            raise ValueError(f"audio hash mismatch for {region_id}: {actual} != {expected}")
        label = f"{item.get('start_qn')}->{item.get('end_qn')}qn"
        obs = compute_fullmix_observation(
            path,
            region_id=region_id,
            region_label=label,
            audio_sha256=actual,
        )
        rows.append(obs.model_dump(mode="json"))
    payload = {
        "status": "FULLMIX OBSERVATION COMPLETE",
        "source_run": source_run,
        "analyzer_id": ANALYZER_ID,
        "analyzer_sha256": analyzer_fingerprint(),
        "configuration_hash": configuration_hash(),
        "MUSICAL WRITES": 0,
        "ASTRA CALLS": 0,
        "regions": rows,
    }
    out = evidence / f"{source_run}_fullmix.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload
