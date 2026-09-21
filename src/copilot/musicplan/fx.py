"""FX design system — white noise, percussive fills, impacts, downlifters, textures.

GROOVE + MOVEMENT + TENSION + RELEASE. FX felt more than noticed; never EDM-style.
"""

from __future__ import annotations

# FX subcategories in the FX BUS.
FX_TYPES: list[str] = ["WHITE_NOISE", "PERCUSSIVE_FILL", "IMPACT", "DOWNLIFTER", "TEXTURE"]

# FX bus processing (native, subtle): EQ -> Glue -> Saturator -> Utility.
FX_BUS_CHAIN: list[str] = ["EQ Eight", "Glue Compressor", "Saturator", "Utility"]

# Dedicated return/send tracks (sends, not per-track inserts).
RETURN_TRACKS: dict[str, str] = {
    "RETURN A": "short room — percussion fills, organic FX",
    "RETURN B": "dark reverb — vocal/transition tails",
    "RETURN C": "rhythmic echo — vocal/percussion throws",
    "RETURN D": "special FX — noise, impacts, transitions",
}

FX_PHILOSOPHY: list[str] = [
    "FX felt more than noticed; groove first, never sacrifice groove for a bigger transition",
    "short + rhythmic + dark + textured + controlled (no EDM risers/impacts)",
    "8/16/32 logic: almost no FX first 16 bars -> small fill -> short sweep -> fill+vocal delay",
    "place on last 1/4, last 1/8, beat 4, or first beat of a new phrase",
    "high-pass aggressively; control white-noise highs; keep low frequencies centered",
    "automation (filter cutoff/volume/send) > more FX",
    "FX bus dynamic; high-pass low-end buildup",
]
