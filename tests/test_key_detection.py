from __future__ import annotations

import numpy as np

from copilot.sample_library.key_detection import (
    PITCH_NAMES,
    _key_from_chroma,
    compute_chroma,
    is_tonal,
)
from copilot.sample_library.schemas import SampleRole


def _sine(freq, sr=44100, dur=2.0):
    t = np.arange(int(sr * dur)) / sr
    return 0.5 * np.sin(2 * np.pi * freq * t)


def test_chroma_maps_known_frequencies():
    sr = 44100
    assert int(np.argmax(compute_chroma(_sine(164.81, sr), sr))) == 4  # E3 -> E
    assert int(np.argmax(compute_chroma(_sine(440.0, sr), sr))) == 9  # A4 -> A


def test_is_tonal():
    assert is_tonal(SampleRole.BASS) is True
    assert is_tonal(SampleRole.CHORD) is True
    assert is_tonal(SampleRole.KICK) is False
    assert is_tonal(SampleRole.CLOSED_HAT) is False


def test_key_from_chroma(tmp_path):
    # synth C major triad -> should detect C major
    sr = 44100
    t = np.arange(int(sr * 2)) / sr
    x = 0.3 * (np.sin(2 * np.pi * 261.63 * t) + np.sin(2 * np.pi * 329.63 * t) + np.sin(2 * np.pi * 392.0 * t))
    c = compute_chroma(x, sr)
    k = _key_from_chroma(c)
    assert PITCH_NAMES[k["root"]] == "C"
    assert k["mode"] == "major"
