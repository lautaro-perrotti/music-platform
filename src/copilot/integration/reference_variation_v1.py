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
) -> tuple[list[MidiNote], dict[str, Any]]:
    """Move secondary onsets by a quarter beat; retain evidenced pitches."""
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
    original = [float(event.onset_qn) - local_start for event in events]
    shifted = original.copy()
    first_by_bar: set[int] = set()
    moved = 0
    for index, onset in enumerate(original):
        bar = int(onset // 4)
        if bar not in first_by_bar:
            first_by_bar.add(bar)
            continue
        if index % 2 == 0:
            continue
        previous = shifted[index - 1]
        following = original[index + 1] if index + 1 < len(original) else length_beats
        bar_start, bar_end = bar * 4.0, min((bar + 1) * 4.0, length_beats)
        for candidate in (onset + 0.25, onset - 0.25):
            if max(previous, bar_start) + 0.05 < candidate < min(following, bar_end) - 0.05:
                shifted[index] = candidate
                moved += 1
                break
    if not moved:
        raise ReferenceVariationError("BASS_VARIATION_NO_SAFE_TRANSFORM")
    notes: list[MidiNote] = []
    for index, event in enumerate(events):
        onset = shifted[index]
        next_onset = shifted[index + 1] if index + 1 < len(events) else length_beats
        duration = min(float(event.duration_qn), next_onset - onset, length_beats - onset)
        if duration <= 0.05:
            raise ReferenceVariationError("BASS_VARIATION_INVALID_DURATION")
        notes.append(MidiNote(
            pitch=int(event.midi_note), start_time=round(onset, 4),
            duration=round(duration, 4), velocity=100,
        ))
    return notes, {
        "source_kind": "ABLETON_MIDI",
        "source_event_count": len(source_events),
        "generated_event_count": len(notes),
        "simultaneous_events_merged": len(source_events) - len(events),
        "shifted_onsets": moved,
        "source_region_qn": [start_qn, start_qn + length_beats],
        "transformation": "secondary_onsets_quarter_qn_within_bar",
        "source_not_copied": True,
        "evidence_refs": list(dict.fromkeys(ref for event in events for ref in event.evidence_refs)),
    }

