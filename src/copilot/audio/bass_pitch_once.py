"""Bounded, inferred bass-note evidence for the FAST_LAB four-bar slice.

This is deliberately not a general transcription provider. It consumes the
already persisted attacks and preserves uncertain candidates as rejections.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

import numpy as np
import soundfile as sf


@dataclass(frozen=True)
class BassNote:
    source_onset_s: float
    source_window_start_s: float
    source_window_end_s: float
    local_onset_s: float
    duration_s: float
    pitch_hz: float
    midi_note: int
    pitch_confidence: float
    onset_confidence: float
    local_stability_cents: float
    authority: str
    source_sha256: str


def _pitch(frame: np.ndarray, sr: float) -> tuple[float | None, float, float]:
    """Normalized autocorrelation, 35–160 Hz; return Hz, periodicity, octave margin."""
    frame = frame - np.mean(frame)
    if len(frame) < int(sr * 0.07) or np.sqrt(np.mean(frame * frame)) < 0.002:
        return None, 0.0, 0.0
    lo, hi = int(sr / 160), int(sr / 35)
    scores = np.zeros(hi + 1)
    for lag in range(lo, hi + 1):
        a, b = frame[:-lag], frame[lag:]
        scores[lag] = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)
    best = int(np.argmax(scores[lo:]) + lo)
    peak = float(scores[best])
    # A high correlation at twice the frequency would make the octave unclear.
    half = best // 2
    octave_score = float(max(scores[max(lo, half - 2):min(hi + 1, half + 3)])) if half >= lo else 0.0
    margin = peak - octave_score
    if peak < 0.62 or margin < 0.12:
        return None, peak, margin
    return float(sr / best), peak, margin


def run(facts_path: Path, output_path: Path) -> dict:
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    source = Path(facts["source"])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != facts["source_sha256"]:
        raise RuntimeError("BASS_SOURCE_HASH_MISMATCH")
    audio, original_sr = sf.read(facts["wav"], always_2d=True)
    if len(audio) / original_sr < 7.67:
        raise RuntimeError("BASS_REGION_TOO_SHORT")
    mono = audio.mean(axis=1)[::4].astype(np.float64)
    sr = original_sr / 4
    attacks = list(facts["transients"]["attacks"])
    strengths = [float(row["strength"]) for row in attacks]
    # Weak spectral changes are not automatically new notes.
    onset_floor = max(0.008, float(median(strengths)))
    accepted: list[BassNote] = []
    rejected: list[dict] = []
    for index, attack in enumerate(attacks):
        onset = float(attack["time_s"])
        strength = float(attack["strength"])
        next_onset = float(attacks[index + 1]["time_s"]) if index + 1 < len(attacks) else 7.68
        end = min(next_onset - 0.008, onset + 0.19, 7.68)
        if strength < onset_floor:
            rejected.append({"onset_s": onset, "status": "INSUFFICIENT_EVIDENCE", "reason": "WEAK_ATTACK"})
            continue
        estimates = []
        for offset in (0.025, 0.04, 0.055):
            frame_end = min(onset + offset + 0.09, end)
            frame = mono[int((onset + offset) * sr):int(frame_end * sr)]
            hz, periodicity, octave_margin = _pitch(frame, sr)
            if hz is not None:
                estimates.append((hz, periodicity, octave_margin))
        if len(estimates) < 2:
            rejected.append({"onset_s": onset, "status": "INSUFFICIENT_EVIDENCE", "reason": "LOW_PERIODICITY_OR_OCTAVE_AMBIGUITY"})
            continue
        hz = float(median(row[0] for row in estimates))
        cents = [1200 * np.log2(row[0] / hz) for row in estimates]
        stability = float(max(cents) - min(cents))
        if stability > 45:
            rejected.append({"onset_s": onset, "status": "PITCH_UNSTABLE", "stability_cents": stability})
            continue
        midi_float = 69 + 12 * np.log2(hz / 440)
        midi_note = int(round(midi_float))
        if abs(midi_float - midi_note) > 0.4:
            rejected.append({"onset_s": onset, "status": "PITCH_UNSTABLE", "reason": "BETWEEN_SEMITONES"})
            continue
        duration = max(0.07, min(0.32, next_onset - onset - 0.015))
        accepted.append(BassNote(
            source_onset_s=float(facts["start_s"]) + onset,
            source_window_start_s=float(facts["start_s"]) + onset,
            source_window_end_s=float(facts["start_s"]) + end,
            local_onset_s=onset,
            duration_s=duration,
            pitch_hz=hz,
            midi_note=midi_note,
            pitch_confidence=float(min(1, median(row[1] for row in estimates))),
            onset_confidence=float(min(1, strength / (2 * onset_floor))),
            local_stability_cents=stability,
            authority="INFERRED",
            source_sha256=digest,
        ))
    report = {
        "provider": "BOUNDED_NUMPY_NORMALIZED_AUTOCORRELATION",
        "source": str(source), "source_sha256": digest,
        "source_start_s": facts["start_s"], "source_end_s": facts["end_s"],
        "tempo_bpm": facts["tempo_bpm"], "attacks": len(attacks),
        "onset_strength_floor": onset_floor,
        "accepted_notes": [asdict(note) for note in accepted],
        "rejected_attacks": rejected,
        "accepted_count": len(accepted), "rejected_count": len(rejected),
        "midi_range": [min((note.midi_note for note in accepted), default=None),
                       max((note.midi_note for note in accepted), default=None)],
        "median_pitch_confidence": median((note.pitch_confidence for note in accepted)) if accepted else None,
        "octave_ambiguity": "NO_ACCEPTED_OCTAVE_JUMPS" if len({note.midi_note for note in accepted}) <= 1 else "REVIEW_REQUIRED",
        "status": "INFERRED_NOT_HUMAN_VERIFIED",
        "limitations": ["Energy attacks may not all be note boundaries.",
                        "No accepted note is human-verified.",
                        "Pitch confidence is an uncalibrated periodicity heuristic, not a probability.",
                        "No inference about harmony or key."],
        "model_api_calls": 0, "musical_writes": 0,
    }
    if output_path.exists():
        raise FileExistsError(output_path)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {key: report[key] for key in ("provider", "attacks", "accepted_count", "rejected_count", "midi_range", "median_pitch_confidence", "octave_ambiguity")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("facts", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.facts, args.output), indent=2))
