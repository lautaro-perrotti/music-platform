"""Producer-aware arrangement decisions.

This module turns validated TrackSpec sections into an auditable arrangement
decision graph.  It is deliberately DAW-free: it computes musical intent and
timeline facts; existing MusicPlan actions and SafeWrite remain the only write
path.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from copilot.musicplan.arrangement import Section
from copilot.producer.track_spec import SectionSpec


ARRANGEMENT_ENGINE_SCHEMA_VERSION = "arrangement-engine-p0"


class EnergyTrend(StrEnum):
    OPEN = "OPEN"
    RISING = "RISING"
    FALLING = "FALLING"
    STABLE = "STABLE"
    PEAK = "PEAK"
    RESET = "RESET"


class ArrangementDecision(BaseModel):
    """One section's producer-facing decision and derived relationships."""

    model_config = ConfigDict(extra="forbid")

    section_name: str = Field(min_length=1, max_length=64)
    start_bar: int = Field(ge=0)
    end_bar: int = Field(gt=0)
    bars: int = Field(gt=0)
    energy: float = Field(ge=0.0, le=1.0)
    energy_delta: float = Field(ge=-1.0, le=1.0)
    trend: EnergyTrend
    active_roles: list[str] = Field(default_factory=list)
    added_roles: list[str] = Field(default_factory=list)
    removed_roles: list[str] = Field(default_factory=list)
    contrast: float = Field(ge=0.0, le=1.0)
    tension: float = Field(ge=0.0, le=1.0)
    release: float = Field(ge=0.0, le=1.0)
    groove_focus: list[str] = Field(default_factory=list)
    variation: str = Field(default="", max_length=240)
    transition: str = Field(default="", max_length=240)


class ArrangementEnginePlan(BaseModel):
    """Pure arrangement output that can be attached to planner evidence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = ARRANGEMENT_ENGINE_SCHEMA_VERSION
    total_bars: int = Field(gt=0)
    decisions: list[ArrangementDecision] = Field(min_length=1, max_length=32)
    rules_applied: list[str] = Field(default_factory=list)

    def to_sections(self) -> list[Section]:
        """Return the legacy DAW-free section values used by existing actions."""

        return [
            Section(
                name=decision.section_name,
                bars=decision.bars,
                active=list(decision.active_roles),
            )
            for decision in self.decisions
        ]


_GROOVE_ROLES = {
    "kick",
    "bass",
    "clap",
    "closed hat",
    "open hat",
    "hat",
    "shaker",
    "conga",
    "clave",
    "perc loop",
    "percussion",
}


def _section_values(section: SectionSpec | Section | dict[str, Any]) -> tuple[str, int, float, list[str], str, str]:
    if isinstance(section, SectionSpec):
        return (
            section.name,
            int(section.bars),
            float(section.energy),
            list(section.active_roles),
            section.variation,
            section.transition,
        )
    if isinstance(section, Section):
        # Legacy sections have no energy metadata.  Preserve their intent and
        # use a neutral planning value rather than pretending it was measured.
        return section.name, int(section.bars), 0.5, list(section.active), "", ""
    if isinstance(section, dict):
        name = str(section.get("name") or "SECTION").strip()
        bars = int(section.get("bars") or 0)
        energy = float(section.get("energy", 0.5))
        active = section.get("active_roles", section.get("active", [])) or []
        return (
            name,
            bars,
            energy,
            [str(item).strip() for item in active if str(item).strip()],
            str(section.get("variation") or ""),
            str(section.get("transition") or ""),
        )
    raise ValueError("ARRANGEMENT_SECTION_UNSUPPORTED")


def _trend(index: int, energy: float, previous: float | None, next_energy: float | None) -> EnergyTrend:
    if index == 0:
        return EnergyTrend.OPEN
    delta = energy - float(previous)
    if next_energy is not None and energy >= float(previous) and energy >= next_energy and energy - float(previous) >= 0.08:
        return EnergyTrend.PEAK
    if delta >= 0.08:
        return EnergyTrend.RISING
    if delta <= -0.08:
        return EnergyTrend.RESET if energy <= 0.35 else EnergyTrend.FALLING
    return EnergyTrend.STABLE


def _unique_roles(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        role = str(value).strip()
        if role and role not in result:
            result.append(role)
    return result


def build_arrangement_engine_plan(
    sections: Iterable[SectionSpec | Section | dict[str, Any]],
    *,
    known_roles: set[str] | None = None,
) -> ArrangementEnginePlan:
    """Compute section relationships without making musical measurements.

    Energy values come from producer intent.  ``contrast`` is a structural
    role-set delta, not an audio similarity judgment.  Unknown roles fail
    closed when a role vocabulary is supplied.
    """

    rows = [_section_values(section) for section in sections]
    if not rows:
        raise ValueError("ARRANGEMENT_EMPTY")
    names = [row[0].casefold() for row in rows]
    if len(names) != len(set(names)):
        raise ValueError("ARRANGEMENT_DUPLICATE_SECTION_NAMES")

    normalized: list[tuple[str, int, float, list[str], str, str]] = []
    for name, bars, energy, active, variation, transition in rows:
        if not 1 <= bars <= 256:
            raise ValueError("ARRANGEMENT_INVALID_BARS")
        if not 0.0 <= energy <= 1.0:
            raise ValueError("ARRANGEMENT_INVALID_ENERGY")
        roles = _unique_roles(active)
        if known_roles is not None:
            unknown = sorted(set(roles) - set(known_roles))
            if unknown:
                raise ValueError(f"ARRANGEMENT_UNKNOWN_ROLES: {', '.join(unknown)}")
        if not roles:
            raise ValueError("ARRANGEMENT_SECTION_HAS_NO_ACTIVE_ROLES")
        normalized.append((name, bars, energy, roles, variation, transition))

    decisions: list[ArrangementDecision] = []
    cursor = 0
    previous_roles: set[str] = set()
    for index, (name, bars, energy, roles, variation, transition) in enumerate(normalized):
        current_roles = set(roles)
        previous_energy = normalized[index - 1][2] if index else None
        next_energy = normalized[index + 1][2] if index + 1 < len(normalized) else None
        delta = 0.0 if previous_energy is None else energy - previous_energy
        added = sorted(current_roles - previous_roles)
        removed = sorted(previous_roles - current_roles)
        union = current_roles | previous_roles
        contrast = 0.0 if not union else (len(set(added) | set(removed)) / len(union))
        trend = _trend(index, energy, previous_energy, next_energy)
        tension = min(1.0, max(0.0, energy + max(delta, 0.0) * 0.5))
        release = min(1.0, max(0.0, (1.0 - energy) + max(-delta, 0.0) * 0.5))
        groove_focus = [role for role in roles if role.casefold() in _GROOVE_ROLES]
        decisions.append(
            ArrangementDecision(
                section_name=name,
                start_bar=cursor,
                end_bar=cursor + bars,
                bars=bars,
                energy=energy,
                energy_delta=delta,
                trend=trend,
                active_roles=roles,
                added_roles=added,
                removed_roles=removed,
                contrast=contrast,
                tension=tension,
                release=release,
                groove_focus=groove_focus,
                variation=variation,
                transition=transition,
            )
        )
        cursor += bars
        previous_roles = current_roles

    return ArrangementEnginePlan(
        total_bars=cursor,
        decisions=decisions,
        rules_applied=[
            "section-boundaries-from-producer-intent",
            "energy-and-role-delta-derived-structural-contrast",
            "groove-focus-is-role-labeling-not-audio-measurement",
            "no-daw-writes",
        ],
    )
