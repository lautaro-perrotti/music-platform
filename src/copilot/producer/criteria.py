"""Model-authored musical intentions, distinct from measured Live facts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from copilot.producer.track_spec import TrackSpec


class ClaimKind(StrEnum):
    ARTISTIC_PREFERENCE = "ARTISTIC_PREFERENCE"
    PERCEPTUAL_ESTIMATE = "PERCEPTUAL_ESTIMATE"


class SectionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_name: str
    perceptual_goal: str = Field(min_length=4)
    lead_role: str
    low_end_owner: str
    space_roles: list[str]
    hook_usage: str = Field(pattern="^(foreground|tease|rest)$")
    energy_rationale: str = Field(min_length=4)
    variation_hypothesis: str = Field(min_length=4)
    claim_kind: ClaimKind
    evidence_refs: list[str] = Field(min_length=1)


class ProducerCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_hook: str = Field(min_length=1)
    hook_role: str = Field(min_length=1)
    sections: list[SectionIntent] = Field(min_length=1)
    uncertainty: str = Field(min_length=4)

    def validate_against(self, spec: TrackSpec, *, selected_digests: set[str]) -> None:
        if self.primary_hook != spec.primary_hook or self.hook_role != spec.hook_role:
            raise ValueError("CRITERIA_HOOK_MISMATCH")
        if [row.section_name for row in self.sections] != [s.name for s in spec.sections]:
            raise ValueError("CRITERIA_SECTION_MISMATCH")
        for row, section in zip(self.sections, spec.sections):
            active = set(section.active_roles)
            if (
                row.lead_role not in active
                or row.low_end_owner not in active
                or row.low_end_owner not in {"Kick", "Bass"}
                or not set(row.space_roles).isdisjoint(active)
                or (row.hook_usage == "rest") != (spec.hook_role not in active)
                or not set(row.evidence_refs).issubset(selected_digests)
            ):
                raise ValueError(f"CRITERIA_SECTION_UNGROUNDED:{section.name}")
