"""Load, hash, slice, and frame audio. No Live dependency."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.file_hash import sha256_file
from copilot.audio.physical_dsp_v2.contract import HOP_S, WINDOW_S
from copilot.schemas.dsp import DspGranularity, TimeSpan


@dataclass
class AudioBuffer:
    samples: np.ndarray  # (n, ch) float64
    sample_rate: int
    artifact_hash: str
    path: str | None = None
    origin_s: float = 0.0

    @property
    def mono(self) -> np.ndarray:
        data = np.asarray(self.samples, dtype=np.float64)
        if data.ndim == 1:
            return data
        return np.mean(data, axis=1)

    @property
    def left(self) -> np.ndarray:
        data = np.asarray(self.samples, dtype=np.float64)
        if data.ndim == 1:
            return data
        return data[:, 0]

    @property
    def right(self) -> np.ndarray | None:
        data = np.asarray(self.samples, dtype=np.float64)
        if data.ndim == 1 or data.shape[1] < 2:
            return None
        return data[:, 1]

    @property
    def channels(self) -> int:
        data = np.asarray(self.samples, dtype=np.float64)
        return 1 if data.ndim == 1 else int(data.shape[1])

    @property
    def n_samples(self) -> int:
        return int(np.asarray(self.samples).shape[0])

    @property
    def duration_s(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return float(self.n_samples) / float(self.sample_rate)

    def slice_seconds(self, start_s: float, end_s: float) -> AudioBuffer:
        sr = self.sample_rate
        a = max(0, int(round(start_s * sr)))
        b = min(self.n_samples, int(round(end_s * sr)))
        if b <= a:
            empty = np.zeros((0, self.channels), dtype=np.float64)
            return AudioBuffer(empty, sr, self.artifact_hash, self.path, origin_s=start_s)
        return AudioBuffer(self.samples[a:b], sr, self.artifact_hash, self.path, origin_s=start_s)


def hash_samples(samples: np.ndarray, sample_rate: int) -> str:
    arr = np.ascontiguousarray(np.asarray(samples, dtype=np.float64))
    digest = hashlib.sha256()
    digest.update(str(int(sample_rate)).encode("utf-8"))
    digest.update(str(arr.shape).encode("utf-8"))
    digest.update(arr.tobytes())
    return digest.hexdigest()


def load_audio(path: Path | str) -> AudioBuffer:
    wav = Path(path)
    if not wav.is_file():
        raise FileNotFoundError(wav)
    digest = sha256_file(wav)
    if not digest:
        raise ValueError(f"audio hash missing: {wav}")
    data, sr = sf.read(str(wav), always_2d=True)
    return AudioBuffer(
        samples=np.asarray(data, dtype=np.float64),
        sample_rate=int(sr),
        artifact_hash=digest,
        path=str(wav),
    )


def buffer_from_samples(
    samples: np.ndarray,
    sample_rate: int,
    *,
    artifact_hash: str | None = None,
    path: str | None = None,
) -> AudioBuffer:
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    digest = artifact_hash or hash_samples(data, sample_rate)
    return AudioBuffer(data, int(sample_rate), digest, path, origin_s=0.0)


def db(num: float, den: float = 1.0) -> float:
    n = max(float(num), 1e-12)
    d = max(float(den), 1e-12)
    return 20.0 * float(np.log10(n / d))


def amp_dbfs(value: float) -> float:
    return db(abs(value), 1.0)


def frame_signal(
    mono: np.ndarray,
    sample_rate: int,
    window_s: float = WINDOW_S,
    hop_s: float = HOP_S,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    win = max(1, int(round(window_s * sample_rate)))
    hop = max(1, int(round(hop_s * sample_rate)))
    if len(mono) < win:
        rms = np.array(
            [float(np.sqrt(np.mean(mono**2))) if len(mono) else 0.0],
            dtype=np.float64,
        )
        peak = np.array(
            [float(np.max(np.abs(mono))) if len(mono) else 0.0],
            dtype=np.float64,
        )
        times = np.array([0.0], dtype=np.float64)
        return times, rms, peak
    n = 1 + (len(mono) - win) // hop
    shape = (n, win)
    strides = (mono.strides[0] * hop, mono.strides[0])
    frames = np.lib.stride_tricks.as_strided(mono, shape=shape, strides=strides)
    rms = np.sqrt(np.mean(frames * frames, axis=1)).astype(np.float64)
    peak = np.max(np.abs(frames), axis=1).astype(np.float64)
    times = (np.arange(n, dtype=np.float64) * hop) / float(sample_rate)
    return times, rms, peak


def qn_to_seconds(qn: float, tempo_bpm: float) -> float:
    return float(qn) * 60.0 / float(tempo_bpm)


def seconds_to_qn(seconds: float, tempo_bpm: float) -> float:
    return float(seconds) * float(tempo_bpm) / 60.0


def whole_span(
    buffer: AudioBuffer,
    *,
    granularity: DspGranularity = DspGranularity.REGION,
    tempo_bpm: float | None = None,
    start_qn: float | None = None,
) -> TimeSpan:
    end_qn = None
    if tempo_bpm and tempo_bpm > 0:
        base = 0.0 if start_qn is None else float(start_qn)
        end_qn = base + seconds_to_qn(buffer.duration_s, tempo_bpm)
    return TimeSpan(
        start_s=0.0,
        end_s=buffer.duration_s,
        start_qn=start_qn,
        end_qn=end_qn,
        tempo_bpm=tempo_bpm,
        granularity=granularity,
    )
