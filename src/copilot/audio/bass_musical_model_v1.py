"""Build a deterministic symbolic bass model from cached musical evidence.

This module is deliberately downstream of capture, separation, and MIDI
reconciliation. It reads an existing ``MusicalUnderstanding`` artifact and
does not call a model, access Ableton, or authorize any musical write.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from copilot.schemas.bass_musical_model import (
    BassMotif,
    BassMusicalModel,
    BassPhraseModel,
    IntervalLanguage,
    PitchClassMaterial,
    RhythmicCell,
)
from copilot.schemas.musical_understanding import BassPitchEvent, MusicalUnderstanding


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reliable_events(result: MusicalUnderstanding) -> list[BassPitchEvent]:
    return [
        event
        for event in result.bass.pitch_events
        if event.status == "RELIABLE" and event.midi_note is not None and event.pitch_class
    ]


def _pitch_material(events: list[BassPitchEvent]) -> list[PitchClassMaterial]:
    by_class: dict[str, list[BassPitchEvent]] = defaultdict(list)
    for event in events:
        by_class[str(event.pitch_class)].append(event)
    total_duration = sum(float(event.duration_qn or 0.0) for event in events)
    denominator = total_duration or float(len(events) or 1)
    rows: list[PitchClassMaterial] = []
    for pitch_class in sorted(by_class):
        rows_for_class = by_class[pitch_class]
        duration = sum(float(event.duration_qn or 0.0) for event in rows_for_class)
        rows.append(
            PitchClassMaterial(
                pitch_class=pitch_class,
                midi_note_count=len(rows_for_class),
                duration_qn=duration,
                duration_ratio=duration / denominator if total_duration else len(rows_for_class) / denominator,
                first_onset_qn=min(float(event.onset_qn or event.grid.onset_qn) for event in rows_for_class),
                last_onset_qn=max(float(event.onset_qn or event.grid.onset_qn) for event in rows_for_class),
            )
        )
    return rows


def _interval_language(events: list[BassPitchEvent]) -> IntervalLanguage:
    deltas = [
        int(second.midi_note - first.midi_note)  # type: ignore[operator]
        for first, second in zip(events, events[1:])
    ]
    interval_classes = [abs(delta) % 12 for delta in deltas]
    interval_classes = [value if value <= 6 else 12 - value for value in interval_classes]
    directions = ["UP" if delta > 0 else "DOWN" if delta < 0 else "SAME" for delta in deltas]
    total = len(deltas) or 1
    return IntervalLanguage(
        total_intervals=len(deltas),
        semitone_histogram={str(key): value for key, value in sorted(Counter(deltas).items())},
        interval_class_histogram={str(key): value for key, value in sorted(Counter(interval_classes).items())},
        direction_histogram={key: value for key, value in sorted(Counter(directions).items())},
        repeated_note_ratio=sum(delta == 0 for delta in deltas) / total,
        stepwise_ratio=sum(abs(delta) <= 2 for delta in deltas) / total,
        leap_ratio=sum(abs(delta) >= 7 for delta in deltas) / total,
        largest_absolute_interval=max((abs(delta) for delta in deltas), default=0),
        evidence_refs=[event.event_id for event in events],
    )


def _duration_bucket(duration_qn: float) -> str:
    if duration_qn <= 0.25:
        return "<=1/16"
    if duration_qn <= 0.5:
        return "<=1/8"
    if duration_qn <= 1.0:
        return "<=1/4"
    if duration_qn <= 2.0:
        return "<=1/2"
    return ">1/2"


def _phrase_events(result: MusicalUnderstanding, events: list[BassPitchEvent]) -> list[tuple[Any, list[BassPitchEvent]]]:
    rows: list[tuple[Any, list[BassPitchEvent]]] = []
    for phrase in result.bass.phrase_structure:
        phrase_rows = [
            event
            for event in events
            if phrase.start_bar <= float(event.grid.bar) < phrase.end_bar
        ]
        rows.append((phrase, phrase_rows))
    return rows


def _phrase_signature(
    phrase: Any,
    events: list[BassPitchEvent],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    onset_tokens: list[str] = []
    duration_tokens: list[str] = []
    full_tokens: list[str] = []
    previous_pitch: int | None = None
    phrase_start_qn = (float(phrase.start_bar) - 1.0) * 4.0
    for event in events:
        onset = float(event.onset_qn if event.onset_qn is not None else event.grid.onset_qn)
        relative = round((onset - phrase_start_qn) * 4.0) / 4.0
        duration = _duration_bucket(float(event.duration_qn or 0.0))
        onset_tokens.append(f"{relative:g}")
        duration_tokens.append(duration)
        delta = "ROOT" if previous_pitch is None else str(int(event.midi_note - previous_pitch))  # type: ignore[operator]
        full_tokens.append(f"{relative:g}:{duration}:{delta}")
        previous_pitch = event.midi_note
    return tuple(onset_tokens), tuple(duration_tokens), tuple(full_tokens)


def _rhythmic_cells(
    phrase_rows: list[tuple[Any, list[BassPitchEvent]]],
    *,
    evidence: str,
) -> tuple[list[RhythmicCell], dict[str, list[str]]]:
    grouped: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = defaultdict(list)
    signatures: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {}
    for phrase, events in phrase_rows:
        onset_tokens, duration_tokens, full_tokens = _phrase_signature(phrase, events)
        phrase_id = str(phrase.phrase_id)
        signatures[phrase_id] = (onset_tokens, duration_tokens, full_tokens)
        if events:
            grouped[(onset_tokens, duration_tokens)].append(phrase_id)
    cells: list[RhythmicCell] = []
    phrase_cell_ids: dict[str, list[str]] = defaultdict(list)
    for index, (key, phrase_ids) in enumerate(sorted(grouped.items(), key=lambda item: item[1][0])):
        onset_tokens, duration_tokens = key
        cell_id = f"cell_{index + 1:02d}"
        cells.append(
            RhythmicCell(
                cell_id=cell_id,
                onset_offsets_qn=[float(value) for value in onset_tokens],
                duration_buckets=list(duration_tokens),
                event_count=len(onset_tokens),
                occurrence_count=len(phrase_ids),
                phrase_ids=phrase_ids,
                evidence_refs=[evidence],
            )
        )
        for phrase_id in phrase_ids:
            phrase_cell_ids[phrase_id].append(cell_id)
    return cells, phrase_cell_ids


def _motifs(
    phrase_rows: list[tuple[Any, list[BassPitchEvent]]],
    *,
    evidence: str,
) -> tuple[list[BassMotif], dict[str, list[str]]]:
    grouped: dict[tuple[str, ...], list[str]] = defaultdict(list)
    rhythmic_by_phrase: dict[str, tuple[str, ...]] = {}
    full_by_phrase: dict[str, tuple[str, ...]] = {}
    phrase_by_id: dict[str, Any] = {}
    for phrase, events in phrase_rows:
        phrase_id = str(phrase.phrase_id)
        onset_tokens, _, full_tokens = _phrase_signature(phrase, events)
        rhythmic_by_phrase[phrase_id] = onset_tokens
        full_by_phrase[phrase_id] = full_tokens
        phrase_by_id[phrase_id] = phrase
        if events:
            grouped[full_tokens].append(phrase_id)

    first_signature = next(iter(grouped), tuple())
    first_phrase_id = next(iter(full_by_phrase), None)
    first_rhythmic_signature = rhythmic_by_phrase.get(first_phrase_id or "", tuple())
    motifs: list[BassMotif] = []
    phrase_motif_ids: dict[str, list[str]] = defaultdict(list)
    for index, (signature, phrase_ids) in enumerate(sorted(grouped.items(), key=lambda item: item[1][0])):
        motif_id = f"motif_{index + 1:02d}"
        if index == 0:
            relation = "FIRST_OBSERVED"
        elif signature == first_signature:
            relation = "EXACT_REPEAT"
        elif rhythmic_by_phrase.get(phrase_ids[0], tuple()) == first_rhythmic_signature:
            relation = "PITCH_VARIANT"
        else:
            relation = "UNIQUE_OR_TRANSFORMED"
        limitations: list[str] = []
        if relation == "UNIQUE_OR_TRANSFORMED":
            limitations.append("TRANSFORMATION_CLASS_IS_STRUCTURAL_NOT_SEMANTIC")
        motifs.append(
            BassMotif(
                motif_id=motif_id,
                signature=list(signature),
                phrase_ids=phrase_ids,
                occurrence_count=len(phrase_ids),
                event_count=len(signature),
                relation_to_first=relation,
                evidence_refs=[evidence],
                limitations=limitations,
            )
        )
        for phrase_id in phrase_ids:
            phrase_motif_ids[phrase_id].append(motif_id)
    return motifs, phrase_motif_ids


def build_bass_musical_model(
    understanding_path: Path | str,
    *,
    output_path: Path | str | None = None,
) -> BassMusicalModel:
    """Build and optionally persist a symbolic model from cached evidence."""
    path = Path(understanding_path)
    result = MusicalUnderstanding.model_validate_json(path.read_text(encoding="utf-8"))
    events = _reliable_events(result)
    evidence = f"{result.reference_id}:bass_musical_model"
    phrase_rows = _phrase_events(result, events)
    cells, phrase_cell_ids = _rhythmic_cells(phrase_rows, evidence=evidence)
    motifs, phrase_motif_ids = _motifs(phrase_rows, evidence=evidence)
    phrases: list[BassPhraseModel] = []
    for phrase, phrase_events in phrase_rows:
        phrase_id = str(phrase.phrase_id)
        limitations = list(phrase.limitations)
        if not phrase_events and "NO_EVENTS_IN_PHRASE" not in limitations:
            limitations.append("NO_EVENTS_IN_PHRASE")
        phrases.append(
            BassPhraseModel(
                phrase_id=phrase_id,
                start_bar=float(phrase.start_bar),
                end_bar=float(phrase.end_bar),
                structural_label=str(phrase.structural_label),
                event_count=len(phrase_events),
                density_per_bar=len(phrase_events) / max(float(phrase.end_bar - phrase.start_bar), 1.0),
                pitch_classes=sorted({str(event.pitch_class) for event in phrase_events if event.pitch_class}),
                rhythmic_cell_ids=phrase_cell_ids.get(phrase_id, []),
                motif_ids=phrase_motif_ids.get(phrase_id, []),
                evidence_refs=[evidence],
                limitations=limitations,
            )
        )

    limitations = [
        "PITCH_CLASSES_AND_INTERVALS_ARE_DESCRIPTIVE_NOT_HARMONIC_FUNCTION",
        "MOTIF_RELATIONS_ARE_STRUCTURAL_NOT_AESTHETIC_JUDGMENTS",
    ]
    if result.bass.selected_tonality is None:
        limitations.append("TONALITY_NOT_COLLAPSED_TO_SINGLE_HYPOTHESIS")
    if result.bass.source_kind != "ABLETON_MIDI":
        limitations.append("SOURCE_IS_AUDIO_DERIVED_AND_RETAINS_TRANSCRIPTION_LIMITS")
    if not events:
        limitations.append("NO_RELIABLE_SYMBOLIC_EVENTS")

    relationship = result.relationships.get("bass_drums")
    model = BassMusicalModel(
        reference_id=result.reference_id,
        source_analysis_id=result.source_analysis_id,
        source_kind=result.bass.source_kind,
        event_count=len(events),
        pitch_material=_pitch_material(events),
        interval_language=_interval_language(events),
        rhythmic_cells=cells,
        motifs=motifs,
        phrases=phrases,
        tonal_hypotheses=list(result.bass.tonality),
        selected_tonality=result.bass.selected_tonality,
        bass_drums_relationship=relationship,
        limitations=limitations,
        provenance={
            "source_understanding_path": str(path),
            "source_understanding_sha256": _sha256(path),
            "analyzer_id": "bass-musical-model-deterministic-v1",
            "model_api_calls": 0,
            "musical_writes": 0,
        },
    )
    if output_path is not None:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return model


def render_bass_musical_model_report(model: BassMusicalModel) -> str:
    selected = model.selected_tonality
    tonic = f"{selected.tonic} {selected.mode}" if selected else "INSUFFICIENT_EVIDENCE"
    lines = [
        "BASS_MUSICAL_MODEL_V1",
        f"reference_id: {model.reference_id}",
        f"source_kind: {model.source_kind}",
        f"events: {model.event_count}",
        f"pitch_classes: {[row.pitch_class for row in model.pitch_material]}",
        f"tonality: {tonic}",
        f"intervals: {model.interval_language.total_intervals}",
        f"rhythmic_cells: {[(cell.cell_id, cell.occurrence_count) for cell in model.rhythmic_cells]}",
        f"motifs: {[(motif.motif_id, motif.relation_to_first) for motif in model.motifs]}",
        f"phrases: {[(phrase.phrase_id, phrase.structural_label, phrase.event_count) for phrase in model.phrases]}",
        "MODEL/API CALLS: 0",
        "MUSICAL WRITES: 0",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["build_bass_musical_model", "render_bass_musical_model_report"]
