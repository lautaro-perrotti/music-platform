from __future__ import annotations

import numpy as np

from copilot.audio.measure import measure_audio


def test_measure_does_not_invent_key_or_tempo() -> None:
    sr = 44100
    t = np.arange(int(sr * 2.0)) / sr
    samples = 0.2 * np.sin(2 * np.pi * 220 * t)
    obs = measure_audio(samples, sr, source="fixture", region="test")
    assert obs.key is None
    assert obs.tempo_bpm is None
    assert obs.signal.lufs is not None
    assert obs.signal.rms > 0
    assert obs.confidence["key"] == 0.0


def test_short_audio_skips_lufs() -> None:
    sr = 44100
    samples = np.zeros(int(sr * 0.1), dtype=np.float64)
    obs = measure_audio(samples, sr, source="fixture", region="short")
    assert obs.signal.lufs is None
    assert obs.signal.duration_seconds < 0.2
