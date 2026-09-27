"""Deterministic, evidence-bound musical variation intent.

This is planning intent, not musical truth and not a DAW write command.  It
keeps the creative operation traceable from the consolidated read view into
the existing VariationPlan/MusicPlan path.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from copilot.schemas.canonical_music_model import CanonicalMusicModelViewV2


class MusicalVariationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "musical-variation-intent-p0"
    intent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    source_reference_id: str | None = None
    source_project_id: str | None = None
    start_qn: float = Field(ge=0)
    length_bars: int = Field(gt=0, le=16)
    source_domains: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    preserve: list[str] = Field(default_factory=list)
    transform: list[str] = Field(default_factory=list)
    reject_if: list[str] = Field(default_factory=list)
    expected_effect: str
    no_write: bool = True


def build_bass_variation_intent(
    view: CanonicalMusicModelViewV2,
    *,
    intent_id: str,
    start_qn: float,
    length_bars: int = 8,
    instruction: str = "",
) -> MusicalVariationIntent:
    """Create one bounded bass intent from the canonical evidence view."""
    if not view.no_write:
        raise ValueError("VARIATION_INTENT_SOURCE_MUST_BE_READ_ONLY")
    if view.bass.status not in {"SUPPORTED", "EVIDENCE_ONLY"}:
        raise ValueError("VARIATION_INTENT_BASS_EVIDENCE_INSUFFICIENT")
    if length_bars not in {8, 16}:
        raise ValueError("VARIATION_INTENT_LENGTH_INVALID")
    refs: list[str] = []
    for domain in (view.bass, view.rhythm, view.groove, view.motifs, view.relationships):
        refs.extend(domain.evidence_refs)
    domain_pairs = (
        ("bass", view.bass),
        ("groove", view.groove),
        ("motifs", view.motifs),
        ("relationships", view.relationships),
    )
    supported_domains = [name for name, domain in domain_pairs if domain.status in {"SUPPORTED", "EVIDENCE_ONLY"}]
    return MusicalVariationIntent(
        intent_id=intent_id,
        role="BASS",
        source_reference_id=view.reference_id,
        source_project_id=view.project_id,
        start_qn=start_qn,
        length_bars=length_bars,
        source_domains=supported_domains,
        evidence_refs=list(dict.fromkeys(refs)),
        preserve=[
            "evidenced_pitch_material",
            "reference_tempo_and_qn_grid",
            "bass_drums_relationship_when_supported",
        ],
        transform=[
            "move_secondary_onsets_within_their_bar",
            "retain_only_authoritative_evidenced_pitches",
            "produce_a_new_editable_sequence_not_an_exact_copy",
        ],
        reject_if=[
            "no_authoritative_bass_events",
            "reference_identity_mismatch",
            "transformation_produces_no_change",
        ],
        expected_effect=instruction.strip() or "one reference-bound bass variation with changed rhythmic placement",
    )


# Product-facing name; keep the historical name as a compatibility alias so
# existing persisted payloads and imports remain stable.
VariationIntent = MusicalVariationIntent

__all__ = ["VariationIntent", "MusicalVariationIntent", "build_bass_variation_intent"]
