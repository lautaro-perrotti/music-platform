"""MIX_GROUPS_V1: drum/synth/fx/vocal buses + bus processing.

Grouping replaces per-track-only processing: each element routes to its bus,
and the bus carries the cohesive chain (Drum Buss on drums, etc.).
"""

from __future__ import annotations

from copilot.schemas.session import MixerState, RoutingState, TrackState

# Group -> member tracks (recipe order).
GROUP_DEFS: dict[str, list[str]] = {
    "DRUMS": ["Kick", "Clap", "Closed Hat", "Shaker", "Conga", "Clave", "Perc Loop"],
    "BASS BUS": ["Bass"],
    "SYNTHS": ["Stab"],
    "FX BUS": ["FX"],
    "VOCALS": ["Vocal"],
}

# Bus processing (native, subtle): cohesion, not flattening.
BUS_CHAINS: dict[str, list[str]] = {
    "DRUMS": ["EQ Eight", "Glue Compressor", "Drum Buss"],
    "BASS BUS": ["EQ Eight", "Compressor"],
    "SYNTHS": ["EQ Eight", "Glue Compressor"],
    "FX BUS": ["EQ Eight", "Hybrid Reverb"],
    "VOCALS": ["EQ Eight", "Compressor"],
}


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


def build_group_actions(*, project_identity: str) -> dict[str, list]:
    """Return {'groups': create-track actions, 'routes': routing actions, 'bus': device-load actions}."""
    from copilot.musicplan import (
        build_create_track_action,
        build_device_load_action,
        build_set_track_routing_action,
    )
    from copilot.musicplan.mixing import NATIVE

    groups = [
        build_create_track_action(
            project_identity=project_identity,
            track_name=g,
            track_kind="audio",
            reason=f"group bus {g}",
            evidence_refs=[],
        )
        for g in GROUP_DEFS
    ]
    routes = []
    for g, members in GROUP_DEFS.items():
        for m in members:
            routes.append(
                build_set_track_routing_action(
                    track=_vtrack(m),
                    project_identity=project_identity,
                    routing_type=g,
                    reason=f"route {m} -> {g}",
                    evidence_refs=[],
                )
            )
    bus = []
    for g, devices in BUS_CHAINS.items():
        for d in devices:
            bus.append(
                build_device_load_action(
                    track=_vtrack(g),
                    project_identity=project_identity,
                    device_name=d,
                    device_uri=NATIVE[d],
                    reason=f"bus: {d} on {g}",
                    evidence_refs=[],
                )
            )
    return {"groups": groups, "routes": routes, "bus": bus}
