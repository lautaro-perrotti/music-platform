"""Real source/capture A/B evidence and centered human review copies."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.bass_pitch_once import _pitch
from copilot.audio.lowend import detect_transients


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))


def _facts(audio: np.ndarray, sr: int) -> dict:
    mono = audio.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(mono)) ** 2
    frequencies = np.fft.rfftfreq(len(mono), 1 / sr)
    total = float(np.sum(spectrum)) + 1e-20
    return {
        "duration_s": len(audio) / sr,
        "rms_by_channel": [_rms(audio[:, index]) for index in range(audio.shape[1])],
        "peak_by_channel": [float(np.max(np.abs(audio[:, index]))) for index in range(audio.shape[1])],
        "band_power_fraction": {
            f"{lo}-{hi}Hz": float(np.sum(spectrum[(frequencies >= lo) & (frequencies < hi)]) / total)
            for lo, hi in ((20, 80), (80, 250), (250, 1000), (1000, 8000))
        },
    }


def _center_if_needed(audio: np.ndarray) -> np.ndarray:
    if audio.shape[1] == 1 or _rms(audio[:, 1]) < _rms(audio[:, 0]) * 0.01:
        return np.repeat(audio[:, :1], 2, axis=1)
    return audio[:, :2]


def run(evidence_path: Path, capture_path: Path, out_dir: Path, label: str) -> dict:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    source_path = Path(evidence["source"])
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != evidence["source_sha256"]:
        raise RuntimeError("SOURCE_HASH_MISMATCH")
    with sf.SoundFile(str(source_path)) as reader:
        sr = reader.samplerate
        reader.seek(int(round(float(evidence["source_start_s"]) * sr)))
        source = reader.read(int(round(7.68 * sr)), dtype="float32", always_2d=True)
    captured, capture_sr = sf.read(str(capture_path), always_2d=True, dtype="float32")
    if sr != capture_sr or source.shape[0] != captured.shape[0]:
        raise RuntimeError("A_B_RATE_OR_DURATION_MISMATCH")
    out_dir.mkdir(parents=True, exist_ok=True)
    original_path = out_dir / "original_bass_4bars.wav"
    review_path = out_dir / f"bass_reconstruction_{label}.wav"
    raw_path = out_dir / f"bass_reconstruction_{label}_raw.wav"
    report_path = out_dir / f"bass_ab_{label}.json"
    for path in (review_path, raw_path, report_path):
        if path.exists():
            raise FileExistsError(path)
    if not original_path.exists():
        sf.write(str(original_path), _center_if_needed(source), sr, subtype="FLOAT")
    shutil.copy2(capture_path, raw_path)
    centered = _center_if_needed(captured)
    sf.write(str(review_path), centered, sr, subtype="FLOAT")
    source_attacks = json.loads((evidence_path.parent / "bass_first_active_facts.json").read_text(encoding="utf-8"))["transients"]["attacks"]
    observed = detect_transients(captured[:, 0], sr, min_distance_s=0.12, role="bass_unlabeled")["attacks"]
    used: set[int] = set()
    comparisons = []
    for note in evidence["accepted_notes"]:
        onset = float(note["local_onset_s"])
        nearby = sorted((abs(float(row["time_s"]) - onset), index, row)
                        for index, row in enumerate(observed)
                        if index not in used and abs(float(row["time_s"]) - onset) <= 0.10)
        chosen = nearby[0] if nearby else None
        if chosen:
            used.add(chosen[1])
        # Pitch contour is measured independently in the captured audio.
        t = float(chosen[2]["time_s"]) if chosen else onset
        frame = captured[int((t + .04) * sr):int((t + .15) * sr), 0][::4].astype(np.float64)
        hz, periodicity, octave_margin = _pitch(frame, sr / 4)
        midi_float = 69 + 12 * np.log2(hz / 440) if hz else None
        comparisons.append({
            "source_onset_s": onset,
            "reconstruction_onset_s": float(chosen[2]["time_s"]) if chosen else None,
            "onset_delta_ms": (float(chosen[2]["time_s"]) - onset) * 1000 if chosen else None,
            "source_pitch_hz": note["pitch_hz"], "source_midi_note": note["midi_note"],
            "reconstruction_pitch_hz": hz, "reconstruction_midi_float": float(midi_float) if midi_float else None,
            "reconstruction_periodicity": periodicity, "reconstruction_octave_margin": octave_margin,
        })
    report = {
        "source": str(source_path), "source_sha256": evidence["source_sha256"],
        "capture": str(capture_path), "capture_sha256": hashlib.sha256(capture_path.read_bytes()).hexdigest(),
        "source_facts": _facts(source, sr), "reconstruction_facts": _facts(captured, sr),
        "source_candidate_attacks": len(source_attacks), "accepted_inferred_notes": len(comparisons),
        "captured_detected_attacks": len(observed),
        "matched_accepted_notes": sum(row["reconstruction_onset_s"] is not None for row in comparisons),
        "comparisons": comparisons,
        "files": {"original": str(original_path), "reconstruction": str(review_path), "raw": str(raw_path)},
        "limitations": [
            "Accepted source notes remain INFERRED, not human verified.",
            "Onset matching uses energy transients with 100 ms tolerance; capture alignment is LIMITED.",
            "Raw capture is preserved; review file only duplicates left channel when right is nearly silent.",
            "A/B level is not normalized and no single quality score is inferred.",
        ],
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"report": str(report_path), "files": report["files"],
            "source": report["source_facts"], "reconstruction": report["reconstruction_facts"],
            "matched": report["matched_accepted_notes"], "expected": len(comparisons)}


def build_review_bundle(out_dir: Path) -> Path:
    """Optional listener package: unnormalized original, V1, V2 with gaps."""
    output = out_dir / "ABC_bass_original_v1_v2.wav"
    if output.exists():
        raise FileExistsError(output)
    paths = [out_dir / name for name in (
        "original_bass_4bars.wav", "bass_reconstruction_v1.wav", "bass_reconstruction_v2.wav")]
    decoded = [sf.read(str(path), always_2d=True, dtype="float32") for path in paths]
    rates = {rate for _, rate in decoded}
    lengths = {len(audio) for audio, _ in decoded}
    if len(rates) != 1 or len(lengths) != 1:
        raise RuntimeError("BASS_REVIEW_BUNDLE_ALIGNMENT_MISMATCH")
    sr = decoded[0][1]
    gap = np.zeros((int(sr * 0.5), 2), dtype="float32")
    sf.write(str(output), np.concatenate([decoded[0][0], gap, decoded[1][0], gap, decoded[2][0]]), sr, subtype="FLOAT")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("capture", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.evidence, args.capture, args.out_dir, args.label), indent=2))
