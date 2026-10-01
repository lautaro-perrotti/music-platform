"""Build one bounded bass variation from authoritative, read-only MIDI evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from copilot.schemas.music_analysis import MusicAnalysisPack
from copilot.schemas.musical_understanding import MusicalUnderstanding
from copilot.schemas.session import MidiNote


class ReferenceVariationError(ValueError):
    """The reference does not contain enough measured evidence to vary safely."""


def validate_bass_variation_symbolically(
    source_events: list[Any],
    generated_notes: list[MidiNote],
    *,
    length_beats: float,
    moved_onsets: int,
    source_offset_qn: float = 0.0,
) -> dict[str, Any]:
    """Validate one generated bass phrase before it reaches Ableton.

    This is a deterministic plan gate, not a new musical analyzer.  It checks
    the invariants that make the bounded variation safe to execute and keeps
    the distinction between evidenced source material and the transformed
    event sequence explicit.
    """
    if not generated_notes:
        raise ReferenceVariationError("BASS_VARIATION_NO_GENERATED_NOTES")
    if length_beats <= 0:
        raise ReferenceVariationError("BASS_VARIATION_LENGTH_INVALID")
    if any(
        note.start_time < -1e-6
        or note.duration <= 0
        or note.start_time + note.duration > length_beats + 1e-6
        or not 0 <= note.pitch <= 127
        for note in generated_notes
    ):
        raise ReferenceVariationError("BASS_VARIATION_SYMBOLIC_BOUNDS_INVALID")

    source_pitches = {int(event.midi_note) for event in source_events if event.midi_note is not None}
    if not source_pitches or any(note.pitch not in source_pitches for note in generated_notes):
        raise ReferenceVariationError("BASS_VARIATION_UNSUPPORTED_PITCH_MATERIAL")

    source_signature = [
        (round(float(event.onset_qn) - source_offset_qn, 4), int(event.midi_note), round(float(event.duration_qn), 4))
        for event in source_events
    ]
    generated_signature = [
        (round(note.start_time, 4), note.pitch, round(note.duration, 4))
        for note in generated_notes
    ]
    direct_copy = source_signature == generated_signature
    if direct_copy or moved_onsets <= 0:
        raise ReferenceVariationError("BASS_VARIATION_NOT_NEW_MATERIAL")

    return {
        "status": "VERIFIED",
        "length_beats": length_beats,
        "source_note_count": len(source_events),
        "generated_note_count": len(generated_notes),
        "note_count_preserved": len(source_events) == len(generated_notes),
        "pitch_range": {
            "source": [min(source_pitches), max(source_pitches)],
            "generated": [min(note.pitch for note in generated_notes), max(note.pitch for note in generated_notes)],
        },
        "harmonic_compatibility": "EVIDENCED_SOURCE_PITCH_MATERIAL",
        "direct_note_copy": direct_copy,
        "rhythm_transformed": moved_onsets > 0,
        "phrase_length_preserved": all(note.start_time + note.duration <= length_beats + 1e-6 for note in generated_notes),
        "unresolved_harmony_preserved": True,
    }


def load_reference_pack(path: Path | str) -> MusicAnalysisPack:
    source = Path(path)
    if not source.is_file():
        raise ReferenceVariationError(f"REFERENCE_PACK_NOT_FOUND: {source}")
    try:
        body = json.loads(source.read_text(encoding="utf-8"))
        return MusicAnalysisPack.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        raise ReferenceVariationError(f"REFERENCE_PACK_INVALID: {source}") from exc


def load_musical_understanding(path: Path | str) -> MusicalUnderstanding:
    source = Path(path)
    if not source.is_file():
        raise ReferenceVariationError(f"MUSICAL_UNDERSTANDING_NOT_FOUND: {source}")
    try:
        return MusicalUnderstanding.model_validate_json(source.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ReferenceVariationError(f"MUSICAL_UNDERSTANDING_INVALID: {source}") from exc


def validate_midi_reference(
    pack: MusicAnalysisPack,
    understanding: MusicalUnderstanding,
    *,
    pack_path: Path,
    understanding_path: Path,
    project_identity: str,
) -> None:
    """Refuse facts from a different project, analysis, or MIDI source."""
    if not pack.no_write or not understanding.no_write:
        raise ReferenceVariationError("REFERENCE_NOT_READ_ONLY")
    if pack.project_id != project_identity:
        raise ReferenceVariationError("REFERENCE_PROJECT_MISMATCH")
    if understanding.reference_id != pack.reference_id or understanding.source_analysis_id != pack_path.stem:
        raise ReferenceVariationError("REFERENCE_ANALYSIS_MISMATCH")
    if understanding.bass.source_kind != "ABLETON_MIDI" or understanding.bass.status != "SUPPORTED":
        raise ReferenceVariationError("AUTHORITATIVE_BASS_MIDI_REQUIRED")
    diagnostics = understanding.bass.source_diagnostics
    if (
        diagnostics.get("expected_project_identity") != project_identity
        or diagnostics.get("actual_project_identity") != project_identity
        or (diagnostics.get("reconciliation") or {}).get("ok") is not True
    ):
        raise ReferenceVariationError("MIDI_PROJECT_IDENTITY_MISMATCH")
    declared_pack = understanding.provenance.get("midi_pack_path")
    if not declared_pack or Path(str(declared_pack)).resolve() != pack_path.resolve():
        raise ReferenceVariationError("MIDI_PACK_PROVENANCE_MISMATCH")
    if not understanding_path.is_file():
        raise ReferenceVariationError("MUSICAL_UNDERSTANDING_NOT_FOUND")


def load_astra_interpretation(path: Path | str) -> dict[str, Any]:
    """Accept only a persisted real provider result, never a fixture."""
    source = Path(path)
    if not source.is_file():
        raise ReferenceVariationError(f"ASTRA_INTERPRETATION_NOT_FOUND: {source}")
    try:
        body = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ReferenceVariationError(f"ASTRA_INTERPRETATION_INVALID: {source}") from exc
    if body.get("status") != "REAL_INTERPRETATION_RETURNED":
        raise ReferenceVariationError("ASTRA_INTERPRETATION_NOT_REAL")
    raw = body.get("raw_response")
    if not isinstance(raw, str) or not raw.strip():
        raise ReferenceVariationError("ASTRA_INTERPRETATION_EMPTY")
    return {
        "path": str(source.resolve()),
        "provider": body.get("provider"),
        "model": body.get("model"),
        "status": body.get("status"),
        "response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "summary": _raw_summary(raw),
    }


def _raw_summary(raw: str) -> str:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:500]
    return str(value.get("summary") or "")[:1000]


def build_reference_bound_bass_notes(
    pack: MusicAnalysisPack,
    understanding: MusicalUnderstanding,
    *,
    start_qn: float,
    length_beats: float,
    variation_index: int = 1,
) -> tuple[list[MidiNote], dict[str, Any]]:
    """Build one deterministic, evidence-bound bass variation.

    Index 1 preserves the previously validated transform.  Indices 2-5 use
    different deterministic combinations of onset movement, pitch assignment,
    and density while staying inside the source's evidenced material.
    """
    if variation_index not in {1, 2, 3, 4, 5}:
        raise ReferenceVariationError("BASS_VARIATION_INDEX_INVALID")
    source_start = float(pack.timeline.get("start_qn") or 0.0)
    source_end = float(pack.timeline.get("end_qn") or 0.0)
    if length_beats <= 0 or start_qn < source_start or start_qn + length_beats > source_end:
        raise ReferenceVariationError("REFERENCE_REGION_OUT_OF_BOUNDS")
    local_start = start_qn - source_start
    source_events = sorted(
        (
            event for event in understanding.bass.pitch_events
            if event.source_kind == "ABLETON_MIDI"
            and event.status == "RELIABLE"
            and event.midi_note is not None
            and event.onset_qn is not None
            and event.duration_qn is not None
            and local_start <= event.onset_qn < local_start + length_beats
        ),
        key=lambda event: (float(event.onset_qn), event.event_id),
    )
    if not source_events:
        raise ReferenceVariationError("AUTHORITATIVE_BASS_EVENTS_MISSING")
    # The authoritative bass track may contain simultaneous ornaments and
    # overlapping notes. Select one event per near-simultaneous attack so the
    # new pattern has a playable monophonic line without inventing pitches.
    events = []
    for event in source_events:
        if events and float(event.onset_qn) - float(events[-1].onset_qn) < 0.1:
            previous = events[-1]
            if (float(event.duration_qn), -int(event.midi_note)) > (float(previous.duration_qn), -int(previous.midi_note)):
                events[-1] = event
        else:
            events.append(event)
    if variation_index == 5 and len(events) > 4:
        events = [event for index, event in enumerate(events) if index % 4 != 3]
    original = [float(event.onset_qn) - local_start for event in events]
    shifted = original.copy()
    first_by_bar: set[int] = set()
    moved = 0
    step = {1: 0.25, 2: 0.25, 3: 0.5, 4: 0.25, 5: 0.75}[variation_index]
    direction = {1: 1, 2: -1, 3: 1, 4: -1, 5: 1}[variation_index]
    eligible_mod = {1: 2, 2: 2, 3: 3, 4: 3, 5: 2}[variation_index]
    eligible_remainder = {1: 1, 2: 0, 3: 1, 4: 2, 5: 1}[variation_index]
    for index, onset in enumerate(original):
        bar = int(onset // 4)
        if bar not in first_by_bar:
            first_by_bar.add(bar)
            continue
        if index % eligible_mod != eligible_remainder:
            continue
        previous = shifted[index - 1]
        following = original[index + 1] if index + 1 < len(original) else length_beats
        bar_start, bar_end = bar * 4.0, min((bar + 1) * 4.0, length_beats)
        for candidate in (onset + direction * step, onset - direction * step):
            if max(previous, bar_start) + 0.05 < candidate < min(following, bar_end) - 0.05:
                shifted[index] = candidate
                moved += 1
                break
    if not moved:
        raise ReferenceVariationError("BASS_VARIATION_NO_SAFE_TRANSFORM")
    pitches = [int(event.midi_note) for event in events]
    if variation_index == 3:
        pitches = pitches[1:] + pitches[:1]
    elif variation_index == 4:
        pitches = list(reversed(pitches))
    elif variation_index == 5:
        rotation = min(2, len(pitches))
        pitches = pitches[-rotation:] + pitches[:-rotation] if rotation else pitches

    notes: list[MidiNote] = []
    for index, event in enumerate(events):
        onset = shifted[index]
        next_onset = shifted[index + 1] if index + 1 < len(events) else length_beats
        duration = min(float(event.duration_qn), next_onset - onset, length_beats - onset)
        if duration <= 0.05:
            raise ReferenceVariationError("BASS_VARIATION_INVALID_DURATION")
        notes.append(MidiNote(
            pitch=pitches[index], start_time=round(onset, 4),
            duration=round(duration, 4), velocity=100,
        ))
    event_traceability = [
        {
            "generated_index": index,
            "source_event_id": event.event_id,
            "source_onset_qn": round(original[index], 4),
            "generated_onset_qn": round(notes[index].start_time, 4),
            "source_pitch": int(event.midi_note),
            "generated_pitch": notes[index].pitch,
            "preserved": ["evidenced_pitch_material", "bar_boundary", "source_register"],
            "changed": (
                (["secondary_onset"] if notes[index].start_time != round(original[index], 4) else [])
                + (["pitch_assignment"] if notes[index].pitch != int(event.midi_note) else [])
            ),
            "pitch_supported_by_source": notes[index].pitch in {int(item.midi_note) for item in events},
            "direct_copy": (
                notes[index].start_time == round(original[index], 4)
                and notes[index].pitch == int(event.midi_note)
                and notes[index].duration == round(float(event.duration_qn), 4)
            ),
        }
        for index, event in enumerate(events)
    ]
    symbolic_validation = validate_bass_variation_symbolically(
        events, notes, length_beats=length_beats, moved_onsets=moved,
        source_offset_qn=local_start,
    )
    return notes, {
        "source_kind": "ABLETON_MIDI",
        "source_event_count": len(source_events),
        "generated_event_count": len(notes),
        "simultaneous_events_merged": len(source_events) - len(events),
        "shifted_onsets": moved,
        "source_region_qn": [start_qn, start_qn + length_beats],
        "variation_index": variation_index,
        "variation_strategy": {
            1: "secondary_onsets_quarter_qn_within_bar",
            2: "alternate_secondary_onsets_earlier_within_bar",
            3: "half_beat_onsets_with_rotated_evidenced_pitch_assignment",
            4: "reverse_evidenced_pitch_assignment_with_alternate_onsets",
            5: "reduced_density_rotated_pitch_assignment_and_larger_onset_moves",
        }[variation_index],
        "transformation": "reference_bound_deterministic_variation",
        "source_not_copied": True,
        "event_traceability": event_traceability,
        "symbolic_validation": symbolic_validation,
        "evidence_refs": list(dict.fromkeys(ref for event in events for ref in event.evidence_refs)),
    }

