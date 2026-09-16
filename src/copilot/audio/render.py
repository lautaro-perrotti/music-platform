from __future__ import annotations

import numpy as np

from copilot.schemas.session import ClipState, TrackState

ABLETON_C3_HZ = 261.625565


def midi_to_hz(pitch: int) -> float:
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def render_clip_audio(
    track: TrackState,
    clip: ClipState,
    tempo_bpm: float,
    sample_rate: int = 44100,
    bass_gain: float = 1.0,
) -> np.ndarray:
    """Offline preview renderer. Not a Live audio tap."""
    seconds_per_beat = 60.0 / tempo_bpm
    duration = clip.length_beats * seconds_per_beat
    n = int(duration * sample_rate)
    mix = np.zeros(n, dtype=np.float64)
    volume = track.mixer.volume
    low_gain = _low_band_gain(track)
    for note in clip.notes:
        if note.mute:
            continue
        start = int(note.start_time * seconds_per_beat * sample_rate)
        length = int(note.duration * seconds_per_beat * sample_rate)
        end = min(n, start + length)
        if end <= start:
            continue
        t = np.arange(end - start) / sample_rate
        freq = midi_to_hz(note.pitch)
        wave = np.sin(2 * np.pi * freq * t)
        note_gain = bass_gain * low_gain if freq < 120 else 1.0
        env = np.linspace(1.0, 0.15, end - start)
        amp = (note.velocity / 127.0) * volume * note_gain
        mix[start:end] += wave * env * amp
    peak = np.max(np.abs(mix))
    if peak > 0.98:
        mix *= 0.98 / peak
    return mix.astype(np.float32)


def render_with_excess_bass(
    track: TrackState,
    clip: ClipState,
    tempo_bpm: float,
    sample_rate: int = 44100,
) -> np.ndarray:
    dry = render_clip_audio(track, clip, tempo_bpm, sample_rate, bass_gain=1.0)
    n = len(dry)
    t = np.arange(n) / sample_rate
    rumble = 0.45 * _low_band_gain(track) * np.sin(2 * np.pi * 55.0 * t)
    return np.clip(dry + rumble.astype(np.float32), -1.0, 1.0)


def _low_band_gain(track: TrackState) -> float:
    if not track.devices:
        return 1.0
    eq = next((p for p in track.devices[0].parameters if "Gain" in p.name), None)
    if eq is None:
        return 1.0
    # 0.5 = unity. Lower values cut only the simulated low band.
    return 0.25 + (eq.value * 1.5)
