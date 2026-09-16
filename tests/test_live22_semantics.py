from __future__ import annotations

import numpy as np
import soundfile as sf

from copilot.audio.live_capture import validate_wav
from copilot.audio.measure import measure_audio
from copilot.audio.semantics import (
    classify_signal_point,
    expected_analysis_seconds,
    region_windows,
)
from copilot.midi.time import bars_to_beats
from copilot.schemas.observation import ObservationSource, SignalPoint, TailPolicy


def test_region_windows_strict_preroll() -> None:
    windows = region_windows(9.0, 13.0, preroll_beats=0.5)
    assert windows["request_start_beat"] == 9.0
    assert windows["request_end_beat"] == 13.0
    assert windows["capture_start_beat"] == 8.5
    assert windows["analysis_start_beat"] == 9.0
    assert windows["analysis_end_beat"] == 13.0
    assert windows["tail_policy"] == TailPolicy.STRICT_REGION.value


def test_region_windows_no_negative_preroll() -> None:
    windows = region_windows(0.0, 16.0, preroll_beats=0.5)
    assert windows["capture_start_beat"] == 0.0


def test_signal_point_names() -> None:
    assert classify_signal_point("Post Mixer") is SignalPoint.TRACK_POST_MIXER
    assert classify_signal_point("Post-FX") is SignalPoint.POST_FX
    assert classify_signal_point("Pre FX") is SignalPoint.PRE_FX
    assert classify_signal_point("Something Else") is SignalPoint.UNKNOWN


def test_tempo_duration_not_bar_assumption() -> None:
    assert expected_analysis_seconds(0, 16, 120.0) == 8.0
    assert expected_analysis_seconds(0, 16, 90.0) == 16 * 60 / 90
    assert bars_to_beats(1, 3, 4) == 3.0
    assert bars_to_beats(1, 6, 8) == 3.0
    assert bars_to_beats(1, 4, 4) == 4.0


def test_observation_declares_source(tmp_path) -> None:
    sr = 44100
    samples = 0.2 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)
    obs = measure_audio(
        samples,
        sr,
        source="test",
        region="0-16",
        observation_source=ObservationSource.TRACK_ISOLATED,
        signal_point=SignalPoint.TRACK_POST_MIXER,
        signal_point_label="Post Mixer",
    )
    assert obs.observation_source is ObservationSource.TRACK_ISOLATED
    assert obs.signal_point is SignalPoint.TRACK_POST_MIXER
    assert obs.key is None


def test_validate_wav_rejects_nan(tmp_path) -> None:
    path = tmp_path / "bad.wav"
    data = np.zeros((44100, 2), dtype=np.float32)
    data[10, 0] = np.nan
    sf.write(str(path), data, 44100, subtype="FLOAT")
    check = validate_wav(path, expected_sr=44100, expected_duration=1.0, require_signal=False)
    assert check.ok is False
    assert check.finite_samples is False
    assert any("NaN" in err or "finite" in err for err in check.errors)
