"""Bounded, original symbolic candidates from a typed producer TrackSpec.

This is a DAW-free composer, not a prompt interpreter or a quality judge.
It deliberately has no access to reference audio, reconstructed MIDI, or Live.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from copilot.musicplan.arrangement_engine import build_arrangement_engine_plan
from copilot.producer.track_spec import TrackSpec
from copilot.schemas.session import MidiNote


ROLES = frozenset({"KICK", "HAT", "BASS", "HARMONY", "HOOK"})
_PITCH_CLASSES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
                  "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


class SymbolicCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    source: str = "ORIGINAL_SYMBOLIC_FROM_TRACK_SPEC"
    tempo_bpm: float
    duration_bars: int
    key: str
    notes_by_role: dict[str, list[MidiNote]]
    structural_checks: list[str] = Field(default_factory=list)
    musical_winner: None = None


def _minor_root(key: str | None) -> int:
    if not key:
        raise ValueError("PRODUCER_KEY_REQUIRED; do not invent a tonal center")
    normalized = key.strip().replace("♯", "#").replace("♭", "b")
    if not normalized.lower().endswith("minor"):
        raise ValueError("V1_SUPPORTS_EXPLICIT_MINOR_KEY_ONLY")
    name = normalized[:-5].strip().upper()
    flats = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}
    name = flats.get(name, name)
    if name not in _PITCH_CLASSES:
        raise ValueError("UNSUPPORTED_KEY")
    return _PITCH_CLASSES[name]


def _note(pitch: int, start: float, duration: float, velocity: int) -> MidiNote:
    return MidiNote(pitch=pitch, start_time=start, duration=duration, velocity=velocity)


def _compose(spec: TrackSpec, variant: int, root_pc: int) -> SymbolicCandidate:
    notes: dict[str, list[MidiNote]] = {role: [] for role in sorted(ROLES)}
    bass_root = 36 + root_pc
    chord_root = 60 + root_pc
    cursor = 0
    for section_index, section in enumerate(spec.sections):
        active = set(section.active_roles)
        for local_bar in range(section.bars):
            bar = cursor + local_bar
            qn = float(bar * 4)
            # Every four-bar phrase has an observable change; variants alter
            # accents and syncopation without using a reference event grid.
            phrase_turn = (bar % 4 == 3)
            if "KICK" in active:
                for beat in range(4):
                    notes["KICK"].append(_note(36, qn + beat, 0.125,
                                               108 if beat == 0 else 94))
            if "HAT" in active:
                for beat in range(4):
                    notes["HAT"].append(_note(42, qn + beat + 0.5, 0.125,
                                              70 + (variant * 4 if beat % 2 else 0)))
                if variant == 2 and phrase_turn:
                    notes["HAT"].append(_note(42, qn + 3.75, 0.125, 55))
            if "BASS" in active:
                offsets = ((0.75, 2.5), (0.5, 2.75), (0.75, 2.25))[variant]
                if section_index == 0 and local_bar < 2:
                    offsets = offsets[:1]
                for n, offset in enumerate(offsets):
                    pitch = bass_root + (7 if phrase_turn and n == len(offsets) - 1 else 0)
                    notes["BASS"].append(_note(pitch, qn + offset, 0.375,
                                                96 if n == 0 else 82))
            if "HARMONY" in active and local_bar % 2 == 0:
                onset = qn + (0.0 if variant != 1 else 1.0)
                # Rootless minor color, leaving room for the bass fundamental.
                for interval in (3, 7, 10):
                    notes["HARMONY"].append(_note(chord_root + interval, onset, 1.5, 70))
            if "HOOK" in active and local_bar % 2 == 0:
                hook_offsets = ((1.5, 3.0), (1.0, 3.5), (1.5, 2.75))[variant]
                hook_intervals = ((12, 10), (10, 7), (7, 12))[variant]
                for offset, interval in zip(hook_offsets, hook_intervals):
                    notes["HOOK"].append(_note(chord_root + interval, qn + offset, 0.25, 78))
        cursor += section.bars

    candidate = SymbolicCandidate(candidate_id=f"Candidate {chr(65 + variant)}",
                                  tempo_bpm=spec.bpm, duration_bars=spec.duration_bars,
                                  key=spec.key or "", notes_by_role=notes)
    return validate_candidate(candidate, spec)


def validate_candidate(candidate: SymbolicCandidate, spec: TrackSpec) -> SymbolicCandidate:
    """Reject structural defects only; never call a candidate good music."""
    if candidate.duration_bars != spec.duration_bars or candidate.tempo_bpm != spec.bpm:
        raise ValueError("CANDIDATE_FORM_MISMATCH")
    starts = {}
    cursor = 0
    for section in spec.sections:
        starts[section.name] = cursor * 4
        cursor += section.bars
    for role in ROLES:
        role_notes = candidate.notes_by_role.get(role, [])
        if role in {r for section in spec.sections for r in section.active_roles} and not role_notes:
            raise ValueError(f"CANDIDATE_EMPTY_ROLE:{role}")
        ordered = sorted(role_notes, key=lambda n: n.start_time)
        for note in ordered:
            if (
                not 0 <= note.pitch <= 127 or not 1 <= note.velocity <= 127
                or note.duration <= 0 or note.start_time < 0
                or note.start_time + note.duration > spec.duration_bars * 4 + 1e-6
            ):
                raise ValueError("CANDIDATE_NOTE_OUTSIDE_FORM")
            if role == "BASS" and not 24 <= note.pitch <= 60:
                raise ValueError("CANDIDATE_BASS_REGISTER")
        if role == "BASS" and any(
            later.start_time < earlier.start_time + earlier.duration - 1e-6
            for earlier, later in zip(ordered, ordered[1:])
        ):
            raise ValueError("CANDIDATE_BASS_OVERLAP")
        for section in spec.sections:
            begin = starts[section.name]
            end = begin + section.bars * 4
            within = [n for n in ordered if begin <= n.start_time < end]
            if (role in section.active_roles) != bool(within):
                raise ValueError(f"CANDIDATE_SECTION_ROLE_MISMATCH:{section.name}:{role}")
            if any(n.start_time + n.duration > end + 1e-6 for n in within):
                raise ValueError(f"CANDIDATE_NOTE_CROSSES_SECTION:{section.name}:{role}")
    bass_pcs = {n.pitch % 12 for n in candidate.notes_by_role.get("BASS", [])}
    chord_pcs = {n.pitch % 12 for n in candidate.notes_by_role.get("HARMONY", [])}
    if chord_pcs and bass_pcs and not bass_pcs.issubset(chord_pcs | {_minor_root(spec.key)}):
        raise ValueError("CANDIDATE_BASS_HARMONY_CONTRADICTION")
    return candidate.model_copy(update={"structural_checks": [
        "16-bar-form" if spec.duration_bars == 16 else "bounded-form",
        "active-role-coverage", "note-bounds", "bass-monophony",
        "bass-harmony-pitch-classes", "not-a-musical-quality-score",
    ]})


def generate_prompt_groove_candidates(spec: TrackSpec) -> list[SymbolicCandidate]:
    """Generate three auditable candidates from typed intent; caller owns its provenance."""
    if spec.duration_bars != 16 or (spec.meter_numerator, spec.meter_denominator) != (4, 4):
        raise ValueError("V1_REQUIRES_16_BARS_4_4")
    if any(set(s.active_roles) - ROLES for s in spec.sections):
        raise ValueError("V1_UNKNOWN_ROLE")
    if set().union(*(set(s.active_roles) for s in spec.sections)) != ROLES:
        raise ValueError("V1_REQUIRES_KICK_HAT_BASS_HARMONY_HOOK")
    build_arrangement_engine_plan(spec.sections, known_roles=set(ROLES))
    root = _minor_root(spec.key)
    return [_compose(spec, variant, root) for variant in range(3)]


def select_structural_candidate(candidates: list[SymbolicCandidate]) -> tuple[SymbolicCandidate, str]:
    if len(candidates) != 3 or len({c.candidate_id for c in candidates}) != 3:
        raise ValueError("THREE_DISTINCT_CANDIDATES_REQUIRED")
    # Selection is a reproducible structural tie-breaker, not a listening win.
    ranked = sorted(candidates, key=lambda c: (
        -len({round(n.start_time % 4, 3) for n in c.notes_by_role["BASS"]}),
        len(c.notes_by_role["HAT"]), c.candidate_id,
    ))
    return ranked[0], "most distinct bass onset positions; then fewer hat hits; not a sound-quality judgment"
