"""Rank already-indexed kick one-shots by factual spectral shape of one source hit."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.physical_dsp_v2.signal import band_energies, stft_power
from copilot.sample_library.config import index_path
from copilot.sample_library.library_v1 import load_index


def _fractions(audio: np.ndarray, sr: int) -> np.ndarray:
    frequencies, _, power, limitations, _ = stft_power(np.mean(audio, axis=1), sr)
    if limitations:
        raise RuntimeError(f"SPECTRAL_PROVIDER_UNAVAILABLE: {limitations}")
    values = np.asarray([float(np.sum(band)) for band in band_energies(frequencies, power).values()])
    return values / np.sum(values)


def run(source: Path) -> dict:
    with sf.SoundFile(str(source)) as handle:
        sr = int(handle.samplerate)
        reference = handle.read(frames=int(0.18 * sr), dtype="float64", always_2d=True)
    target = _fractions(reference, sr)
    index = load_index(index_path())
    if index is None:
        raise RuntimeError("SAMPLE_INDEX_UNAVAILABLE")
    candidates = []
    for asset in index.assets.values():
        if asset.semantic_role.value != "KICK" or not Path(asset.path).is_file():
            continue
        with sf.SoundFile(asset.path) as handle:
            if int(handle.samplerate) != sr:
                continue
            audio = handle.read(frames=int(0.18 * sr), dtype="float64", always_2d=True)
        shape = _fractions(audio, sr)
        candidates.append({"path": asset.path, "sha256": asset.sha256,
                           "band_l1_distance": float(np.sum(np.abs(target - shape))),
                           "band_fractions": shape.tolist()})
    return {"source_kick_window_s": [0.0, 0.18], "source_band_fractions": target.tolist(),
            "candidates": sorted(candidates, key=lambda row: row["band_l1_distance"])[:8],
            "limitation": "One isolated source window and broad-band shape only; no musical quality claim."}


if __name__ == "__main__":
    import sys
    print(json.dumps(run(Path(sys.argv[1])), indent=2))
