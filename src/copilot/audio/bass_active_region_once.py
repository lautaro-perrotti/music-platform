"""Choose the first measured active four-bar bass window; do not infer notes."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.lowend import detect_transients, estimate_fundamental


def run(manifest_path: Path, output_dir: Path) -> dict:
    records = json.loads(manifest_path.read_text(encoding="utf-8"))["records"]
    bass = [row["manifest"] for row in records if row["role"] == "BASS"]
    if len(bass) != 1:
        raise RuntimeError("BASS_SOURCE_AMBIGUOUS")
    source = bass[0]
    path = Path(source["immutable_path"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != source["immutable_sha256"]:
        raise RuntimeError("BASS_SOURCE_HASH_MISMATCH")
    selected = None
    observed_windows = []
    with sf.SoundFile(str(path)) as reader:
        sr = int(reader.samplerate)
        frames = int(round(16 * 60 / 125 * sr))
        for window_index in range(32):
            audio = reader.read(frames, dtype="float32", always_2d=True)
            if len(audio) != frames:
                break
            rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
            observed_windows.append({"window_index": window_index, "start_s": window_index * 7.68, "rms": rms})
            if rms > 0.001:
                selected = (window_index, audio)
                break
    if selected is None:
        raise RuntimeError("NO_ACTIVE_BASS_WINDOW")
    window_index, audio = selected
    output_dir.mkdir(parents=True, exist_ok=True)
    wav = output_dir / "bass_first_active_4bars.wav"
    report_path = output_dir / "bass_first_active_facts.json"
    if wav.exists() or report_path.exists():
        raise FileExistsError("ACTIVE_BASS_REGION_ALREADY_EXISTS")
    sf.write(str(wav), audio, sr, subtype="FLOAT")
    attacks = detect_transients(audio, sr, min_distance_s=0.12, role="bass_unlabeled")
    peak = estimate_fundamental(audio, sr)
    report = {
        "source": str(path), "source_sha256": digest,
        "window_index": window_index, "start_s": window_index * 7.68,
        "end_s": (window_index + 1) * 7.68, "tempo_bpm": 125,
        "selection_threshold_rms": 0.001, "observed_windows": observed_windows,
        "wav": str(wav), "transients": attacks, "region_low_band_peak": peak,
        "note_events": [], "pitch_status": "PROVIDER_UNAVAILABLE_NUMBA_DLL_BLOCKED",
        "limitations": ["Transients are not verified note starts.", "Region spectral peak is not note transcription."],
        "musical_writes": 0, "model_api_calls": 0,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"report": str(report_path), "wav": str(wav), "start_s": report["start_s"],
            "rms": observed_windows[-1]["rms"], "transient_count": attacks["count"],
            "region_low_band_peak": peak, "pitch_status": report["pitch_status"]}


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
