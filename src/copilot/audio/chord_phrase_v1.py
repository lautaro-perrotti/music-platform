"""Bounded triad hypotheses from existing chroma, with explicit abstention."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.harmonic_understanding_v1 import PITCH_CLASSES, _read_chroma
from copilot.schemas.chord_events import ChordEvent, HarmonicPhrase


def _rank_triads(weights: np.ndarray) -> list[dict]:
    rows = []
    for root in range(12):
        for quality, third in (("minor", 3), ("major", 4)):
            indices = (root, (root + third) % 12, (root + 7) % 12)
            score = float(.32 * weights[indices[0]] + .38 * weights[indices[1]] + .30 * weights[indices[2]])
            rows.append({"root": PITCH_CLASSES[root], "quality": quality,
                         "label": PITCH_CLASSES[root] + ("m" if quality == "minor" else ""),
                         "pitch_classes": [PITCH_CLASSES[index] for index in indices],
                         "score": score, "tone_support": [float(weights[index]) for index in indices]})
    return sorted(rows, key=lambda item: (item["score"], item["label"]), reverse=True)


def infer_phrase(manifest_path: Path, output_path: Path, *, start_s: float, bars: int = 4,
                 tempo_bpm: float = 125.0) -> HarmonicPhrase:
    if output_path.exists():
        raise FileExistsError(output_path)
    if bars not in {4, 8} or start_s < 0:
        raise ValueError("BOUNDED_REGION_REQUIRED")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = {row["role"]: row["manifest"] for row in manifest["records"]}
    duration_s = bars * 4 * 60 / tempo_bpm
    source_refs = {}
    chroma = {}
    for role in ("PIANO", "OTHER", "BASS"):
        record = records[role]
        path = Path(record["immutable_path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != record["immutable_sha256"]:
            raise RuntimeError(f"SOURCE_HASH_MISMATCH: {role}")
        source_refs[role] = digest
        if role != "BASS":
            chroma[role] = _read_chroma(path)[:2]
    windows = []
    for bar in range(bars):
        lo = start_s + bar * 4 * 60 / tempo_bpm
        hi = lo + 4 * 60 / tempo_bpm
        vectors = {}
        for role in ("PIANO", "OTHER"):
            frames, times = chroma[role]
            mask = (times >= lo) & (times < hi)
            vector = np.mean(frames[mask], axis=0) if np.any(mask) else np.zeros(12)
            vectors[role] = vector / vector.sum() if vector.sum() else vector
        # PIANO is more specific but sparse; OTHER is supporting, not pure.
        weights = .6 * vectors["PIANO"] + .4 * vectors["OTHER"]
        if weights.sum():
            weights /= weights.sum()
        ranked = _rank_triads(weights) if weights.sum() else []
        best, second = (ranked[0], ranked[1]) if len(ranked) >= 2 else (None, None)
        margin = best["score"] - second["score"] if best and second else 0.0
        supported = bool(best and margin >= .02 and min(best["tone_support"]) >= .06)
        windows.append({
            "bar_index": bar, "start_s": lo, "end_s": hi, "start_qn": bar * 4,
            "pitch_class_energy": {PITCH_CLASSES[index]: round(float(value), 5) for index, value in enumerate(weights)},
            "candidates": ranked[:3], "margin": margin,
            "status": "CHORD_INFERRED" if supported else "INSUFFICIENT_EVIDENCE",
            "selected": best["label"] if supported else None,
            "evidence_refs": [f"PIANO:{source_refs['PIANO']}:{lo:.3f}-{hi:.3f}",
                              f"OTHER:{source_refs['OTHER']}:{lo:.3f}-{hi:.3f}"],
        })
    chord_events = []
    for window in windows:
        if window["status"] != "CHORD_INFERRED":
            continue
        candidate = window["candidates"][0]
        confidence = min(.70, .40 + 4 * window["margin"])
        if chord_events and chord_events[-1].root == candidate["root"] and chord_events[-1].quality == candidate["quality"] \
                and chord_events[-1].start_qn + chord_events[-1].duration_qn == window["start_qn"]:
            chord_events[-1].duration_qn += 4
            chord_events[-1].confidence = min(chord_events[-1].confidence, confidence)
            chord_events[-1].evidence_refs.extend(window["evidence_refs"])
        else:
            chord_events.append(ChordEvent(start_qn=window["start_qn"], duration_qn=4,
                                           root=candidate["root"], quality=candidate["quality"],
                                           pitch_classes=candidate["pitch_classes"], confidence=confidence,
                                           evidence_refs=window["evidence_refs"]))
    phrase = HarmonicPhrase(source_start_s=start_s, source_end_s=start_s + duration_s,
                            tempo_bpm=tempo_bpm, chord_events=chord_events, windows=windows,
                            source_refs=source_refs,
                            limitations=["Triad templates only; extensions and inversions are not claimed.",
                                         "OTHER stem may contain bleed and does not prove source purity.",
                                         "Confidence is a bounded heuristic, not a calibrated probability.",
                                         "BASS is preserved as independent contextual evidence, not forced into the chord label.",
                                         "No human chord verification."],
                            status="INFERRED" if chord_events else "INSUFFICIENT_EVIDENCE")
    output_path.write_text(phrase.model_dump_json(indent=2), encoding="utf-8")
    return phrase


def compare_capture(phrase_path: Path, manifest_path: Path, capture_path: Path, output_dir: Path) -> dict:
    """Factual A/B for the same region; no timbral quality score."""
    phrase = HarmonicPhrase.model_validate_json(phrase_path.read_text(encoding="utf-8"))
    if phrase.status != "INFERRED" or not phrase.chord_events:
        raise RuntimeError("NO_SUPPORTED_CHORD_EVENT")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = {row["role"]: row["manifest"] for row in manifest["records"]}
    captured, sr = sf.read(str(capture_path), always_2d=True, dtype="float32")
    expected_frames = int(round((phrase.source_end_s - phrase.source_start_s) * sr))
    if len(captured) != expected_frames or not np.isfinite(captured).all():
        raise RuntimeError("CAPTURE_SPAN_OR_FINITE_MISMATCH")
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    source_chroma = np.zeros(12)
    for role in ("PIANO", "OTHER"):
        record = records[role]
        path = Path(record["immutable_path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != phrase.source_refs[role]:
            raise RuntimeError(f"SOURCE_HASH_MISMATCH: {role}")
        with sf.SoundFile(str(path)) as reader:
            if reader.samplerate != sr:
                raise RuntimeError("SOURCE_RATE_MISMATCH")
            reader.seek(int(round(phrase.source_start_s * sr)))
            audio = reader.read(expected_frames, dtype="float32", always_2d=True)
        if len(audio) != expected_frames:
            raise RuntimeError("SOURCE_SPAN_MISMATCH")
        out = output_dir / f"harmony_source_{role.lower()}.wav"
        if out.exists():
            raise FileExistsError(out)
        sf.write(str(out), audio, sr, subtype="FLOAT")
        files[role.lower()] = str(out)
        chroma, _, _ = _read_chroma(out)
        vector = chroma.mean(axis=0) if len(chroma) else np.zeros(12)
        if vector.sum():
            vector /= vector.sum()
        source_chroma += (.6 if role == "PIANO" else .4) * vector
    review = output_dir / "harmony_v1.wav"
    raw = output_dir / "harmony_v1_raw.wav"
    report_path = output_dir / "harmony_ab_v1.json"
    for path in (review, raw, report_path):
        if path.exists():
            raise FileExistsError(path)
    shutil.copy2(capture_path, raw)
    centered = np.repeat(captured[:, :1], 2, axis=1) if np.sqrt(np.mean(captured[:, 1] ** 2)) < .01 * np.sqrt(np.mean(captured[:, 0] ** 2)) else captured
    sf.write(str(review), centered, sr, subtype="FLOAT")
    capture_chroma, _, _ = _read_chroma(review)
    captured_vector = capture_chroma.mean(axis=0) if len(capture_chroma) else np.zeros(12)
    if captured_vector.sum():
        captured_vector /= captured_vector.sum()
    expected = {pc for event in phrase.chord_events for pc in event.pitch_classes}
    indices = [PITCH_CLASSES.index(pc) for pc in expected]
    report = {
        "phrase": str(phrase_path), "capture": str(capture_path),
        "source_region": [phrase.source_start_s, phrase.source_end_s],
        "chord_events": [event.model_dump() for event in phrase.chord_events],
        "harmonic_change_points_qn": [event.start_qn for event in phrase.chord_events[1:]],
        "expected_pitch_classes": sorted(expected),
        "source_chord_class_fraction": float(source_chroma[indices].sum()),
        "capture_chord_class_fraction": float(captured_vector[indices].sum()),
        "source_top_classes": [PITCH_CLASSES[int(i)] for i in np.argsort(source_chroma)[::-1][:6]],
        "capture_top_classes": [PITCH_CLASSES[int(i)] for i in np.argsort(captured_vector)[::-1][:6]],
        "capture_duration_s": len(captured) / sr,
        "capture_rms_left": float(np.sqrt(np.mean(captured[:, 0] ** 2))),
        "capture_peak_left": float(np.max(np.abs(captured[:, 0]))),
        "files": {**files, "reconstruction": str(review), "raw": str(raw)},
        "limitations": ["Pitch-class overlap is not chord correctness or timbral similarity.",
                        "OTHER stem may contain bleed.",
                        "Capture reported limited temporal alignment; no sample-accurate change claim.",
                        "Source and reconstruction are not loudness matched."],
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start-s", type=float, required=True)
    parser.add_argument("--bars", type=int, default=4)
    args = parser.parse_args()
    phrase = infer_phrase(args.manifest, args.output, start_s=args.start_s, bars=args.bars)
    print(json.dumps({"status": phrase.status, "region": [phrase.source_start_s, phrase.source_end_s],
                      "chord_events": [event.model_dump() for event in phrase.chord_events],
                      "window_status": [window["status"] for window in phrase.windows]}, indent=2))
