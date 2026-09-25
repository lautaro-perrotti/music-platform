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
# pYIN's voiced-probability is retained as evidence, not treated as a
# categorical truth.  0.50 is the minimum majority-confidence boundary;
# voiced_fraction is an independent guard against a single-frame estimate.
AUDIO_RELIABLE_PITCH_CONFIDENCE = 0.50
AUDIO_RELIABLE_VOICED_FRACTION = 0.25


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


def _pyin_track(path: Path) -> tuple[Any, np.ndarray, int, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int, str | None]:
    librosa, unavailable = _optional_pyin()
    if librosa is None:
        return None, np.asarray([]), 0, np.asarray([]), np.asarray([]), np.asarray([]), np.asarray([]), 0, unavailable or "PITCH_PROVIDER_UNAVAILABLE"
    y, sr = librosa.load(path, sr=None, mono=True)
    if len(y) < 2048 or not np.any(np.abs(y) > 1e-7):
        return librosa, y, sr, np.asarray([]), np.asarray([]), np.asarray([]), np.asarray([]), 0, "BASS_SIGNAL_INSUFFICIENT"
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
    return (
        librosa,
        y,
        sr,
        np.asarray(times, dtype=np.float64),
        np.asarray(f0, dtype=np.float64),
        np.asarray(voiced, dtype=bool),
        np.asarray(probability, dtype=np.float64),
        hop,
        None,
    )


def _pitch_events(path: Path, tempo_bpm: float, evidence_prefix: str) -> tuple[list[BassPitchEvent], list[str], dict[str, float], dict[str, Any]]:
    try:
        librosa, y, sr, times, f0, voiced, probability, hop, initial_error = _pyin_track(path)
        if initial_error:
            return [], [initial_error], {}, {"status": initial_error}
        old_valid = voiced & np.isfinite(f0) & (probability >= 0.62)
        old_runs = 0
        old_duration_passes = 0
        i = 0
        while i < len(old_valid):
            if not old_valid[i]:
                i += 1
                continue
            old_runs += 1
            run_start = i
            i += 1
            while i < len(old_valid) and old_valid[i]:
                i += 1
            run_start_s = max(0.0, float(times[run_start] - hop / (2.0 * sr)))
            run_end_s = min(float(len(y) / sr), float(times[i - 1] + hop / (2.0 * sr)))
            if run_end_s - run_start_s >= 0.06:
                old_duration_passes += 1
        envelope = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=envelope,
            sr=sr,
            hop_length=hop,
            units="frames",
            backtrack=False,
            delta=0.20,
            wait=max(1, int(round(0.12 * sr / hop))),
        )
        onset_times = [float(times[min(int(frame), len(times) - 1)]) for frame in onset_frames]
        events: list[BassPitchEvent] = []
        low_confidence_regions = 0
        unstable_regions = 0
        for index, onset in enumerate(onset_times):
            next_onset = onset_times[index + 1] if index + 1 < len(onset_times) else onset + 0.75
            region_start = min(float(len(y) / sr), onset + 0.025)
            region_end = min(float(len(y) / sr), max(region_start + 0.04, next_onset - 0.025), onset + 0.75)
            region = (times >= region_start) & (times < region_end)
            voiced_region = region & voiced & np.isfinite(f0)
            stable_region = voiced_region & (probability >= 0.40)
            if int(np.count_nonzero(stable_region)) < 2:
                continue
            values = f0[stable_region]
            midi_values = 69.0 + 12.0 * np.log2(values / 440.0)
            midi = float(np.median(midi_values))
            f0_hz = float(np.median(values))
            confidence = float(np.clip(np.median(probability[stable_region]), 0.0, 1.0))
            voiced_fraction = float(np.count_nonzero(stable_region) / max(1, np.count_nonzero(region)))
            unstable = float(np.std(midi_values)) > 0.5
            note = int(round(midi)) if not unstable else None
            pc = PITCH_CLASSES[note % 12] if note is not None else None
            if unstable:
                unstable_regions += 1
            if confidence < AUDIO_RELIABLE_PITCH_CONFIDENCE or voiced_fraction < AUDIO_RELIABLE_VOICED_FRACTION:
                low_confidence_regions += 1
            status = "UNKNOWN" if unstable or confidence < AUDIO_RELIABLE_PITCH_CONFIDENCE or voiced_fraction < AUDIO_RELIABLE_VOICED_FRACTION else "RELIABLE"
            onset_qn = onset * tempo_bpm / 60.0
            offset_qn = region_end * tempo_bpm / 60.0
            events.append(
                BassPitchEvent(
                    event_id=f"{evidence_prefix}:bass:{len(events):04d}",
                    grid=_grid_point(onset, tempo_bpm, evidence=f"{evidence_prefix}:onset"),
                    offset_s=max(0.001, region_end - onset),
                    f0_hz=f0_hz,
                    midi_float=midi,
                    midi_note=note,
                    pitch_class=pc,
                    confidence=confidence,
                    status=status,
                    onset_qn=float(onset_qn),
                    offset_qn=float(offset_qn),
                    duration_qn=float(max(0.0, offset_qn - onset_qn)),
                    source_kind="AUDIO_PYIN_ONSET_CONDITIONED",
                    voiced_fraction=voiced_fraction,
                    evidence_refs=[f"{evidence_prefix}:onset", f"{evidence_prefix}:pyin", f"{evidence_prefix}:voicing"],
                )
            )
        if not events:
            return [], ["NO_ONSET_CONDITIONED_BASS_PITCH_EVENTS"], {}, {
                "pyin_voiced_runs_total": old_runs,
                "onset_candidates": len(onset_times),
            }
        reliable = [event for event in events if event.status == "RELIABLE" and event.pitch_class]
        histogram: dict[str, float] = {pc: 0.0 for pc in PITCH_CLASSES}
        total = sum((event.duration_qn or 0.0) * event.confidence for event in reliable)
        if total > 0:
            for event in reliable:
                histogram[event.pitch_class or "C"] += (event.duration_qn or 0.0) * event.confidence / total
        limits = []
        if any(event.status == "UNKNOWN" for event in events):
            limits.append("UNSTABLE_OR_LOW_CONFIDENCE_ONSET_REGIONS_REPORTED_AS_UNKNOWN")
        if not reliable:
            limits.append("NO_RELIABLE_BASS_PITCH_EVENTS")
        diagnostics = {
            "old_contiguous_pyin_runs": old_runs,
            "old_runs_that_met_060s_duration_gate": old_duration_passes,
            "onset_candidates": len(onset_times),
            "onset_regions_with_pitch": len(events),
            "low_confidence_regions": low_confidence_regions,
            "unstable_regions": unstable_regions,
            "segmentation_change": "ONSET_CONDITIONED_REGIONS",
            "onset_parameters": {"delta": 0.20, "minimum_separation_s": 0.12, "attack_exclusion_s": 0.025},
            "diagnosis": "Previous continuous-run segmentation discarded short pYIN runs before note onsets could be represented; onset-conditioned regions now preserve them as distinct UNKNOWN or reliable candidates.",
        }
        return events, limits, histogram, diagnostics
    except Exception as exc:  # pragma: no cover - provider/runtime dependent
        return [], [f"PITCH_ANALYSIS_FAILED:{type(exc).__name__}:{exc}"], {}, {"status": "FAILED"}


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


