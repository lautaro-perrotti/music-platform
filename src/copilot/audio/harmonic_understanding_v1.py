"""Deterministic harmonic understanding from authoritative bass + OTHER audio.

The output is evidence, not a chord transcription claim.  The bass source is
the reconciled Ableton MIDI artifact; the optional OTHER stem contributes a
local chroma measurement.  Stem separation purity is retained as a limitation
and never silently promoted to ground truth.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.schemas.bass_musical_model import BassMusicalModel
from copilot.schemas.harmonic_understanding import (
    BassHarmonyRelationship,
    ChordHypothesis,
    HarmonicRhythmSegment,
    HarmonicUnderstanding,
    HarmonicWindow,
)
from copilot.schemas.musical_understanding import (
    BassPitchEvent,
    MusicalUnderstanding,
    TonalityHypothesis,
)

# Keep the vocabulary explicit and deterministic.  No model or external
# harmony library is involved in selecting these candidates.
PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MODE_INTERVALS = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
}
CHORD_INTERVALS = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "diminished": (0, 3, 6),
    "dominant7": (0, 4, 7, 10),
    "minor7": (0, 3, 7, 10),
    "major7": (0, 4, 7, 11),
}
_PC_TO_INDEX = {name: index for index, name in enumerate(PITCH_CLASSES)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, model: type[Any]) -> Any:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _read_chroma(path: Path, *, frame_size: int = 4096, hop: int = 2048) -> tuple[np.ndarray, np.ndarray, int]:
    data, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    mono = np.mean(np.asarray(data, dtype=np.float64), axis=1)
    if len(mono) < frame_size:
        return np.zeros((0, 12), dtype=np.float64), np.zeros(0, dtype=np.float64), int(sample_rate)
    window = np.hanning(frame_size)
    frequencies = np.fft.rfftfreq(frame_size, 1.0 / sample_rate)
    midi = 69.0 + 12.0 * np.log2(np.maximum(frequencies, 1e-9) / 440.0)
    valid = (frequencies >= 55.0) & (frequencies <= 1400.0) & np.isfinite(midi)
    pitch_indices = np.mod(np.rint(midi[valid]).astype(int), 12)
    frames: list[np.ndarray] = []
    times: list[float] = []
    for start in range(0, len(mono) - frame_size + 1, hop):
        spectrum = np.abs(np.fft.rfft(mono[start : start + frame_size] * window)) ** 2
        vector = np.zeros(12, dtype=np.float64)
        for index, pitch_class in enumerate(pitch_indices):
            vector[pitch_class] += float(spectrum[valid][index])
        total = float(vector.sum())
        if total > 0:
            vector /= total
        frames.append(vector)
        times.append((start + frame_size / 2.0) / sample_rate)
    return np.asarray(frames), np.asarray(times), int(sample_rate)


def _event_window(event: BassPitchEvent, windows: list[HarmonicWindow]) -> HarmonicWindow | None:
    qn = float(event.onset_qn if event.onset_qn is not None else event.grid.onset_qn)
    return next((window for window in windows if window.start_qn <= qn < window.end_qn), None)


def _bass_weights(events: list[BassPitchEvent], window: HarmonicWindow) -> np.ndarray:
    weights = np.zeros(12, dtype=np.float64)
    for event in events:
        if not event.pitch_class:
            continue
        qn = float(event.onset_qn if event.onset_qn is not None else event.grid.onset_qn)
        if window.start_qn <= qn < window.end_qn:
            duration = float(event.duration_qn or 0.25)
            weights[_PC_TO_INDEX[event.pitch_class]] += duration * max(0.1, float(event.confidence))
    total = float(weights.sum())
    return weights / total if total else weights


def _pitch_names(values: np.ndarray, *, threshold: float = 0.08) -> list[str]:
    return [PITCH_CLASSES[index] for index, value in enumerate(values) if float(value) >= threshold]


def _chord_candidates(weights: np.ndarray, evidence: str) -> list[ChordHypothesis]:
    active = int(np.count_nonzero(weights >= 0.08))
    if not active:
        return []
    rows: list[ChordHypothesis] = []
    for root_index, root in enumerate(PITCH_CLASSES):
        for quality, intervals in CHORD_INTERVALS.items():
            indices = [(root_index + interval) % 12 for interval in intervals]
            in_energy = float(weights[indices].sum())
            active_in = sum(float(weights[index]) >= 0.08 for index in indices)
            completeness = min(1.0, active_in / 3.0)
            off_energy = float(weights[[index for index in range(12) if index not in indices]].sum())
            complexity_penalty = 0.025 * max(0, len(intervals) - 3)
            score = float(np.clip(0.70 * in_energy + 0.25 * completeness + 0.05 * (1.0 - off_energy) - complexity_penalty, 0.0, 1.0))
            suffix = {"major": "", "minor": "m", "diminished": "dim", "sus2": "sus2", "sus4": "sus4", "dominant7": "7", "minor7": "m7", "major7": "maj7"}[quality]
            label = f"{root}{suffix}"
            rows.append(
                ChordHypothesis(
                    root=root,
                    quality=quality,
                    label=label,
                    pitch_classes=[PITCH_CLASSES[index] for index in indices],
                    score=score,
                    confidence=score,
                    evidence_refs=[evidence],
                )
            )
    return sorted(rows, key=lambda item: (item.score, item.label), reverse=True)


def _select_chord(candidates: list[ChordHypothesis], *, active_count: int) -> ChordHypothesis | None:
    if not candidates or active_count < 2:
        return None
    best = candidates[0]
    second = candidates[1].score if len(candidates) > 1 else 0.0
    # A simpler chord can sit only a few hundredths above an extension that
    # merely reuses the same observed pitch classes.  Keep the margin strict
    # enough for genuinely competing roots, but do not turn an unobserved
    # seventh into a false ambiguity.
    if best.score < 0.62 or best.score - second < 0.02:
        return None
    return best.model_copy(update={"confidence": float(np.clip(best.score * (0.65 + 0.35 * (best.score - second) / 0.2), 0.0, 1.0))})


def _tonality_candidates(weights: np.ndarray, evidence: str) -> list[TonalityHypothesis]:
    rows: list[TonalityHypothesis] = []
    for tonic_index, tonic in enumerate(PITCH_CLASSES):
        for mode, intervals in MODE_INTERVALS.items():
            indices = [(tonic_index + interval) % 12 for interval in intervals]
            in_energy = float(weights[indices].sum())
            out_energy = float(weights[[index for index in range(12) if index not in indices]].sum())
            tonic_support = float(weights[tonic_index])
            score = float(np.clip(0.68 * in_energy + 0.20 * (1.0 - out_energy) + 0.12 * tonic_support, 0.0, 1.0))
            rows.append(TonalityHypothesis(tonic=tonic, mode=mode, scale=[PITCH_CLASSES[index] for index in indices], score=score, confidence=score, evidence_refs=[evidence]))
    return sorted(rows, key=lambda item: (item.score, item.tonic, item.mode), reverse=True)


def _select_tonality(candidates: list[TonalityHypothesis]) -> TonalityHypothesis | None:
    if not candidates:
        return None
    best = candidates[0]
    second = candidates[1].score if len(candidates) > 1 else 0.0
    if best.score < 0.70 or best.score - second < 0.05:
        return None
    return best.model_copy(update={"confidence": float(np.clip(best.score, 0.0, 1.0))})


def _windows(end_qn: float, *, bar_width: int = 4) -> list[HarmonicWindow]:
    rows: list[HarmonicWindow] = []
    width_qn = bar_width * 4.0
    count = max(1, int(math.ceil(end_qn / width_qn)))
    for index in range(count):
        start_qn = index * width_qn
        finish_qn = min(end_qn, (index + 1) * width_qn)
        rows.append(HarmonicWindow(window_id=f"harmonic_window_{index + 1:02d}", start_bar=start_qn / 4.0 + 1.0, end_bar=finish_qn / 4.0 + 1.0, start_qn=start_qn, end_qn=finish_qn))
    return rows


def build_harmonic_understanding(
    bass_model_path: Path | str,
    understanding_path: Path | str,
    *,
    other_stem_path: Path | str | None = None,
    output_path: Path | str | None = None,
) -> HarmonicUnderstanding:
    bass_model_path = Path(bass_model_path)
    understanding_path = Path(understanding_path)
    bass_model = _load_json(bass_model_path, BassMusicalModel)
    understanding = _load_json(understanding_path, MusicalUnderstanding)
    events = [event for event in understanding.bass.pitch_events if event.status == "RELIABLE" and event.pitch_class]
    end_qn = max(
        float(understanding.timeline.get("windows_reused", [{}])[-1].get("end_qn", 0.0) or 0.0),
        max((float(event.offset_qn or event.onset_qn or 0.0) for event in events), default=0.0),
    )
    windows = _windows(end_qn)
    chroma = np.zeros((0, 12), dtype=np.float64)
    times = np.zeros(0, dtype=np.float64)
    other_path = Path(other_stem_path) if other_stem_path is not None else None
    if other_path is not None and other_path.is_file():
        chroma, times, _ = _read_chroma(other_path)

    source_kind = "AUTHORITATIVE_BASS_ONLY"
    if len(chroma):
        source_kind = "OTHER_STEM_CHROMA_PLUS_AUTHORITATIVE_BASS"
    all_weights = np.zeros(12, dtype=np.float64)
    for window in windows:
        bass_weights = _bass_weights(events, window)
        start_s = window.start_qn * 60.0 / understanding.tempo_bpm
        end_s = window.end_qn * 60.0 / understanding.tempo_bpm
        frame_mask = (times >= start_s) & (times < end_s)
        audio_weights = np.mean(chroma[frame_mask], axis=0) if np.any(frame_mask) else np.zeros(12, dtype=np.float64)
        audio_total = float(audio_weights.sum())
        if audio_total:
            audio_weights /= audio_total
        if audio_total and bass_weights.sum():
            weights = 0.75 * audio_weights + 0.25 * bass_weights
        elif audio_total:
            weights = audio_weights
        else:
            weights = bass_weights
        total = float(weights.sum())
        if total:
            weights /= total
        all_weights += weights
        evidence = f"{understanding.reference_id}:{window.window_id}"
        candidates = _chord_candidates(weights, evidence)
        selected = _select_chord(candidates, active_count=int(np.count_nonzero(weights >= 0.08))) if audio_total else None
        limitations = ["CHORDS_ARE_RANKED_TEMPLATE_HYPOTHESES_NOT_GROUND_TRUTH"]
        if not audio_total:
            limitations.append("NO_AUTHORITATIVE_HARMONIC_AUDIO_IN_WINDOW")
        limitations.append("OTHER_STEM_SEPARATION_DOES_NOT_PROVE_SOURCE_PURITY") if audio_total else None
        window.pitch_class_energy = {PITCH_CLASSES[index]: round(float(value), 8) for index, value in enumerate(weights) if value > 0}
        window.bass_pitch_classes = sorted({event.pitch_class for event in events if event.pitch_class and window.start_qn <= float(event.onset_qn or event.grid.onset_qn) < window.end_qn})
        window.hypotheses = candidates[:8]
        window.selected = selected
        window.status = "SUPPORTED" if selected else "INSUFFICIENT_EVIDENCE"
        window.evidence_refs = [evidence]
        window.limitations = limitations

    if float(all_weights.sum()):
        all_weights /= float(all_weights.sum())
    tonal_candidates = _tonality_candidates(all_weights, f"{understanding.reference_id}:tonality")
    selected_tonality = _select_tonality(tonal_candidates) if len(chroma) else None

    rhythm: list[HarmonicRhythmSegment] = []
    for window in windows:
        label = window.selected.label if window.selected else "UNKNOWN"
        confidence = window.selected.confidence if window.selected else 0.0
        if rhythm and rhythm[-1].label == label and rhythm[-1].end_bar == window.start_bar:
            rhythm[-1] = rhythm[-1].model_copy(update={"end_bar": window.end_bar, "confidence": min(rhythm[-1].confidence, confidence), "evidence_refs": rhythm[-1].evidence_refs + window.evidence_refs})
        else:
            rhythm.append(HarmonicRhythmSegment(start_bar=window.start_bar, end_bar=window.end_bar, label=label, confidence=confidence, evidence_refs=list(window.evidence_refs), limitations=list(window.limitations)))

    relationships: list[BassHarmonyRelationship] = []
    for event in events:
        window = _event_window(event, windows)
        if window is None or not event.pitch_class:
            continue
        if window.selected is None:
            role = "UNKNOWN"
            label = "UNKNOWN"
            confidence = 0.0
        else:
            label = window.selected.label
            pitch_index = _PC_TO_INDEX[event.pitch_class]
            chord_indices = {_PC_TO_INDEX[pc] for pc in window.selected.pitch_classes}
            relative = (pitch_index - _PC_TO_INDEX[window.selected.root]) % 12
            role = "ROOT" if relative == 0 else "THIRD" if relative in {3, 4} else "FIFTH" if relative == 7 else "EXTENSION" if pitch_index in chord_indices else "NON_CHORD_TONE"
            confidence = window.selected.confidence
        relationships.append(BassHarmonyRelationship(event_id=event.event_id, bass_pitch_class=event.pitch_class, window_id=window.window_id, chord_label=label, role=role, confidence=confidence, evidence_refs=[f"{understanding.reference_id}:{event.event_id}"]))

    limitations = [
        "CHORD_AND_TONAL_LABELS_ARE_DETERMINISTIC_HYPOTHESES_NOT_MEASUREMENTS",
        "NO_LLM_OR_API_CALLS",
        "NO_ABLETON_ACCESS",
        "MUSICAL_WRITES_0",
    ]
    if other_path is None:
        limitations.append("HARMONIC_AUDIO_SOURCE_NOT_PROVIDED")
    else:
        limitations.extend(["OTHER_STEM_SEPARATION_DOES_NOT_PROVE_SOURCE_PURITY", "HUMAN_STEM_QUALITY_REVIEW_REMAINS_SEPARATE"])
    if selected_tonality is None:
        limitations.append("TONALITY_NOT_COLLAPSED_TO_SINGLE_HYPOTHESIS")
    model = HarmonicUnderstanding(
        reference_id=understanding.reference_id,
        source_analysis_id=understanding.source_analysis_id,
        tempo_bpm=understanding.tempo_bpm,
        source_kind=source_kind,
        windows=windows,
        harmonic_rhythm=rhythm,
        tonal_hypotheses=tonal_candidates[:12],
        selected_tonality=selected_tonality,
        bass_harmony_relationships=relationships,
        limitations=limitations,
        provenance={
            "bass_model_path": str(bass_model_path),
            "bass_model_sha256": _sha256(bass_model_path),
            "understanding_path": str(understanding_path),
            "understanding_sha256": _sha256(understanding_path),
            "other_stem_path": str(other_path) if other_path else None,
            "other_stem_sha256": _sha256(other_path) if other_path and other_path.is_file() else None,
            "bass_model_event_count": bass_model.event_count,
            "analyzer_id": "harmonic-understanding-deterministic-v1",
            "model_api_calls": 0,
            "musical_writes": 0,
        },
    )
    if output_path is not None:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return model


def render_harmonic_understanding_report(model: HarmonicUnderstanding) -> str:
    tonality = model.selected_tonality
    lines = [
        "HARMONIC_UNDERSTANDING_V1",
        f"reference_id: {model.reference_id}",
        f"source_kind: {model.source_kind}",
        f"windows_supported: {sum(item.selected is not None for item in model.windows)}/{len(model.windows)}",
        f"harmonic_rhythm: {[(item.start_bar, item.end_bar, item.label) for item in model.harmonic_rhythm]}",
        f"tonality: {f'{tonality.tonic} {tonality.mode}' if tonality else 'INSUFFICIENT_EVIDENCE'}",
        f"bass_harmony_relationships: {len(model.bass_harmony_relationships)}",
        "MODEL/API CALLS: 0",
        "MUSICAL WRITES: 0",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["build_harmonic_understanding", "render_harmonic_understanding_report"]
