"""Key detection via chroma -> Krumhansl-Kessler profile matching.

Read-only, numpy/scipy only. Estimates root + mode for TONAL samples; returns
UNKNOWN honestly when evidence is weak. Percussive/noise roles are skipped
(a kick does not need a forced musical key).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import stft

from copilot.sample_library.schemas import SampleRole

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Kessler key profiles (standard major/minor probe profiles)
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# Roles where a musical key is meaningful
TONAL_ROLES = {
    SampleRole.BASS,
    SampleRole.CHORD,
    SampleRole.MELODY,
    SampleRole.SYNTH,
    SampleRole.VOCAL,
    SampleRole.VOCAL_CHOP,
    SampleRole.TOP_LOOP,
    SampleRole.DRUM_LOOP,
    SampleRole.TEXTURE,
    SampleRole.AMBIENCE,
}

_MIN_KEY_CORR = 0.45


def is_tonal(role: SampleRole) -> bool:
    return role in TONAL_ROLES


def compute_chroma(mono: np.ndarray, sr: int) -> np.ndarray | None:
    """12-dim pitch-class profile (energy per semitone), normalized."""
    if len(mono) < 4096:
        return None
    freqs, _, zxx = stft(mono, fs=sr, nperseg=4096, noverlap=2048)
    mag = np.abs(zxx)  # (freq_bins, frames)
    with np.errstate(divide="ignore", invalid="ignore"):
        pcs = np.round(12.0 * np.log2(np.maximum(freqs, 1.0) / 440.0) + 9.0).astype(int) % 12
    valid = (freqs >= 80.0) & (freqs <= 4000.0)
    chroma = np.zeros(12)
    for pc in range(12):
        chroma[pc] = float(np.sum(mag[(pcs == pc) & valid, :]))
    total = float(np.sum(chroma))
    if total <= 1e-12:
        return None
    return chroma / total


def _key_from_chroma(chroma: np.ndarray) -> dict[str, Any]:
    best_corr = -1.0
    best_root = 0
    best_mode = "major"
    for root in range(12):
        for mode, prof in (("major", KK_MAJOR), ("minor", KK_MINOR)):
            rotated = np.roll(prof, root)
            c = float(np.corrcoef(chroma, rotated)[0, 1])
            if c > best_corr:
                best_corr, best_root, best_mode = c, root, mode
    return {"root": best_root, "mode": best_mode, "correlation": best_corr}


def estimate_key(path: Path) -> dict[str, Any]:
    """Return {name, root, mode, confidence, chroma} or {'name': 'UNKNOWN'}."""
    try:
        data, sr = sf.read(str(path), always_2d=True)
    except Exception:
        return {"name": "UNKNOWN"}
    if data.size == 0:
        return {"name": "UNKNOWN"}
    mono = np.mean(data.astype(np.float64), axis=1)
    chroma = compute_chroma(mono, sr)
    if chroma is None:
        return {"name": "UNKNOWN"}
    k = _key_from_chroma(chroma)
    if k["correlation"] < _MIN_KEY_CORR:
        return {"name": "UNKNOWN", "root": k["root"], "mode": k["mode"], "confidence": round(k["correlation"], 3), "chroma": [round(float(c), 4) for c in chroma]}
    name = f"{PITCH_NAMES[k['root']]} {k['mode']}"
    return {"name": name, "root": k["root"], "mode": k["mode"], "confidence": round(k["correlation"], 3), "chroma": [round(float(c), 4) for c in chroma]}
