from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.live_capture import (
    beats_to_seconds,
    find_onset_samples,
    write_analysis_wav,
)


def test_beats_to_seconds_uses_session_tempo() -> None:
    assert abs(beats_to_seconds(16, 120.0) - 8.0) < 1e-9
    assert abs(beats_to_seconds(16, 100.0) - 9.6) < 1e-9


def test_trim_uses_onset_not_raw_prefix(tmp_path: Path) -> None:
    sr = 44100
    pad = np.zeros((int(0.2 * sr), 2), dtype=np.float32)
    low = np.column_stack(
        [
            0.2 * np.sin(2 * np.pi * 80 * np.arange(int(4 * sr)) / sr),
            0.2 * np.sin(2 * np.pi * 80 * np.arange(int(4 * sr)) / sr),
        ]
    ).astype(np.float32)
    high = np.column_stack(
        [
            0.2 * np.sin(2 * np.pi * 2000 * np.arange(int(4 * sr)) / sr),
            0.2 * np.sin(2 * np.pi * 2000 * np.arange(int(4 * sr)) / sr),
        ]
    ).astype(np.float32)
    raw = np.vstack([pad, low, high])
    raw_path = tmp_path / "raw.wav"
    dest = tmp_path / "late.wav"
    sf.write(raw_path, raw, sr, subtype="FLOAT")
    trim = write_analysis_wav(
        raw_path,
        dest,
        start_beat=8.0,
        end_beat=16.0,
        tempo=120.0,
        play_offset_seconds=0.2,
        align="transport",
    )
    assert abs(float(trim["analysis_duration"]) - 4.0) < 0.02
    assert int(trim["trim_start_samples"]) > int(0.2 * sr)
    data, _ = sf.read(dest, always_2d=True)
    # late region must be the high tone, not the first 4s of audible raw
    spec = np.abs(np.fft.rfft(np.mean(data, axis=1)))
    freqs = np.fft.rfftfreq(len(data), 1.0 / sr)
    peak_hz = float(freqs[int(np.argmax(spec))])
    assert peak_hz > 1000.0


def test_transport_trim_keeps_requested_silence(tmp_path: Path) -> None:
    sr = 44100
    silence = np.zeros((int(4 * sr), 2), dtype=np.float32)
    tone = np.full((int(4 * sr), 2), 0.3, dtype=np.float32)
    raw = np.vstack([silence, tone])
    raw_path = tmp_path / "raw.wav"
    dest = tmp_path / "early.wav"
    sf.write(raw_path, raw, sr, subtype="FLOAT")
    onset_trim = write_analysis_wav(
        raw_path,
        tmp_path / "onset.wav",
        start_beat=0.0,
        end_beat=8.0,
        tempo=120.0,
        play_offset_seconds=0.0,
        align="onset",
    )
    transport_trim = write_analysis_wav(
        raw_path,
        dest,
        start_beat=0.0,
        end_beat=8.0,
        tempo=120.0,
        play_offset_seconds=0.0,
        align="transport",
    )
    onset_audio, _ = sf.read(tmp_path / "onset.wav", always_2d=True)
    transport_audio, _ = sf.read(dest, always_2d=True)
    assert float(np.sqrt(np.mean(onset_audio**2))) > 0.1
    assert float(np.sqrt(np.mean(transport_audio**2))) < 0.05
    assert int(transport_trim["trim_start_samples"]) == 0
    assert int(onset_trim["trim_start_samples"]) > 0


def test_onset_skips_leading_silence() -> None:
    x = np.zeros((1000, 2))
    x[200:, :] = 0.1
    assert find_onset_samples(x, threshold=0.01) == 200
