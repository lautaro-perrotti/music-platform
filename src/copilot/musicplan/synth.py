"""Musical elements & synth design — rhythmic melody + timbre + groove + character.

The musical elements are RHYTHMIC instruments, not melodic content. Native Ableton
synths (Operator/Wavetable/Analog/Simpler) for sound design; ONE strong hook beats
five weak ones; call-and-response; resampling; automation over layers.
"""

from __future__ import annotations

# Native synth roles (design guidance, not sample roles).
SYNTH_TYPES: dict[str, str] = {
    "Operator": "percussive synths, FM plucks, metallic/bell, short envelopes",
    "Wavetable": "warm stabs, dark plucks, mid-bass, filtered leads",
    "Analog": "warm chords, house stabs, organ-like, vintage textures",
    "Simpler/Sampler": "guitar/sax/vocal hits -> rhythmic instruments (resample!)",
}

# Hook processing chains (native, subtle).
HOOK_PROCESSING: dict[str, str] = {
    "house stab": "EQ Eight -> Saturator -> Auto Filter -> Echo",
    "guitar chop": "EQ Eight -> Saturator -> Auto Filter -> Echo -> Hybrid Reverb",
    "sax hit": "EQ Eight -> Compressor -> Saturator -> Auto Filter -> Echo",
    "organ stab": "EQ Eight -> Saturator -> Auto Filter -> Echo -> Hybrid Reverb",
}

SYNTH_PHILOSOPHY: list[str] = [
    "musical elements = rhythmic instruments (RHYTHM + TIMBRE + GROOVE), not melody",
    "ONE STRONG HOOK > FIVE WEAK ONES",
    "rhythm-first: X — X X — X —, then choose pitch",
    "simple harmony: minor / dorian / minor pentatonic / 1-2 chord grooves",
    "call-and-response: guitar <-> conga, vocal <-> sax (never stack them all)",
    "resampling encouraged (freeze/flatten -> Simpler -> new pattern)",
    "automation (filter cutoff / send / pitch) > more layers",
    "midrange: don't stack guitar+sax+synth+vocal; EQ to carve space",
]
