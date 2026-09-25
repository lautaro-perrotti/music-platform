"""Typed musical intent contracts for the producer layer.

This module is deliberately DAW-free.  A TrackSpec is a plan-level contract:
it records what the producer intends to make, not facts measured from audio and
not executable Ableton operations.  The runtime may later translate the
validated sections into canonical MusicPlan actions.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


TRACK_SPEC_SCHEMA_VERSION = "track-spec-v1"


class SectionSpec(BaseModel):
    """One producer-defined section; names are intentionally not genre-fixed."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    bars: int = Field(ge=1, le=256)
    energy: float = Field(ge=0.0, le=1.0)
    active_roles: list[str] = Field(default_factory=list)
    variation: str = Field(default="", max_length=240)
    transition: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def normalize_roles(self) -> "SectionSpec":
        roles: list[str] = []
        for role in self.active_roles:
            normalized = str(role).strip()
            if normalized and normalized not in roles:
                roles.append(normalized)
        self.active_roles = roles
        return self


class TrackSpec(BaseModel):
    """Executable musical intent, independent from provider or DAW details."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = TRACK_SPEC_SCHEMA_VERSION
    title: str = Field(default="Untitled", min_length=1, max_length=160)
    intent: str = Field(default="", max_length=2000)
    bpm: float = Field(ge=40.0, le=240.0)
    key: str | None = Field(default=None, max_length=32)
    meter_numerator: int = Field(default=4, ge=1, le=32)
    meter_denominator: int = Field(default=4, ge=1, le=32)
    duration_bars: int = Field(default=0, ge=0, le=4096)
    vocals: str = Field(default="unspecified", max_length=32)
    style: str = Field(default="", max_length=160)
    primary_hook: str | None = Field(default=None, max_length=160)
    sections: list[SectionSpec] = Field(min_length=1, max_length=32)
    sound_palette: dict[str, list[str]] = Field(default_factory=dict)
    reference_targets: dict[str, Any] = Field(default_factory=dict)
    mix_targets: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_timeline(self) -> "TrackSpec":
        names = [section.name.casefold() for section in self.sections]
        if len(names) != len(set(names)):
            raise ValueError("TRACK_SPEC_DUPLICATE_SECTION_NAMES")
        computed_bars = sum(section.bars for section in self.sections)
        if self.duration_bars and computed_bars != self.duration_bars:
            raise ValueError(
                "TRACK_SPEC_DURATION_MISMATCH: "
                f"declared={self.duration_bars} computed={computed_bars}"
            )
        if not self.duration_bars:
            self.duration_bars = computed_bars
        if not any(section.active_roles for section in self.sections):
            raise ValueError("TRACK_SPEC_HAS_NO_ACTIVE_ROLES")
        self.constraints = [str(item).strip() for item in self.constraints if str(item).strip()]
        return self

    def to_arrangement(self, *, known_roles: set[str] | None = None):
        """Convert plan sections to the existing DAW-free Section value object.

        The conversion performs no writes.  Unknown roles are rejected instead
        of silently dropping producer intent.
        """

        from copilot.musicplan.arrangement import Section

        if known_roles is not None:
            unknown = sorted(
                {
                    role
                    for section in self.sections
                    for role in section.active_roles
                    if role not in known_roles
                }
            )
            if unknown:
                raise ValueError(f"TRACK_SPEC_UNKNOWN_ROLES: {', '.join(unknown)}")
        return [
            Section(name=section.name, bars=section.bars, active=list(section.active_roles))
            for section in self.sections
        ]


def _decode_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    if fenced:
        text = fenced.group(1)
    else:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            text = match.group(0)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("TRACK_SPEC_PAYLOAD_MUST_BE_OBJECT")
    return value


def parse_track_spec(raw: TrackSpec | dict[str, Any] | str) -> TrackSpec:
    """Parse a strict provider result without inventing missing measurements."""

    if isinstance(raw, TrackSpec):
        return raw
    payload = _decode_json_object(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise ValueError("TRACK_SPEC_PAYLOAD_MUST_BE_OBJECT")
    return TrackSpec.model_validate(payload)


def track_spec_from_planner_payload(payload: dict[str, Any]) -> TrackSpec | None:
    """Extract the optional structured contract from an Astra planner response."""

    candidate = payload.get("track_spec")
    if candidate is None:
        return None
    return parse_track_spec(candidate)
