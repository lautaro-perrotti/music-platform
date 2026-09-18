"""Deterministic synthetic signals for PHYSICAL_DSP_V2 tests. No Live."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def write_wav(path: Path, data: np.ndarray, sr: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if data.ndim == 1:
        data = np.column_stack([data, data])
    sf.write(str(path), np.asarray(data, dtype=np.float32), sr)
    return path


def silence(sr: int = 44100, duration_s: float = 1.0, channels: int = 2) -> np.ndarray:
    return np.zeros((int(sr * duration_s), channels), dtype=np.float64)


def sine(
    sr: int = 44100,
    duration_s: float = 1.0,
    freq: float = 440.0,
    amp: float = 0.2,
    channels: int = 2,
) -> np.ndarray:
    t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
    tone = amp * np.sin(2.0 * np.pi * freq * t)
    if channels == 1:
        return tone.reshape(-1, 1)
    return np.column_stack([tone, tone])


def mixture(sr: int, duration_s: float, partials: list[tuple[float, float]]) -> np.ndarray:
    n = int(sr * duration_s)
    t = np.arange(n, dtype=np.float64) / sr
    mono = np.zeros(n, dtype=np.float64)
    for freq, amp in partials:
        mono += amp * np.sin(2.0 * np.pi * freq * t)
    return np.column_stack([mono, mono])


def impulse_train(sr: int, duration_s: float, period_s: float, width: int = 32, amp: float = 0.9) -> np.ndarray:
    n = int(sr * duration_s)
    mono = np.zeros(n, dtype=np.float64)
    step = max(1, int(round(period_s * sr)))
    for i in range(0, n, step):
        mono[i : i + width] = amp
    return np.column_stack([mono, mono])


def hard_pan_left(sr: int = 44100, duration_s: float = 1.0, freq: float = 330.0, amp: float = 0.25) -> np.ndarray:
    t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
    tone = amp * np.sin(2.0 * np.pi * freq * t)
    return np.column_stack([tone, np.zeros_like(tone)])


def identical_stereo(sr: int = 44100, duration_s: float = 1.0, freq: float = 220.0, amp: float = 0.2) -> np.ndarray:
    return sine(sr, duration_s, freq, amp, channels=2)


def phase_inverted(sr: int = 44100, duration_s: float = 1.0, freq: float = 220.0, amp: float = 0.2) -> np.ndarray:
    t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
    tone = amp * np.sin(2.0 * np.pi * freq * t)
    return np.column_stack([tone, -tone])


def level_pair(sr: int, duration_s: float, amp_a: float, amp_b: float, freq: float = 200.0) -> tuple[np.ndarray, np.ndarray]:
    return sine(sr, duration_s, freq, amp_a), sine(sr, duration_s, freq, amp_b)


def dynamic_envelope(sr: int = 44100, duration_s: float = 2.0, freq: float = 180.0) -> np.ndarray:
    t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
    env = np.linspace(0.02, 0.4, len(t))
    tone = env * np.sin(2.0 * np.pi * freq * t)
    return np.column_stack([tone, tone])


def mono_sine(sr: int = 44100, duration_s: float = 1.0, freq: float = 440.0, amp: float = 0.2) -> np.ndarray:
    return sine(sr, duration_s, freq, amp, channels=1)
