"""Physical-signal gate used when checking a generated Arrangement clip."""

import numpy as np
import soundfile as sf

from copilot.audio.live_capture import SILENCE_PEAK, SILENCE_RMS, validate_wav


def test_arrangement_capture_requires_finite_non_silent_audio(tmp_path):
    rate = 44100
    t = np.arange(rate, dtype=np.float64) / rate
    signal = np.column_stack((0.1 * np.sin(2 * np.pi * 220 * t),) * 2)
    audible = tmp_path / "audible.wav"
    sf.write(audible, signal, rate)
    check = validate_wav(
        audible, expected_sr=rate, expected_duration=1.0, require_signal=True
    )
    assert check.ok and check.finite_samples
    assert check.rms > SILENCE_RMS and check.peak > SILENCE_PEAK

    silent = tmp_path / "silent.wav"
    sf.write(silent, np.zeros_like(signal), rate)
    rejected = validate_wav(
        silent, expected_sr=rate, expected_duration=1.0, require_signal=True
    )
    assert not rejected.ok
    assert any("silent" in error for error in rejected.errors)
