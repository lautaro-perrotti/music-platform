"""Read-only comparison boundary for symbolic generation artifacts.

An ABC score or MIDI file is not automatically a multitrack Ableton plan.
Only explicitly evidenced, independently editable roles count as such.
This module never invokes a model or a DAW and never creates a MusicPlan.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from copilot.producer.prompt_groove_v1 import SymbolicCandidate
from copilot.producer.track_spec import TrackSpec
from copilot.schemas.session import MidiNote


class RepresentationType(StrEnum):
    MIDI_EVENTS = "MIDI_EVENTS"
    MIDI_FILE = "MIDI_FILE"
    ABC_SCORE = "ABC_SCORE"
    AUDIO = "AUDIO"


class GeneratedLane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)
    representation_type: RepresentationType
    independently_editable: bool = False
    notes: list[MidiNote] = Field(default_factory=list)
    file: Path | None = None
    sha256: str | None = None
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence(self) -> "GeneratedLane":
        if self.representation_type == RepresentationType.MIDI_EVENTS:
            if not self.notes or self.file is not None or self.sha256 is not None:
                raise ValueError("MIDI_EVENTS_REQUIRE_NOTES_ONLY")
        elif self.notes or self.file is None or self.sha256 is None:
            raise ValueError("FILE_REPRESENTATION_REQUIRES_FILE_AND_HASH")
        if self.representation_type == RepresentationType.AUDIO and self.independently_editable:
            raise ValueError("AUDIO_IS_NOT_SYMBOLICALLY_EDITABLE")
        if self.role == "UNMAPPED" and self.independently_editable:
            raise ValueError("UNMAPPED_ROLE_IS_NOT_INDEPENDENTLY_EDITABLE")
        return self


class ArrangementSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_bar: int = Field(ge=0)
    end_bar: int = Field(gt=0)
    active_roles: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_bounds(self) -> "ArrangementSpan":
        if self.end_bar <= self.start_bar:
            raise ValueError("ARRANGEMENT_SPAN_EMPTY")
        return self


class GenerationBundle(BaseModel):
    """Observed output, not an assertion that the generator met the brief."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "generation-bundle-v1"
    prompt: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    output_id: str = Field(min_length=1)
    tempo_bpm: float | None = Field(default=None, gt=0)
    meter: str | None = None
    bars: int | None = Field(default=None, gt=0)
    key: str | None = None
    tracks: list[GeneratedLane] = Field(min_length=1)
    arrangement: list[ArrangementSpan] = Field(default_factory=list)
    audio_preview: Path | None = None
    provenance: list[str] = Field(min_length=1)
    musical_winner: None = None
    no_ableton_access: bool = True

    @model_validator(mode="after")
    def validate_structure(self) -> "GenerationBundle":
        if self.schema_version != "generation-bundle-v1":
            raise ValueError("UNKNOWN_GENERATION_BUNDLE_VERSION")
        if self.arrangement and self.bars is None:
            raise ValueError("ARRANGEMENT_REQUIRES_BAR_COUNT")
        roles = {lane.role for lane in self.tracks if lane.role != "UNMAPPED"}
        for span in self.arrangement:
            if span.end_bar > self.bars or not set(span.active_roles).issubset(roles):
                raise ValueError("ARRANGEMENT_OUTSIDE_EVIDENCED_ROLES_OR_BARS")
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def from_our_symbolic_candidate(
    *, prompt: str, spec: TrackSpec, candidate: SymbolicCandidate,
) -> GenerationBundle:
    """Adapt existing in-memory notes without calling Lucas or Ableton."""
    if candidate.duration_bars != spec.duration_bars or candidate.tempo_bpm != spec.bpm:
        raise ValueError("CANDIDATE_TRACK_SPEC_MISMATCH")
    lanes = [
        GeneratedLane(role=role, representation_type=RepresentationType.MIDI_EVENTS,
                      independently_editable=True, notes=notes,
                      provenance=f"symbolic_candidate:{candidate.candidate_id}:{role}")
        for role, notes in sorted(candidate.notes_by_role.items()) if notes
    ]
    arrangement = []
    cursor = 0
    for section in spec.sections:
        arrangement.append(ArrangementSpan(
            start_bar=cursor, end_bar=cursor + section.bars,
            active_roles=section.active_roles,
        ))
        cursor += section.bars
    return GenerationBundle(
        prompt=prompt, provider="OUR_SYMBOLIC", model="prompt_groove_v1",
        output_id=candidate.candidate_id, tempo_bpm=spec.bpm,
        meter=f"{spec.meter_numerator}/{spec.meter_denominator}",
        bars=spec.duration_bars, key=spec.key, tracks=lanes,
        arrangement=arrangement,
        provenance=["in_memory_symbolic_candidate", "no_model_or_daw_call"],
    )


def from_external_symbolic_file(
    *, prompt: str, provider: str, model: str, output_id: str,
    path: Path, representation_type: RepresentationType,
    provenance: str,
) -> GenerationBundle:
    """Import a real worker artifact without inferring unsupported roles.

    The source file is hashed, not copied or executed. ABC/MIDI parsing and
    role mapping require a later explicit, tested adapter.
    """
    if provider not in {"YUE2", "MUSECOCO"}:
        raise ValueError("EXTERNAL_SYMBOLIC_PROVIDER_UNSUPPORTED")
    if representation_type not in {RepresentationType.ABC_SCORE, RepresentationType.MIDI_FILE}:
        raise ValueError("EXTERNAL_SYMBOLIC_REPRESENTATION_UNSUPPORTED")
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("EXTERNAL_SYMBOLIC_ARTIFACT_MISSING_OR_EMPTY")
    return GenerationBundle(
        prompt=prompt, provider=provider, model=model, output_id=output_id,
        tracks=[GeneratedLane(
            role="UNMAPPED", representation_type=representation_type,
            independently_editable=False, file=path.resolve(),
            sha256=_sha256(path), provenance=provenance,
        )],
        provenance=[provenance, "roles_not_inferred_from_file_extension"],
    )


def compare_editability(bundles: list[GenerationBundle]) -> list[dict[str, object]]:
    """Structural facts only; no ranking or claim of musical quality."""
    if len({(bundle.provider, bundle.output_id) for bundle in bundles}) != len(bundles):
        raise ValueError("DUPLICATE_PROVIDER_OUTPUT")
    prompts = {bundle.prompt for bundle in bundles}
    if len(prompts) != 1:
        raise ValueError("BENCHMARK_PROMPT_MISMATCH")
    rows = []
    for bundle in bundles:
        for lane in bundle.tracks:
            if lane.file is not None and (not lane.file.is_file() or _sha256(lane.file) != lane.sha256):
                raise ValueError("SYMBOLIC_ARTIFACT_MISSING_OR_CHANGED")
        editable = sorted({lane.role for lane in bundle.tracks if lane.independently_editable})
        rows.append({
            "provider": bundle.provider,
            "model": bundle.model,
            "output_id": bundle.output_id,
            "representations": sorted({lane.representation_type.value for lane in bundle.tracks}),
            "observed_lanes": len(bundle.tracks),
            "tempo_bpm": bundle.tempo_bpm,
            "meter": bundle.meter,
            "bars": bundle.bars,
            "key": bundle.key,
            "independently_editable_roles": editable,
            "editable_role_count": len(editable),
            "symbolic_artifact_count": sum(
                lane.representation_type != RepresentationType.AUDIO for lane in bundle.tracks
            ),
            "arrangement_spans": len(bundle.arrangement),
            "audio_preview_present": bundle.audio_preview is not None,
            "musical_quality": "HUMAN_EVALUATION_PENDING",
        })
    return rows
