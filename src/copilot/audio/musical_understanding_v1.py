"""Deterministic BASS + DRUMS understanding over cached separated stems.

This module measures musical structure; it does not diagnose, plan, call a
model, or mutate Ableton.  Every inferred field remains ranked/qualified and
can degrade to ``INSUFFICIENT_EVIDENCE``.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from copilot.audio.lowend import detect_transients
from copilot.music_source.stem_reference import StemReferenceAnalysis
from copilot.schemas.musical_understanding import (
    BassDrumsRelationship,
    BassPitchEvent,
    BassUnderstanding,
    DrumTransientEvent,
    DrumsUnderstanding,
    IntervalEvidence,
    MotifPhraseEvidence,
    MusicalGridPoint,
    MusicalUnderstanding,
    PeriodicityCandidate,
    PulseStructure,
    RhythmicStructure,
    TonalityHypothesis,
)

PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MODE_INTERVALS = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
}
GRID_STEPS = ((1.0, "quarter"), (0.5, "eighth"), (0.25, "sixteenth"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _grid_point(onset_s: float, tempo_bpm: float, *, evidence: str) -> MusicalGridPoint:
    qn = max(0.0, onset_s * tempo_bpm / 60.0)
    best_step, best_name = min(GRID_STEPS, key=lambda item: abs(qn / item[0] - round(qn / item[0])))
    nearest = round(qn / best_step) * best_step
    deviation = qn - nearest
    beat_in_bar = qn % 4.0
    return MusicalGridPoint(
        onset_s=float(onset_s),
        onset_qn=float(qn),
        bar=float(math.floor(qn / 4.0) + 1),
        beat_in_bar=float(math.floor(beat_in_bar) + 1.0),
        subdivision=best_name,
        nearest_grid_qn=float(nearest),
        deviation_qn=float(deviation),
        deviation_ms=float(deviation * 60.0 / tempo_bpm * 1000.0),
        evidence_refs=[evidence],
    )


def _load_mono(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(path, always_2d=True, dtype="float32")
    return np.mean(np.asarray(data, dtype=np.float64), axis=1), int(sr)


def _optional_pyin() -> tuple[Any | None, str | None]:
    try:
        import librosa  # type: ignore

        return librosa, None
    except Exception as exc:  # pragma: no cover - environment dependent
        return None, f"PITCH_PROVIDER_UNAVAILABLE:{type(exc).__name__}"


def _pitch_events(path: Path, tempo_bpm: float, evidence_prefix: str) -> tuple[list[BassPitchEvent], list[str], dict[str, float]]:
    librosa, unavailable = _optional_pyin()
    if librosa is None:
        return [], [unavailable or "PITCH_PROVIDER_UNAVAILABLE"], {}
    try:
        y, sr = librosa.load(path, sr=None, mono=True)
        if len(y) < 2048 or not np.any(np.abs(y) > 1e-7):
            return [], ["BASS_SIGNAL_INSUFFICIENT"], {}
        hop = 256
        f0, voiced, probability = librosa.pyin(
            y,
            fmin=float(librosa.note_to_hz("C1")),
            fmax=float(librosa.note_to_hz("C5")),
            sr=sr,
            frame_length=4096,
            hop_length=hop,
            fill_na=np.nan,
        )
        times = librosa.times_like(f0, sr=sr, hop_length=hop)
        probability = np.asarray(probability, dtype=np.float64)
        f0 = np.asarray(f0, dtype=np.float64)
        voiced = np.asarray(voiced, dtype=bool)
        valid = voiced & np.isfinite(f0) & (probability >= 0.62)
        events: list[BassPitchEvent] = []
        i = 0
        while i < len(valid):
            if not valid[i]:
                i += 1
                continue
            j = i + 1
            while j < len(valid) and valid[j]:
                j += 1
            start = float(max(0.0, times[i] - hop / (2.0 * sr)))
            end = float(min(len(y) / sr, times[j - 1] + hop / (2.0 * sr)))
            if end - start >= 0.06:
                values = f0[i:j]
                midi_values = 69.0 + 12.0 * np.log2(values / 440.0)
                midi = float(np.median(midi_values))
                f0_hz = float(np.median(values))
                confidence = float(np.clip(np.median(probability[i:j]), 0.0, 1.0))
                unstable = float(np.std(midi_values)) > 0.5
                note = int(round(midi)) if not unstable else None
                pc = PITCH_CLASSES[note % 12] if note is not None else None
                status = "UNKNOWN" if unstable or confidence < 0.70 else "RELIABLE"
                events.append(
                    BassPitchEvent(
                        event_id=f"{evidence_prefix}:bass:{len(events):04d}",
                        grid=_grid_point(start, tempo_bpm, evidence=f"{evidence_prefix}:pitch"),
                        offset_s=max(0.001, end - start),
                        f0_hz=f0_hz,
                        midi_float=midi,
                        midi_note=note,
                        pitch_class=pc,
                        confidence=confidence,
                        status=status,
                        evidence_refs=[f"{evidence_prefix}:pyin", f"{evidence_prefix}:voicing"],
                    )
                )
            i = j
        if not events:
            return [], ["NO_STABLE_BASS_PITCH_EVENTS"], {}
        reliable = [event for event in events if event.status == "RELIABLE" and event.pitch_class]
        histogram: dict[str, float] = {pc: 0.0 for pc in PITCH_CLASSES}
        total = sum(event.offset_s * event.confidence for event in reliable)
        if total > 0:
            for event in reliable:
                histogram[event.pitch_class or "C"] += event.offset_s * event.confidence / total
        limits = []
        if any(event.status == "UNKNOWN" for event in events):
            limits.append("UNSTABLE_PITCH_FRAMES_REPORTED_AS_UNKNOWN")
        if not reliable:
            limits.append("NO_RELIABLE_BASS_PITCH_EVENTS")
        return events, limits, histogram
    except Exception as exc:  # pragma: no cover - provider/runtime dependent
        return [], [f"PITCH_ANALYSIS_FAILED:{type(exc).__name__}:{exc}"], {}


def _periodicity(onsets_qn: list[float], *, evidence: str) -> list[PeriodicityCandidate]:
    if len(onsets_qn) < 4:
        return []
    values = np.asarray(onsets_qn, dtype=np.float64)
    result: list[PeriodicityCandidate] = []
    for bars in (1, 2, 4):
        period = float(bars * 4)
        bins = np.floor(np.mod(values, period) / 0.25).astype(int)
        unique, counts = np.unique(bins, return_counts=True)
        concentration = float(np.max(counts) / len(bins)) if len(unique) else 0.0
        cycles = max(1, int(math.floor((float(np.max(values)) - float(np.min(values))) / period)))
        repeats = 0
        for cycle in range(cycles):
            a = set(np.floor(np.mod(values - float(np.min(values)), period) / 0.25).astype(int))
            b = set(np.floor(np.mod(values - float(np.min(values)) - cycle * period, period) / 0.25).astype(int))
            if a and b:
                repeats += len(a & b) / len(a | b)
        strength = float(np.clip(0.55 * concentration + 0.45 * repeats / max(1, cycles), 0.0, 1.0))
        if strength >= 0.35:
            result.append(PeriodicityCandidate(period_bars=bars, period_qn=period, strength=strength, evidence_refs=[evidence]))
    return sorted(result, key=lambda item: item.strength, reverse=True)


def _rhythm(events: list[Any], *, total_bars: float, tempo_bpm: float, evidence: str) -> RhythmicStructure:
    onsets = [float(event.grid.onset_qn) for event in events]
    iois = np.diff(np.asarray(onsets, dtype=np.float64)) if len(onsets) > 1 else np.asarray([], dtype=np.float64)
    deviations = [abs(float(event.grid.deviation_ms)) for event in events]
    offbeat = [1.0 for event in events if abs((event.grid.onset_qn % 1.0) - 0.5) < 0.125]
    return RhythmicStructure(
        event_count=len(events),
        density_per_bar=float(len(events) / max(total_bars, 1.0)),
        median_duration_qn=(
            float(np.median([float(event.offset_s) * tempo_bpm / 60.0 for event in events if hasattr(event, "offset_s")]))
            if any(hasattr(event, "offset_s") for event in events)
            else None
        ),
        ioi_qn=[float(value) for value in iois if value > 0],
        offbeat_ratio=float(len(offbeat) / len(events)) if events else None,
        median_timing_deviation_ms=float(np.median(deviations)) if deviations else None,
        periodicity_candidates=_periodicity(onsets, evidence=evidence),
        evidence_refs=[evidence],
    )


def _bass_intervals(events: list[BassPitchEvent]) -> list[IntervalEvidence]:
    reliable = [event for event in events if event.status == "RELIABLE" and event.midi_note is not None]
    result = []
    for first, second in zip(reliable, reliable[1:]):
        delta = int(second.midi_note - first.midi_note)  # type: ignore[operator]
        result.append(IntervalEvidence(
            from_event_id=first.event_id,
            to_event_id=second.event_id,
            semitones=delta,
            direction="UP" if delta > 0 else "DOWN" if delta < 0 else "SAME",
            interval_class=abs(delta) % 12 if abs(delta) % 12 <= 6 else 12 - (abs(delta) % 12),
            confidence=min(first.confidence, second.confidence),
            evidence_refs=[first.event_id, second.event_id],
        ))
    return result


def _tonality(histogram: dict[str, float], events: list[BassPitchEvent], evidence: str) -> tuple[str, list[TonalityHypothesis], TonalityHypothesis | None, list[int]]:
    reliable = [event for event in events if event.status == "RELIABLE" and event.midi_note is not None]
    if len(reliable) < 3 or len({event.pitch_class for event in reliable}) < 3:
        return "INSUFFICIENT_EVIDENCE", [], None, []
    values = np.asarray([histogram.get(pc, 0.0) for pc in PITCH_CLASSES], dtype=np.float64)
    rows: list[TonalityHypothesis] = []
    for tonic_index, tonic in enumerate(PITCH_CLASSES):
        for mode, intervals in MODE_INTERVALS.items():
            scale = [(tonic_index + step) % 12 for step in intervals]
            coverage = float(np.sum(values[scale]))
            root = float(values[tonic_index])
            score = float(np.clip(0.75 * coverage + 0.25 * root, 0.0, 1.0))
            rows.append(TonalityHypothesis(
                tonic=tonic,
                mode=mode,
                scale=[PITCH_CLASSES[index] for index in scale],
                score=score,
                confidence=0.0,
                evidence_refs=[evidence],
            ))
    rows.sort(key=lambda row: row.score, reverse=True)
    best = rows[0]
    second = rows[1].score if len(rows) > 1 else 0.0
    margin = max(0.0, best.score - second)
    confidence = float(np.clip(0.5 * best.score + 5.0 * margin, 0.0, 1.0))
    ranked = [row.model_copy(update={"confidence": confidence if row is best else max(0.0, row.score - second)}) for row in rows[:8]]
    selected = ranked[0] if best.score >= 0.60 and margin >= 0.05 else None
    status = "SUPPORTED" if selected is not None else "INSUFFICIENT_EVIDENCE"
    degrees = []
    if selected:
        lookup = {pc: idx + 1 for idx, pc in enumerate(selected.scale)}
        degrees = [lookup[event.pitch_class] for event in reliable if event.pitch_class in lookup]
    return status, ranked, selected, degrees


def _phrases(events: list[Any], total_bars: float, evidence: str) -> list[MotifPhraseEvidence]:
    result: list[MotifPhraseEvidence] = []
    signatures: list[set[tuple[int, str | None]]] = []
    for phrase_index, start in enumerate((1.0, 9.0, 17.0, 25.0)):
        # Keep the requested canonical phrase spans even when a short fixture
        # has no material in later phrases; the absence is represented by the
        # limitation rather than by an invalid zero-length span.
        end = start + 8.0
        phrase_events = [event for event in events if start <= event.grid.bar < end]
        sig = {(int(round(event.grid.onset_qn % 32.0 / 0.25)), getattr(event, "pitch_class", None)) for event in phrase_events}
        signatures.append(sig)
        similarity = None
        label = "UNKNOWN"
        limits: list[str] = []
        if phrase_index:
            first = signatures[0]
            similarity = len(first & sig) / len(first | sig) if first or sig else 1.0
            if not phrase_events:
                label = "UNKNOWN"
                limits.append("NO_EVENTS_IN_PHRASE")
            elif similarity >= 0.75:
                label = "A"
            elif similarity >= 0.45:
                label = "A_PRIME"
            elif similarity <= 0.15:
                label = "B"
            else:
                label = "UNKNOWN"
        else:
            label = "A"
        if not phrase_events:
            label = "UNKNOWN"
        if not phrase_events and "NO_EVENTS_IN_PHRASE" not in limits:
            limits.append("NO_EVENTS_IN_PHRASE")
        result.append(MotifPhraseEvidence(
            phrase_id=f"phrase_{phrase_index + 1}",
            start_bar=start,
            end_bar=end,
            structural_label=label,
            similarity_to_first=similarity,
            event_count=len(phrase_events),
            evidence_refs=[evidence],
            limitations=limits,
        ))
    return result


def _build_drums(path: Path, tempo_bpm: float, total_bars: float, evidence: str) -> tuple[DrumsUnderstanding, list[float]]:
    data, sr = _load_mono(path)
    detection = detect_transients(data, sr, min_distance_s=0.08, role="drums")
    events: list[DrumTransientEvent] = []
    for index, attack in enumerate(detection.get("attacks") or []):
        time_s = float(attack["time_s"])
        events.append(DrumTransientEvent(
            event_id=f"{evidence}:drums:{index:04d}",
            grid=_grid_point(time_s, tempo_bpm, evidence=f"{evidence}:transient"),
            strength=max(0.0, float(attack.get("strength") or 0.0)),
            evidence_refs=[f"{evidence}:rms_flux"],
        ))
    rhythm = _rhythm(events, total_bars=total_bars, tempo_bpm=tempo_bpm, evidence=evidence)
    periodicity = rhythm.periodicity_candidates
    subdivisions: dict[str, int] = {}
    for event in events:
        subdivisions[event.grid.subdivision] = subdivisions.get(event.grid.subdivision, 0) + 1
    pulse = PulseStructure(
        dominant_subdivisions=[name for name, _ in sorted(subdivisions.items(), key=lambda pair: pair[1], reverse=True)[:3]],
        periodicity_candidates=periodicity,
        offbeat_ratio=rhythm.offbeat_ratio,
        evidence_refs=[evidence],
    )
    limits = ["TRANSIENTS_ARE_UNLABELED_ENERGY_ONSETS"]
    if len(events) < 4:
        limits.append("INSUFFICIENT_DRUM_ONSETS_FOR_PATTERN_SUPPORT")
    return DrumsUnderstanding(
        status="SUPPORTED" if events else "INSUFFICIENT_EVIDENCE",
        transient_grid=events,
        pulse_structure=pulse,
        rhythmic_structure=rhythm,
        repetition=periodicity,
        local_variations=[],
        limitations=limits,
    ), [event.grid.onset_s for event in events]


def analyze_musical_understanding(
    stem_analysis_path: Path | str,
    *,
    output_path: Path | str | None = None,
    report_path: Path | str | None = None,
) -> MusicalUnderstanding:
    """Analyze the cached BASS and DRUMS artifacts without external calls/writes."""
    stem_path = Path(stem_analysis_path)
    source = StemReferenceAnalysis.model_validate_json(stem_path.read_text(encoding="utf-8"))
    bass_artifact = source.stems.get("BASS").artifact if source.stems.get("BASS") else None
    drums_artifact = source.stems.get("DRUMS").artifact if source.stems.get("DRUMS") else None
    if bass_artifact is None or drums_artifact is None:
        raise ValueError("BASS and DRUMS stem artifacts are required")
    bass_path = Path(bass_artifact.path)
    drums_path = Path(drums_artifact.path)
    if not bass_path.exists() or not drums_path.exists():
        raise FileNotFoundError("cached BASS/DRUMS artifacts are not available")
    total_bars = float(source.timeline.get("windows_reused", [{"end_qn": 0}])[0].get("end_qn", 0.0)) / 4.0
    bass_events, bass_limits, histogram = _pitch_events(bass_path, source.tempo_bpm, source.reference_id)
    bass_rhythm = _rhythm(
        bass_events,
        total_bars=total_bars,
        tempo_bpm=source.tempo_bpm,
        evidence=f"{source.reference_id}:bass",
    )
    tonality_status, tonality, selected, degrees = _tonality(histogram, bass_events, f"{source.reference_id}:tonality")
    if tonality_status == "INSUFFICIENT_EVIDENCE":
        bass_limits.append("TONALITY_NOT_COLLAPSED_TO_A_SINGLE_KEY")
    bass_phrases = _phrases(bass_events, total_bars, f"{source.reference_id}:bass_phrase")
    drums, drum_times = _build_drums(drums_path, source.tempo_bpm, total_bars, source.reference_id)
    bass_times = [event.grid.onset_s for event in bass_events]
    coincidence: list[float] = []
    beat_s = 60.0 / source.tempo_bpm
    for bass_time in bass_times:
        if not drum_times:
            continue
        nearest = min(drum_times, key=lambda drum_time: abs(drum_time - bass_time))
        delta_ms = (nearest - bass_time) * 1000.0
        if abs(delta_ms) <= min(50.0, beat_s * 0.125 * 1000.0):
            coincidence.append(delta_ms)
    threshold = len(bass_times)
    relationship = BassDrumsRelationship(
        bass_event_count=len(bass_times),
        drum_event_count=len(drum_times),
        coincidence_count=len(coincidence),
        coincidence_ratio=float(len(coincidence) / threshold) if threshold else 0.0,
        displacement_ms=[float(value) for value in coincidence],
        median_displacement_ms=float(np.median(coincidence)) if coincidence else None,
        relationship_status="DESCRIPTIVE_ONLY",
        evidence_refs=[f"{source.reference_id}:bass", f"{source.reference_id}:drums"],
        limitations=["COINCIDENCE_IS_NOT_A_CAUSAL_OR_AESTHETIC_JUDGMENT"],
    )
    bass = BassUnderstanding(
        status="SUPPORTED" if any(event.status == "RELIABLE" for event in bass_events) else "INSUFFICIENT_EVIDENCE",
        pitch_events=bass_events,
        pitch_classes=histogram,
        tonality_status=tonality_status,
        tonality=tonality,
        selected_tonality=selected,
        scale_degrees=degrees,
        intervals=_bass_intervals(bass_events),
        rhythmic_structure=bass_rhythm,
        motifs=bass_phrases,
        phrase_structure=bass_phrases,
        limitations=bass_limits,
    )
    try:
        librosa_version = importlib.metadata.version("librosa")
    except importlib.metadata.PackageNotFoundError:
        librosa_version = None
    result = MusicalUnderstanding(
        reference_id=source.reference_id,
        source_analysis_id=source.source_analysis_id,
        stem_analysis_id=source.provenance.get("analysis_id", "stem_reference_analysis_v1"),
        tempo_bpm=source.tempo_bpm,
        timeline=source.timeline,
        bass=bass,
        drums=drums,
        relationships={"bass_drums": relationship},
        global_limitations=[
            "MEASUREMENTS_ONLY_NO_AESTHETIC_JUDGMENT",
            "DRUMS_ARE_UNLABELED_TRANSIENTS",
            "SEPARATION_RECONSTRUCTION_DOES_NOT_PROVE_STEM_PURITY",
        ],
        provenance={
            "source_stem_analysis": str(stem_path),
            "bass_path": str(bass_path),
            "drums_path": str(drums_path),
            "bass_sha256": _sha256(bass_path),
            "drums_sha256": _sha256(drums_path),
            "pitch_provider": "librosa.pyin" if librosa_version else "unavailable",
            "librosa_version": librosa_version,
            "model_api_calls": 0,
            "musical_writes": 0,
            "analyzer_id": "musical-understanding-deterministic-v1",
        },
    )
    if output_path is not None:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    if report_path is not None:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_musical_understanding_report(result), encoding="utf-8")
    return result


def render_musical_understanding_report(result: MusicalUnderstanding) -> str:
    selected = result.bass.selected_tonality
    key_text = f"{selected.tonic} {selected.mode}" if selected else "INSUFFICIENT_EVIDENCE"
    lines = [
        "MUSICAL_UNDERSTANDING_V1",
        f"reference_id: {result.reference_id}",
        f"tempo_bpm: {result.tempo_bpm:g}",
        f"timeline: {result.timeline.get('windows_reused', [])}",
        "",
        "BASS",
        f"status: {result.bass.status}",
        f"pitch_events: {len(result.bass.pitch_events)} reliable: {sum(e.status == 'RELIABLE' for e in result.bass.pitch_events)}",
        f"tonality: {result.bass.tonality_status} ({key_text})",
        f"intervals: {len(result.bass.intervals)}",
        f"rhythm_events: {result.bass.rhythmic_structure.event_count}",
        f"phrases: {[(p.structural_label, p.event_count) for p in result.bass.phrase_structure]}",
        "",
        "DRUMS",
        f"status: {result.drums.status}",
        f"transients: {len(result.drums.transient_grid)}",
        f"dominant_subdivisions: {result.drums.pulse_structure.dominant_subdivisions}",
        f"periodicity: {[(p.period_bars, round(p.strength, 3)) for p in result.drums.repetition]}",
        "",
        "BASS_DRUMS",
        f"coincidence_ratio: {result.relationships['bass_drums'].coincidence_ratio:.3f}",
        f"median_displacement_ms: {result.relationships['bass_drums'].median_displacement_ms}",
        "",
        "SAFETY",
        "MODEL/API CALLS: 0",
        "MUSICAL_WRITES: 0",
        f"provenance: {result.provenance['analyzer_id']}",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["analyze_musical_understanding", "render_musical_understanding_report"]
