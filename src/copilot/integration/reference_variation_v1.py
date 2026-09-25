"""REFERENCE_TO_VARIATION_V1: one reference-bound MIDI variation.

Core owns measured evidence.  This module converts that evidence into a new,
editable bass pattern.  It deliberately does not copy source MIDI (the
reference may be audio-only) and never talks to Ableton.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from copilot.schemas.music_analysis import MusicAnalysisPack
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


def _reference_features(pack: MusicAnalysisPack) -> dict[str, Any]:
    windows = list(pack.windows)
    if not windows:
        raise ReferenceVariationError("REFERENCE_FEATURES_INSUFFICIENT: no measured windows")
    events: list[float] = []
    densities: list[float] = []
    repetitions: list[float] = []
    variations: list[float] = []
    energies: list[float] = []
    centroids: list[float] = []
    for window in windows:
        events.extend(window.groove.event_locations)
        for value, target in (
            (window.groove.onset_density_per_s, densities),
            (window.groove.repetition_strength, repetitions),
            (window.groove.variation_score, variations),
            (window.energy_db, energies),
            (window.timbre.spectral_centroid_hz, centroids),
        ):
            if value is not None:
                target.append(float(value))
    if not events:
        raise ReferenceVariationError("REFERENCE_FEATURES_INSUFFICIENT: no measured events")
    low = [float(w.low_band_energy) for w in windows if w.low_band_energy is not None]
    return {
        "event_locations": sorted(set(round(float(item), 3) for item in events)),
        "event_count": len(events),
        "onset_density_per_s": sum(densities) / len(densities) if densities else None,
        "repetition_strength": sum(repetitions) / len(repetitions) if repetitions else None,
        "variation_score": sum(variations) / len(variations) if variations else None,
        "energy_db": sum(energies) / len(energies) if energies else None,
        "spectral_centroid_hz": sum(centroids) / len(centroids) if centroids else None,
        "relative_low_band_energy": sum(low) / len(low) if low else None,
        "evidence_refs": list(dict.fromkeys(pack.evidence_refs + [ref for w in windows for ref in w.evidence_refs])),
    }


def build_reference_bound_bass_notes(
    pack: MusicAnalysisPack,
    *,
    length_beats: float,
    transformation_seed: str,
) -> tuple[list[MidiNote], dict[str, Any]]:
    """Generate new notes from measured rhythm/register, not a fixed motif.

    The reference event grid is rotated on alternating bars, lightly thinned,
    and assigned a measured-register bass vocabulary.  This preserves
    high-level behavior while guaranteeing a different note sequence.
    """
    if length_beats <= 0:
        raise ReferenceVariationError("REFERENCE_LENGTH_INVALID")
    features = _reference_features(pack)
    source_span = max(
        float(pack.timeline.get("end_qn") or 0.0)
        - float(pack.timeline.get("start_qn") or 0.0),
        4.0,
    )
    normalized = [((event - float(pack.timeline.get("start_qn") or 0.0)) % source_span) for event in features["event_locations"]]
    grid = sorted(set(round((event / source_span) * length_beats * 2.0) / 2.0 for event in normalized))
    # Never produce an unmusical wall of onsets from full-mix detection.
    grid = [item for index, item in enumerate(grid) if index % 2 == 0 and item < length_beats]
    if not grid:
        raise ReferenceVariationError("REFERENCE_FEATURES_INSUFFICIENT: no usable rhythmic grid")

    centroid = features.get("spectral_centroid_hz")
    base_pitch = 36 if centroid is None or centroid < 500.0 else 40
    pattern = (0, 0, 3, 5, 0, 7, 5, 3)
    notes: list[MidiNote] = []
    for index, start in enumerate(grid):
        # Alternating bar rotation creates new material instead of copying the
        # reference's exact event positions.
        transformed = (start + (0.5 if int(start // 4) % 2 else 0.0)) % length_beats
        if transformed + 1.0 > length_beats:
            transformed = max(0.0, length_beats - 1.0)
        pitch = base_pitch + pattern[(index + len(transformation_seed)) % len(pattern)]
        duration = 1.0 if index % 3 else 1.5
        notes.append(MidiNote(
            pitch=pitch,
            start_time=round(transformed, 3),
            duration=duration,
            velocity=96 if index % 4 else 108,
        ))
    notes.sort(key=lambda note: (note.start_time, note.pitch))
    deduped: list[MidiNote] = []
    seen: set[tuple[float, int]] = set()
    for note in notes:
        key = (note.start_time, note.pitch)
        if key not in seen:
            deduped.append(note)
            seen.add(key)
    features.update({
        "source_span_beats": source_span,
        "generated_event_count": len(deduped),
        "register_base_pitch": base_pitch,
        "transformation": "quantized_reference_events_plus_alternating_half_beat_rotation_and_thinning",
        "source_not_copied": True,
    })
    return deduped, features

