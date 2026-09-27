"""Canonical read-only music-model views and bounded variation intent."""

from copilot.music_model.canonical_view import (
    build_canonical_music_model_view,
    build_canonical_music_model_view_v2,
)
from copilot.music_model.variation_intent import MusicalVariationIntent, build_bass_variation_intent

__all__ = [
    "MusicalVariationIntent",
    "build_bass_variation_intent",
    "build_canonical_music_model_view",
    "build_canonical_music_model_view_v2",
]

