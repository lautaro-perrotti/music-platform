"""One bounded, factual comparison of a drum-stem region and Live proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

from copilot.audio.drum_events_v1 import DrumEventSetV1
from copilot.audio.lowend import detect_transients
from copilot.audio.physical_dsp_v2.signal import band_energies, spectral_centroid, stft_power
from copilot.musicplan.drum_reconstruction_v1 import build_drum_reconstruction


def _facts(signal: np.ndarray, sr: int) -> dict:
    mono = np.mean(signal, axis=1)
    freqs, _, power, limitations, quality = stft_power(mono, sr)
    bands = band_energies(freqs, power)
    totals = {name: float(np.sum(value)) for name, value in bands.items()}
    total = sum(totals.values())
    return {
        "rms_by_channel": np.sqrt(np.mean(signal * signal, axis=0)).tolist(),
        "peak_by_channel": np.max(np.abs(signal), axis=0).tolist(),
        "spectral_centroid_hz": spectral_centroid(freqs, power),
        "band_fraction": {name: value / total if total else None for name, value in totals.items()},
        "transient_times_s": [row["time_s"] for row in detect_transients(mono, sr, min_distance_s=0.10)["attacks"]],
        "spectral_quality": str(quality),
        "spectral_limitations": [str(item) for item in limitations],
    }


def _filtered_attacks(mono: np.ndarray, sr: int, *, role: str) -> list[float]:
    frequency, mode = (250, "low") if role == "KICK" else (2000, "high")
    filtered = sosfilt(butter(4, frequency, fs=sr, btype=mode, output="sos"), mono)
    return [float(item["time_s"]) for item in detect_transients(filtered, sr, min_distance_s=0.17)["attacks"]]


def run(event_path: Path, captured_wav: Path, output_dir: Path, *, label: str) -> dict:
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    events = DrumEventSetV1.model_validate(payload)
    source = Path(events.source_path)
    if hashlib.sha256(source.read_bytes()).hexdigest() != events.source_sha256:
        raise ValueError("SOURCE_HASH_MISMATCH")
    if not captured_wav.is_file():
        raise FileNotFoundError(captured_wav)
    duration = events.region_end_seconds - events.region_start_seconds
    with sf.SoundFile(str(source)) as reader:
        sr = int(reader.samplerate)
        if reader.channels != 2 or events.region_start_seconds != 0.0:
            raise ValueError("EXPECTED_STEREO_REGION_FROM_ZERO")
        reference = reader.read(frames=int(round(duration * sr)), dtype="float64", always_2d=True)
    capture, capture_sr = sf.read(str(captured_wav), always_2d=True)
    if capture_sr != sr or reference.shape != capture.shape:
        raise ValueError("REGION_OR_SAMPLE_RATE_MISMATCH")
    if not np.isfinite(reference).all() or not np.isfinite(capture).all():
        raise ValueError("NONFINITE_AUDIO")
    if np.max(np.abs(capture[:, 1])) > 1e-8:
        raise ValueError("EXPECTED_LEFT_ONLY_RAW_CAPTURE_FOR_THIS_PROOF")
    reconstructed = build_drum_reconstruction(events)
    if len(reconstructed.events) != 32:
        raise ValueError("EXPECTED_32_EVENTS")

    output_dir.mkdir(parents=True, exist_ok=True)
    original_path = output_dir / "original_4bars.wav"
    raw_path = output_dir / f"reconstruction_{label}_raw.wav"
    review_path = output_dir / f"reconstruction_{label}_review_stereo.wav"
    alternating_path = output_dir / f"AB_{label}_alternating.wav"
    report_path = output_dir / f"drum_ab_{label}.json"
    for path in (raw_path, review_path, alternating_path, report_path):
        if path.exists():
            raise FileExistsError(path)
    if original_path.exists():
        previous, previous_sr = sf.read(str(original_path), always_2d=True)
        if previous_sr != sr or not np.array_equal(previous, reference.astype(np.float32)):
            raise ValueError("EXISTING_ORIGINAL_REGION_MISMATCH")
    else:
        sf.write(str(original_path), reference, sr, subtype="FLOAT")
    shutil.copy2(captured_wav, raw_path)
    centered = np.repeat(capture[:, :1], 2, axis=1)
    sf.write(str(review_path), centered, sr, subtype="FLOAT")
    silence = np.zeros((int(round(0.35 * sr)), 2), dtype=np.float64)
    sf.write(str(alternating_path), np.concatenate((reference, silence, centered, silence,
                                                     reference, silence, centered)), sr, subtype="FLOAT")

    attacks = {role: _filtered_attacks(capture[:, 0], sr, role=role) for role in ("KICK", "CLOSED_HAT")}
    used: dict[str, set[int]] = {role: set() for role in attacks}
    rows = []
    for event in reconstructed.events:
        candidates = [(abs(time - event.observed_onset_seconds), index, time)
                      for index, time in enumerate(attacks[event.role])
                      if index not in used[event.role] and abs(time - event.observed_onset_seconds) <= 0.10]
        selected = min(candidates) if candidates else None
        if selected is not None:
            used[event.role].add(selected[1])
        rows.append({
            "source_event_id": event.source_event_id,
            "expected_onset_s": event.observed_onset_seconds,
            "reconstructed_onset_s": selected[2] if selected else None,
            "delta_ms": round((selected[2] - event.observed_onset_seconds) * 1000, 2) if selected else None,
            "inferred_role": event.role,
            "role_status": event.role_status,
            "midi_note": event.midi_note,
            "source_accent_dbfs": event.accent_rms_dbfs,
            "midi_velocity": event.velocity,
            "matched_transient": selected is not None,
            "limitation": "ENERGY_ONSET_NOT_ISOLATED_INSTRUMENT; CAPTURE_ALIGNMENT_LIMITED",
        })
    original = _facts(reference, sr)
    realized = _facts(capture[:, :1], sr)
    report = {
        "source": str(source),
        "source_sha256": events.source_sha256,
        "capture": str(captured_wav),
        "capture_sha256": hashlib.sha256(captured_wav.read_bytes()).hexdigest(),
        "duration_s": duration,
        "sample_rate": sr,
        "reference": original,
        "reconstruction_active_left": realized,
        "source_selected_event_count": len(rows),
        "source_detected_transient_count": len(original["transient_times_s"]),
        "reconstruction_detected_transient_count": len(realized["transient_times_s"]),
        "matched_selected_events": sum(row["matched_transient"] for row in rows),
        "unmatched_source_event_ids": [row["source_event_id"] for row in rows if not row["matched_transient"]],
        "event_rows": rows,
        "files": {"original": str(original_path), "raw": str(raw_path),
                  "review_copy": str(review_path), "alternating": str(alternating_path)},
        "limitations": [
            "Source roles are INFERRED, not human-verified.",
            "Source transient detector also sees attacks outside the selected 32; this is not a labeled missing-instrument count.",
            "Capture V1 reported CAPTURE_QUALITY_WARNING; event deltas are approximate, not sample-accurate.",
            "Raw capture has only left-channel signal; review copy duplicates left to right without gain normalization.",
            "A/B retains level differences; decoded MP3 original is not bit-identical to its compressed source.",
        ],
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"report": str(report_path), "files": report["files"],
            "source_transients": report["source_detected_transient_count"],
            "captured_transients": report["reconstruction_detected_transient_count"],
            "matched_selected": report["matched_selected_events"],
            "unmatched_selected": len(report["unmatched_source_event_ids"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("event_artifact", type=Path)
    parser.add_argument("captured_wav", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.event_artifact, args.captured_wav, args.output_dir, label=args.label), indent=2))


if __name__ == "__main__":
    main()