def _midi_notes(
    midi_pack_path: Path | None,
    *,
    tempo_bpm: float,
    evidence_prefix: str,
) -> tuple[list[BassPitchEvent], list[str], dict[str, Any]]:
    """Read exact notes only when the persisted ALS identity reconciles."""
    if midi_pack_path is None:
        return [], ["MIDI_SOURCE_NOT_PROVIDED"], {"status": "NOT_PROVIDED"}
    try:
        from copilot.audio.midi_read_only_v1 import (
            _load_als_root,
            _parent_map,
            _als_device_inventory,
            _local,
            _track_locator_name,
            TRACK_TAGS,
            identity_for_als_path,
            match_als_track,
            read_arrangement_midi,
        )
        from copilot.daw.object_ref import PersistentObjectRef

        pack = json.loads(midi_pack_path.read_text(encoding="utf-8"))
        source = pack.get("source_ref") or {}
        als_path = Path(str(source.get("project_path") or ""))
        expected_identity = str(source.get("project_identity") or "")
        diagnostics: dict[str, Any] = {
            "pack_path": str(midi_pack_path),
            "als_path": str(als_path),
            "expected_project_identity": expected_identity,
            "track_name_locator": source.get("track_name"),
            "track_index_locator": source.get("track_index"),
        }
        if not als_path.is_file():
            return [], ["MIDI_ALS_NOT_FOUND"], {**diagnostics, "status": "NOT_FOUND"}
        actual_identity = identity_for_als_path(als_path)
        diagnostics["actual_project_identity"] = actual_identity
        if actual_identity != expected_identity:
            return [], ["MIDI_PROJECT_IDENTITY_MISMATCH"], {**diagnostics, "status": "PROJECT_MISMATCH"}
        persistent = source.get("persistent_track_ref")
        if not isinstance(persistent, dict):
            return [], ["MIDI_PERSISTED_TRACK_REF_MISSING"], {**diagnostics, "status": "TRACK_REF_MISSING"}
        matched = match_als_track(
            _load_als_root(als_path),
            PersistentObjectRef.model_validate(persistent),
        )
        if not matched.get("ok"):
            root = _load_als_root(als_path)
            parents = _parent_map(root)
            candidate_rows: list[dict[str, Any]] = []
            for track_index, track in enumerate(item for item in root.iter() if _local(item.tag) in TRACK_TAGS):
                name = _track_locator_name(track)
                if name != str(source.get("track_name") or persistent.get("name") or ""):
                    continue
                names, classes = _als_device_inventory(track)
                current_read = read_arrangement_midi(
                    root,
                    track,
                    parents,
                    region_start=float((pack.get("timeline") or {}).get("start_qn") or 0.0),
                    region_end=float((pack.get("timeline") or {}).get("end_qn") or 0.0),
                )
                current_clips = current_read.get("clips") or []
                current_notes = current_read.get("notes") or []
                canonical = {
                    "role": "midi" if _local(track.tag) == "MidiTrack" else "unknown",
                    "device_names": names,
                    "device_classes": classes,
                    "clip_names": [str(clip.get("clip_name") or "") for clip in current_clips],
                    "arrangement_clip_spans": [
                        [float(clip["arrangement_start_qn"]), float(clip["arrangement_end_qn"])]
                        for clip in current_clips
                    ],
                    "arrangement_note_count": len(current_notes),
                }
                candidate_rows.append({
                    "track_locator_index": track_index,
                    "track_type": _local(track.tag),
                    "track_name": name,
                    **canonical,
                    "raw_candidate_digest": hashlib.sha256(
                        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest(),
                })
            expected = {
                "role": persistent.get("role"),
                "device_names": persistent.get("device_names") or [],
                "device_classes": persistent.get("device_classes") or [],
                "clip_slots": persistent.get("clip_slots") or [],
                "clip_names": persistent.get("clip_names") or [],
                "note_counts": persistent.get("note_counts") or [],
                "content_fingerprint": persistent.get("content_fingerprint"),
            }
            mismatch_fields: list[str] = []
            if candidate_rows:
                candidate = candidate_rows[0]
                if expected["role"] != candidate["role"]:
                    mismatch_fields.append("role")
                if expected["device_names"] != candidate["device_names"]:
                    mismatch_fields.append("device_names")
                if expected["device_classes"] != candidate["device_classes"]:
                    mismatch_fields.append("device_classes")
                if expected["clip_names"] != candidate["clip_names"]:
                    mismatch_fields.append("clip_names")
                if expected["note_counts"] != [candidate["arrangement_note_count"]]:
                    mismatch_fields.append("note_counts_or_clip_scope")
                if expected["clip_slots"]:
                    mismatch_fields.append("clip_slots_not_reconciled_to_arrangement_clips")
            return [], ["MIDI_TRACK_IDENTITY_UNRESOLVED", str(matched.get("error"))], {
                **diagnostics,
                "status": "TRACK_IDENTITY_UNRESOLVED",
                "match": {key: value for key, value in matched.items() if key != "element"},
                "expected_persisted_ref": expected,
                "current_same_name_candidates": candidate_rows,
                "mismatch_fields": mismatch_fields,
                "resolution_rule": "NAME_AND_INDEX_ARE_LOCATORS_ONLY; NO_GUESSING",
            }
        root = _load_als_root(als_path)
        parents = _parent_map(root)
        start_qn = float((pack.get("timeline") or {}).get("start_qn") or 0.0)
        end_qn = float((pack.get("timeline") or {}).get("end_qn") or 0.0)
        read = read_arrangement_midi(root, matched["element"], parents, region_start=start_qn, region_end=end_qn)
        events: list[BassPitchEvent] = []
        for idx, note in enumerate(read.get("notes") or []):
            if note.get("muted") or note.get("pitch") is None:
                continue
            onset_qn = float(note["arrangement_start_qn"]) - start_qn
            duration_qn = float(note["duration_qn"])
            onset_s = onset_qn * 60.0 / tempo_bpm
            pitch = int(note["pitch"])
            events.append(BassPitchEvent(
                event_id=f"{evidence_prefix}:midi:{idx:04d}",
                grid=_grid_point(onset_s, tempo_bpm, evidence=f"{evidence_prefix}:midi"),
                offset_s=max(0.001, duration_qn * 60.0 / tempo_bpm),
                f0_hz=float(440.0 * 2.0 ** ((pitch - 69) / 12.0)),
                midi_float=float(pitch),
                midi_note=pitch,
                pitch_class=PITCH_CLASSES[pitch % 12],
                confidence=1.0,
                status="RELIABLE",
                onset_qn=onset_qn,
                offset_qn=onset_qn + duration_qn,
                duration_qn=duration_qn,
                source_kind="ABLETON_MIDI",
                voiced_fraction=1.0,
                evidence_refs=[f"{evidence_prefix}:midi_readback", str(note.get("clip_identity") or "")],
            ))
        diagnostics.update({"status": "READ", "clips": len(read.get("clips") or []), "notes": len(events)})
        return events, [], diagnostics
    except Exception as exc:  # pragma: no cover - external artifact dependent
        return [], [f"MIDI_READ_FAILED:{type(exc).__name__}:{exc}"], {"status": "FAILED"}


def analyze_musical_understanding(
    stem_analysis_path: Path | str,
    *,
    midi_pack_path: Path | str | None = None,
    midi_reconciliation_path: Path | str | None = None,
    output_path: Path | str | None = None,
    report_path: Path | str | None = None,
) -> MusicalUnderstanding:
    """Analyze cached BASS/DRUMS and prefer reconciled MIDI without writes."""
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
    midi_events, midi_limits, midi_diagnostics = _midi_notes(
        Path(midi_pack_path) if midi_pack_path is not None else None,
        tempo_bpm=source.tempo_bpm,
        evidence_prefix=source.reference_id,
    )
    if midi_diagnostics.get("status") == "READ":
        bass_events = midi_events
        bass_limits = midi_limits
        pitch_diagnostics = midi_diagnostics
        bass_source_kind = "ABLETON_MIDI"
    else:
        pitch_result = _pitch_events(bass_path, source.tempo_bpm, source.reference_id)
        if len(pitch_result) == 3:  # compatibility with test/provider stubs
            bass_events, bass_limits, histogram = pitch_result
            pitch_diagnostics = {}
        else:
            bass_events, bass_limits, histogram, pitch_diagnostics = pitch_result
        bass_limits = [*midi_limits, *bass_limits]
        pitch_diagnostics = {"midi": midi_diagnostics, "audio": pitch_diagnostics}
        bass_source_kind = "AUDIO_PYIN_ONSET_CONDITIONED"
    bass_rhythm = _rhythm(
        bass_events,
        total_bars=total_bars,
        tempo_bpm=source.tempo_bpm,
        evidence=f"{source.reference_id}:bass",
    )
    if bass_source_kind == "ABLETON_MIDI":
        histogram = {pc: 0.0 for pc in PITCH_CLASSES}
        reliable_midi = [event for event in bass_events if event.pitch_class]
        total = sum((event.duration_qn or 0.0) for event in reliable_midi)
        if total > 0:
            for event in reliable_midi:
                histogram[event.pitch_class or "C"] += (event.duration_qn or 0.0) / total
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
        source_kind=bass_source_kind,
        source_diagnostics=pitch_diagnostics,
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
            "midi_pack_path": str(midi_pack_path) if midi_pack_path is not None else None,
            "source_kind": bass_source_kind,
            "bass_sha256": _sha256(bass_path),
            "drums_sha256": _sha256(drums_path),
            "pitch_provider": "ableton-midi-read-only-v1" if bass_source_kind == "ABLETON_MIDI" else ("librosa.pyin+librosa.onset" if librosa_version else "unavailable"),
            "librosa_version": librosa_version,
            "model_api_calls": 0,
            "musical_writes": 0,
            "analyzer_id": "musical-understanding-deterministic-v1",
        },
    )
    if midi_reconciliation_path is not None:
        reconciliation = {
            "schema_version": "midi-source-reconciliation-v1",
            "status": midi_diagnostics.get("status", "NOT_PROVIDED"),
            "reference_id": source.reference_id,
            "source_analysis_id": source.source_analysis_id,
            "project_identity": midi_diagnostics.get("actual_project_identity"),
            "diagnostics": midi_diagnostics,
            "model_api_calls": 0,
            "musical_writes": 0,
        }
        target = Path(midi_reconciliation_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(reconciliation, indent=2, ensure_ascii=False), encoding="utf-8")
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
        f"source_kind: {result.bass.source_kind}",
        f"pitch_events: {len(result.bass.pitch_events)} reliable: {sum(e.status == 'RELIABLE' for e in result.bass.pitch_events)}",
        f"tonality: {result.bass.tonality_status} ({key_text})",
        f"intervals: {len(result.bass.intervals)}",
        f"rhythm_events: {result.bass.rhythmic_structure.event_count}",
        f"phrases: {[(p.structural_label, p.event_count) for p in result.bass.phrase_structure]}",
        "note_table:",
        "bar beat | qn | duration_qn | midi | pitch_class | confidence | source",
        *[
            f"{event.grid.bar:g} {event.grid.beat_in_bar:g} | {event.onset_qn if event.onset_qn is not None else event.grid.onset_qn:.3f} | {event.duration_qn if event.duration_qn is not None else 0.0:.3f} | {event.midi_note if event.status == 'RELIABLE' and event.midi_note is not None else 'UNKNOWN'} | {event.pitch_class if event.status == 'RELIABLE' and event.pitch_class else 'UNKNOWN'} | {event.confidence:.3f} | {event.source_kind} | {event.status}"
            for event in result.bass.pitch_events
        ],
        f"source_diagnostics: {result.bass.source_diagnostics}",
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
