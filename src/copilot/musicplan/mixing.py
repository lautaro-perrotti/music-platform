"""Groovy/Latin tech house mixing + mastering chains (MIXING_V1) — native Ableton devices.

Priority: BALANCE -> GROOVE -> LOW END -> TRANSIENTS -> SPACE -> STEREO -> LOUDNESS.
Static balance first; subtractive EQ before boosts; sidechain creates space (not
pumping); preserve transients; saturation adds harmonics (always level-match).
"""

from __future__ import annotations

from copilot.schemas.session import MixerState, RoutingState, TrackState

# Native device URIs (mock derives the device name from the last path segment).
NATIVE: dict[str, str] = {
    "EQ Eight": "devices/audio-effects/EQ Eight",
    "Compressor": "devices/audio-effects/Compressor",
    "Glue Compressor": "devices/audio-effects/Glue Compressor",
    "Saturator": "devices/audio-effects/Saturator",
    "Drum Buss": "devices/audio-effects/Drum Buss",
    "Utility": "devices/audio-effects/Utility",
    "Echo": "devices/audio-effects/Echo",
    "Hybrid Reverb": "devices/audio-effects/Hybrid Reverb",
    "Auto Filter": "devices/audio-effects/Auto Filter",
    "Limiter": "devices/audio-effects/Limiter",
}

# Per-track native chains (subtle; minimal processing first).
MIXING_CHAINS: dict[str, list[str]] = {
    "Kick": ["EQ Eight", "Drum Buss"],
    "Clap": ["EQ Eight", "Saturator"],
    "Closed Hat": ["EQ Eight"],
    "Shaker": ["EQ Eight", "Saturator"],
    "Conga": ["EQ Eight", "Saturator"],
    "Clave": ["EQ Eight", "Saturator"],
    "Perc Loop": ["EQ Eight", "Drum Buss"],
    "Bass": ["EQ Eight", "Compressor", "Saturator"],
    "Vocal": ["EQ Eight", "Compressor", "Echo"],
    "Stab": ["EQ Eight", "Auto Filter", "Echo"],
    "FX": ["EQ Eight", "Saturator"],
}

# Master bus (simple): EQ -> Glue -> Saturator -> Limiter. Applied on a master track later.
MASTER_CHAIN: list[str] = ["EQ Eight", "Glue Compressor", "Saturator", "Limiter"]

MIXING_PHILOSOPHY: list[str] = [
    "BALANCE -> GROOVE -> LOW END -> TRANSIENTS -> SPACE -> STEREO -> LOUDNESS",
    "static balance first (volume/pan/EQ/basic compression) before complex processing",
    "kick + bass as one system: sidechain creates space, never heavy pumping",
    "low end controlled <120Hz; low-mids 150-400Hz: one element owns each region",
    "percussion hierarchy: main -> secondary -> texture/ghost",
    "preserve transients; saturation adds harmonics (level-match!)",
    "target ~-6dBFS peak headroom on premaster",
    "master = final refinement of a good mix, never a fix for a bad balance",
    "DO NOT SACRIFICE GROOVE FOR LOUDNESS",
]


def _vtrack(name: str) -> TrackState:
    return TrackState(
        stable_id="",
        index=-1,
        name=name,
        role="audio",
        mixer=MixerState(),
        routing=RoutingState(output_type="Main", monitoring="in"),
        devices=[],
        clips=[],
    )


def build_mixing_actions(
    *,
    project_identity: str,
    chains: dict[str, list[str]] | None = None,
) -> list:
    """Generate DEVICE_LOAD actions for the per-track native chains."""
    from copilot.musicplan import build_device_load_action

    chains = chains or MIXING_CHAINS
    actions = []
    for track_name, devices in chains.items():
        for device_name in devices:
            actions.append(
                build_device_load_action(
                    track=_vtrack(track_name),
                    project_identity=project_identity,
                    device_name=device_name,
                    device_uri=NATIVE[device_name],
                    reason=f"mixing: {device_name} on {track_name}",
                    evidence_refs=[],
                )
            )
    return actions
