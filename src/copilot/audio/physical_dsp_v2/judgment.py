"""Fail-closed scan for musical judgment language. MEASURE only."""

from __future__ import annotations

import re

from copilot.schemas.dsp import DspBundle, DspObservation

FORBIDDEN_PHRASES = (
    "muddy",
    "professional",
    "weak drop",
    "bad kick",
    "needs compression",
    "needs eq",
    "groove problem",
    "dropout problem",
    "needs fixing",
    "masking",
    "boring",
    "good groove",
    "bad groove",
    "human feel",
    "robotic",
    "overcompressed",
    "too loud",
    "too quiet",
    "music flamingo",
    "weak mix",
)

FORBIDDEN_WORDS_RE = re.compile(
    r"\b(bad|good|masking|muddy|professional|boring|robotic)\b",
    re.IGNORECASE,
)
FORBIDDEN_PHRASE_RE = re.compile(
    "|".join(re.escape(p) for p in FORBIDDEN_PHRASES),
    re.IGNORECASE,
)


def _human_text(obs: DspObservation) -> str:
    parts = [
        obs.analyzer_id,
        obs.subject.label or "",
        obs.subject.source_id or "",
        obs.quality.value,
        *[lim.code for lim in obs.limitations],
        *[lim.detail for lim in obs.limitations],
        *[item.name for item in obs.values],
        obs.provenance.method,
        obs.provenance.provider_id,
    ]
    return " ".join(str(p) for p in parts)


def assert_no_judgment(obs: DspObservation) -> None:
    blob = _human_text(obs)
    hit = FORBIDDEN_PHRASE_RE.search(blob) or FORBIDDEN_WORDS_RE.search(blob)
    if hit:
        raise ValueError(f"PHYSICAL_DSP_V2 leaked judgment language: {hit.group(0)}")


def assert_bundle_no_judgment(bundle: DspBundle) -> None:
    for obs in bundle.observations:
        assert_no_judgment(obs)
    extra = " ".join(lim.code + " " + lim.detail for lim in bundle.limitations)
    hit = FORBIDDEN_PHRASE_RE.search(extra) or FORBIDDEN_WORDS_RE.search(extra)
    if hit:
        raise ValueError(f"PHYSICAL_DSP_V2 leaked judgment language: {hit.group(0)}")
