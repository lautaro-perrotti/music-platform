"""Read-only Astra handoff for MUSIC_ANALYZER_V1."""

from __future__ import annotations

import json
from typing import Any

from copilot.music_model.canonical_view import build_canonical_music_model_view
from copilot.schemas.music_analysis import MusicAnalysisPack
from copilot.schemas.canonical_music_model import CanonicalMusicModelView


ASTRA_DIAGNOSIS = "ASTRA_DIAGNOSIS"
ASTRA_PRODUCER_PLANNER = "ASTRA_PRODUCER_PLANNER"


def build_astra_diagnosis_context(pack: MusicAnalysisPack) -> dict[str, Any]:
    """Give Astra factual reference evidence for interpretation only."""
    return {
        "role": ASTRA_DIAGNOSIS,
        "NO_WRITE": True,
        "REFERENCE_STATE_TOKEN": pack.tokens.reference_state_token,
        "TARGET_STATE_TOKEN": pack.tokens.target_state_token,
        "measurements": pack.model_dump(mode="json"),
        "rule": "MEASURE != DIAGNOSE; every claim must cite supplied measurements",
    }


def build_astra_producer_planner_context(
    pack: MusicAnalysisPack,
    *,
    sample_set_context: dict[str, Any] | None = None,
    diagnosis: dict[str, Any] | None = None,
    music_model: CanonicalMusicModelView | None = None,
) -> dict[str, Any]:
    """Build planner input without turning it into a mutation request."""
    if sample_set_context and sample_set_context.get("NO_WRITE") is False:
        raise ValueError("sample context must be NO_WRITE")
    if music_model is not None:
        identity = music_model.identity
        if (
            identity.get("reference_state_token") != pack.tokens.reference_state_token
            or identity.get("target_state_token") != pack.tokens.target_state_token
        ):
            raise ValueError("CANONICAL_MUSIC_MODEL_TOKEN_MISMATCH")
    canonical = music_model or build_canonical_music_model_view(pack)
    return {
        "role": ASTRA_PRODUCER_PLANNER,
        "NO_WRITE": True,
        "REFERENCE_STATE_TOKEN": pack.tokens.reference_state_token,
        "TARGET_STATE_TOKEN": pack.tokens.target_state_token,
        "diagnosis": diagnosis or {},
        "music_model": canonical.model_dump(mode="json"),
        "sample_set_context": sample_set_context or {},
        "constraints": [
            "Plan from measurements; never invent unavailable values.",
            "The music_model is a read-only view over domain artifacts; preserve its statuses and limitations.",
            "Use the sample shortlist as candidates, not as authorization.",
            "Return a typed MusicPlan for later compiler validation.",
            "No Ableton writes, routing, buses, automation, MIDI editing, or new actions.",
        ],
    }


def build_astra_producer_planner_prompt(context: dict[str, Any]) -> str:
    """Serialize only the grounded planner context for an Astra provider."""
    if context.get("role") != ASTRA_PRODUCER_PLANNER or context.get("NO_WRITE") is not True:
        raise ValueError("invalid producer planner context")
    return (
        "You are ASTRA_PRODUCER_PLANNER. Interpret only the supplied factual evidence. "
        "Do not invent measurements and do not execute writes. Return a typed MusicPlan "
        "candidate whose actions will be validated by ProductionCompiler.\n\n"
        + json.dumps(context, sort_keys=True, separators=(",", ":"))
    )
