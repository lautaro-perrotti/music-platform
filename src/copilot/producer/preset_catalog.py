"""Provider-neutral preset-first selection for producer intent.

The catalog describes available presets; it does not inspect or mutate a DAW.
Selection is deterministic and fail-closed when no role-compatible preset is
available. Applying the selected URI remains an existing Core/SafeWrite job.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


PRESET_CATALOG_SCHEMA_VERSION = "preset-catalog-v1"


class PresetCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str = Field(min_length=1, max_length=2048)
    name: str = Field(min_length=1, max_length=240)
    plugin: str = Field(min_length=1, max_length=120)
    roles: list[str] = Field(min_length=1, max_length=16)
    tags: list[str] = Field(default_factory=list, max_length=64)
    macro_targets: dict[str, float] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class PresetSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=80)
    desired_tags: list[str] = Field(default_factory=list, max_length=32)
    limit: int = Field(default=3, ge=1, le=16)


class PresetSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = PRESET_CATALOG_SCHEMA_VERSION
    status: str
    request: PresetSelectionRequest
    candidates: list[PresetCandidate] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    writes_authorized: int = 0


class PresetCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = PRESET_CATALOG_SCHEMA_VERSION
    presets: list[PresetCandidate] = Field(default_factory=list)

    def select(self, request: PresetSelectionRequest) -> PresetSelection:
        """Return a deterministic shortlist; never choose across roles silently."""

        plugin = request.plugin.casefold().strip()
        role = request.role.casefold().strip()
        wanted_tags = {tag.casefold().strip() for tag in request.desired_tags if tag.strip()}
        ranked: list[tuple[float, PresetCandidate]] = []
        for candidate in self.presets:
            if candidate.plugin.casefold().strip() != plugin:
                continue
            candidate_roles = {item.casefold().strip() for item in candidate.roles}
            if role not in candidate_roles:
                continue
            candidate_tags = {item.casefold().strip() for item in candidate.tags}
            tag_hits = len(wanted_tags & candidate_tags)
            score = 1.0 + (0.1 * tag_hits)
            ranked.append((score, candidate))

        ranked.sort(key=lambda row: (-row[0], row[1].name.casefold(), row[1].uri))
        chosen = ranked[: request.limit]
        if not chosen:
            return PresetSelection(
                status="UNAVAILABLE",
                request=request,
                reasons=["NO_ROLE_COMPATIBLE_PRESET"],
            )
        return PresetSelection(
            status="SELECTED",
            request=request,
            candidates=[candidate for _, candidate in chosen],
            scores=[score for score, _ in chosen],
            reasons=[
                "ROLE_MATCH_REQUIRED",
                "TAG_MATCH_USED_AS_TIEBREAKER",
                "PRESET_FIRST_NO_INIT_PATCH",
            ],
        )


def catalog_from_payload(payload: dict[str, Any]) -> PresetCatalog:
    """Parse a persisted catalog without inventing missing preset metadata."""

    if not isinstance(payload, dict):
        raise ValueError("PRESET_CATALOG_PAYLOAD_MUST_BE_OBJECT")
    return PresetCatalog.model_validate(payload)
