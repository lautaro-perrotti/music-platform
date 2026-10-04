"""Bounded musical decisions from Lucas; never DAW commands or executable code."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from copilot.schemas.session import MidiNote


INTENT = ("Quiero un groove tech-house oscuro, sexy, hipnótico y repetitivo.\n"
          "Tiene que tener un bajo protagonista y muy rítmico, batería con groove,\n"
          "pocas notas pero bien elegidas, stabs cortos y espacio.\n"
          "No quiero que suene electro ni Kraftwerk.\n"
          "Quiero que tenga onda de club, movimiento y tensión sin necesidad\n"
          "de una melodía grande.")
Role = Literal["kick", "clap", "closed_hat", "open_hat", "perc", "bass", "stab"]
Degree = Literal["1", "b3", "4", "5", "b6", "6", "b7"]
Dynamic = Literal["ghost", "normal", "accent"]
Timing = Literal["early", "on_grid", "late"]
ROOT_MIDI = {"F": 41, "F#": 42, "G": 43, "G#": 44, "A": 45, "Bb": 46}
DEGREE_SEMITONES = {"1": 0, "b3": 3, "4": 5, "5": 7, "b6": 8, "6": 9, "b7": 10}


class StrictPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tonality(StrictPlanModel):
    root: Literal["F", "F#", "G", "G#", "A", "Bb"]
    mode: Literal["minor", "dorian"]
    bass_root_midi: int
    reason: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def check_root(self) -> "Tonality":
        # Ableton's octave labels differ from concert-pitch naming. Both the
        # 29-34 sub register and 41-46 upper register are valid, provided the
        # pitch class agrees with the root Lucas chose.
        if (not 29 <= self.bass_root_midi <= 46
                or self.bass_root_midi % 12 != ROOT_MIDI[self.root] % 12):
            raise ValueError("bass_root_midi must match chosen root in bounded bass register")
        return self


class Character(StrictPlanModel):
    darkness: int = Field(ge=0, le=5)
    groove: int = Field(ge=0, le=5)
    density: int = Field(ge=0, le=5)
    repetition: int = Field(ge=0, le=5)
    tension: int = Field(ge=0, le=5)


class Swing(StrictPlanModel):
    amount_beats: float = Field(ge=0, le=.045)
    roles: list[Literal["closed_hat", "open_hat", "perc", "bass"]] = Field(max_length=4)


class Section(StrictPlanModel):
    start_bar: int = Field(ge=1, le=16)
    end_bar: int = Field(ge=1, le=16)
    energy: float = Field(ge=0, le=1)
    active_roles: list[Role] = Field(min_length=2, max_length=7)
    variation_notes: str = Field(min_length=1, max_length=200)


class Hit(StrictPlanModel):
    offset: float = Field(ge=0, lt=4)
    duration: float = Field(gt=0, le=.5)
    dynamic: Dynamic
    timing: Timing


class BassHit(Hit):
    degree: Degree
    octave_shift: Literal[-1, 0, 1] = 0


class StabHit(Hit):
    degrees: list[Degree] = Field(min_length=1, max_length=4)
    register_octave: Literal[1, 2] = 1


class Omission(StrictPlanModel):
    bar: int = Field(ge=1, le=16)
    offset: float = Field(ge=0, lt=4)


class DrumAddition(StrictPlanModel):
    bar: int = Field(ge=1, le=16)
    event: Hit


class BassAddition(StrictPlanModel):
    bar: int = Field(ge=1, le=16)
    event: BassHit


class StabAddition(StrictPlanModel):
    bar: int = Field(ge=1, le=16)
    event: StabHit


class DrumPattern(StrictPlanModel):
    motif: list[Hit] = Field(min_length=1, max_length=8)
    omissions: list[Omission] = Field(default_factory=list, max_length=12)
    additions: list[DrumAddition] = Field(default_factory=list, max_length=12)


class BassPattern(StrictPlanModel):
    motif: list[BassHit] = Field(min_length=2, max_length=6)
    omissions: list[Omission] = Field(default_factory=list, max_length=12)
    additions: list[BassAddition] = Field(default_factory=list, max_length=12)
    concept: str = Field(min_length=1, max_length=240)


class StabPattern(StrictPlanModel):
    motif: list[StabHit] = Field(min_length=1, max_length=2)
    omissions: list[Omission] = Field(default_factory=list, max_length=12)
    additions: list[StabAddition] = Field(default_factory=list, max_length=12)
    concept: str = Field(min_length=1, max_length=240)


class Roles(StrictPlanModel):
    kick_transition_omit_bar: int | None = Field(default=None, ge=1, le=16)
    clap_pickup_bars: list[int] = Field(default_factory=list, max_length=4)
    closed_hat: DrumPattern
    open_hat: DrumPattern
    perc: DrumPattern
    bass: BassPattern
    stab: StabPattern


class Rationale(StrictPlanModel):
    bass: str = Field(min_length=1, max_length=240)
    groove: str = Field(min_length=1, max_length=240)
    sections: str = Field(min_length=1, max_length=240)
    stab: str = Field(min_length=1, max_length=240)


class ProducerPlanV1(StrictPlanModel):
    intent: str
    tempo: Literal[126]
    bars: Literal[16]
    meter: Literal["4/4"]
    tonality: Tonality
    character: Character
    swing: Swing
    sections: list[Section] = Field(min_length=1, max_length=5)
    roles: Roles
    rationale: Rationale

    @model_validator(mode="after")
    def check_plan(self) -> "ProducerPlanV1":
        if self.intent != INTENT:
            raise ValueError("intent must preserve the exact human input")
        cursor = 1
        for section in self.sections:
            if section.start_bar != cursor or section.end_bar < cursor:
                raise ValueError("sections must cover bars 1-16 contiguously")
            if len(section.active_roles) != len(set(section.active_roles)):
                raise ValueError("duplicate active role")
            if not {"kick", "clap", "bass"}.issubset(section.active_roles):
                raise ValueError("kick, clap and bass must remain active")
            cursor = section.end_bar + 1
        if cursor != 17:
            raise ValueError("sections must end at bar 16")
        if any(not 1 <= bar <= 16 for bar in self.roles.clap_pickup_bars):
            raise ValueError("clap pickup bar out of bounds")
        if self.tonality.mode == "minor" and any(
            hit.degree == "6" for hit in self.roles.bass.motif
        ):
            raise ValueError("natural sixth requires dorian mode")
        return self


def degree_to_midi(root_midi: int, degree: Degree, octave_shift: int = 0) -> int:
    pitch = root_midi + DEGREE_SEMITONES[degree] + 12 * octave_shift
    if not 29 <= pitch <= 84:
        raise ValueError("rendered pitch outside bounded register")
    return pitch


def _timed_offset(role: str, hit: Hit, swing: Swing) -> float:
    micro = {"early": -.012, "on_grid": 0.0, "late": .012}[hit.timing]
    weak_sixteenth = abs((hit.offset % .5) - .25) < .001
    swing_offset = swing.amount_beats if role in swing.roles and weak_sixteenth else 0.0
    result = micro + swing_offset
    if not -.035 <= result <= .045:
        raise ValueError(f"microtiming out of bounds for {role}")
    return result


def _velocity(role: str, dynamic: Dynamic) -> int:
    values = {"ghost": 56, "normal": 86, "accent": 108}
    if role == "bass":
        values = {"ghost": 62, "normal": 98, "accent": 112}
    elif role == "stab":
        values = {"ghost": 52, "normal": 78, "accent": 96}
    return values[dynamic]


def _events_for_bar(pattern: DrumPattern | BassPattern | StabPattern, bar: int) -> list[Hit]:
    omitted = {round(item.offset, 6) for item in pattern.omissions if item.bar == bar}
    events = [event for event in pattern.motif if round(event.offset, 6) not in omitted]
    events.extend(item.event for item in pattern.additions if item.bar == bar)
    return sorted(events, key=lambda event: event.offset)


def render_producer_plan_v1(plan: ProducerPlanV1) -> dict[str, list[MidiNote]]:
    """Expand Lucas's decisions mechanically; no creative fallback or randomness."""
    out: dict[str, list[MidiNote]] = {role: [] for role in Role.__args__}
    sections = {bar: section for section in plan.sections
                for bar in range(section.start_bar, section.end_bar + 1)}
    for bar in range(1, 17):
        base = (bar - 1) * 4
        active = set(sections[bar].active_roles)
        if "kick" in active:
            for beat in range(4):
                if bar == plan.roles.kick_transition_omit_bar and beat == 3:
                    continue
                out["kick"].append(MidiNote(pitch=60, start_time=base + beat,
                                            duration=.25, velocity=116))
        if "clap" in active:
            for beat in (1, 3):
                out["clap"].append(MidiNote(pitch=60, start_time=base + beat,
                                            duration=.25, velocity=106))
            if bar in plan.roles.clap_pickup_bars:
                out["clap"].append(MidiNote(pitch=60, start_time=base + 3.75,
                                            duration=.18, velocity=56))
        for role in ("closed_hat", "open_hat", "perc", "bass", "stab"):
            if role not in active:
                continue
            pattern = getattr(plan.roles, role)
            events = _events_for_bar(pattern, bar)
            if role == "bass" and not 2 <= len(events) <= 6:
                raise ValueError(f"bass density out of bounds in bar {bar}")
            if role == "stab" and len(events) > 2:
                raise ValueError(f"stab density out of bounds in bar {bar}")
            for event in events:
                onset = round(base + event.offset + _timed_offset(role, event, plan.swing), 6)
                pitches = [60]
                if role == "bass":
                    pitches = [degree_to_midi(plan.tonality.bass_root_midi,
                                             event.degree, event.octave_shift)]
                elif role == "stab":
                    pitches = [degree_to_midi(plan.tonality.bass_root_midi,
                                              degree, event.register_octave)
                               for degree in event.degrees]
                if not 0 <= onset < 64 or onset + event.duration > 64.000001:
                    raise ValueError(f"note outside 16 bars: {role} bar {bar}")
                for pitch in pitches:
                    out[role].append(MidiNote(pitch=pitch, start_time=onset,
                                              duration=event.duration,
                                              velocity=_velocity(role, event.dynamic)))
    if any(len(notes) > 512 for notes in out.values()):
        raise ValueError("too many notes for direct Live clip")
    return out


def render_summary(rendered: dict[str, list[MidiNote]]) -> dict:
    return {role: {"count": len(notes), "pitches": dict(Counter(n.pitch for n in notes)),
                   "onsets": [n.start_time for n in notes]}
            for role, notes in rendered.items()}
