"""Read-only source inventory for the next HARMONY / CHORDS slice."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import soundfile as sf


def run(manifest_path: Path, output_path: Path) -> dict:
    if output_path.exists():
        raise FileExistsError(output_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = []
    for row in manifest["records"]:
        if row["role"] not in {"GUITAR", "PIANO", "OTHER"}:
            continue
        source = row["manifest"]
        path = Path(source["immutable_path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source["immutable_sha256"]:
            raise RuntimeError(f"SOURCE_HASH_MISMATCH: {row['role']}")
        windows = []
        with sf.SoundFile(str(path)) as reader:
            sr = reader.samplerate
            window_frames = int(round(7.68 * sr))
            for index in range(32):
                audio = reader.read(window_frames, dtype="float32", always_2d=True)
                if len(audio) != window_frames:
                    break
                windows.append({"start_s": round(index * 7.68, 6),
                                "rms": float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))})
        results.append({
            "role": row["role"], "source": str(path), "source_sha256": digest,
            "first_active_window": next((item for item in windows if item["rms"] > .001), None),
            "bass_reference_window": windows[18] if len(windows) > 18 else None,
            "window_count": len(windows),
        })
    report = {"status": "SOURCE_INVENTORY_ONLY", "tempo_bpm": 125,
              "musical_writes": 0, "model_api_calls": 0, "sources": results,
              "limitations": ["No chords, notes, or harmonic functions inferred here."]}
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest, args.output), indent=2))
