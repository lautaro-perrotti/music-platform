"""Groovy/Latin tech house arrangement (ARRANGEMENT_V1).

8/16/32-bar structure, by subtraction/variation (never addition). Each section
declares which tracks are active; everything else is muted. The arrangement
evolves by removing/re-adding elements, keeping the groove hypnotic.
"""

from __future__ import annotations

from dataclasses import dataclass

from copilot.schemas.session import MixerState, RoutingState, TrackState

# Track names in recipe order (matches tech_house.GROOVY_LATIN_GROOVE).
ALL_TRACKS: list[str] = [
    "Kick", "Clap", "Closed Hat", "Shaker", "Conga", "Clave",
    "Perc Loop", "Bass", "Vocal", "Stab", "FX",
]


@dataclass
class Section:
    name: str
    bars: int
    active: list[str]


TECH_HOUSE_ARRANGEMENT: list[Section] = [
    Section("INTRO", 8, ["Conga", "Clave", "Shaker"]),
    Section("GROOVE", 16, ["Kick", "Clap", "Closed Hat", "Shaker", "Conga", "Clave", "Perc Loop"]),
    Section("BASS", 8, ["Kick", "Clap", "Closed Hat", "Shaker", "Conga", "Clave", "Perc Loop", "Bass"]),
    Section("DROP", 32, ["Kick", "Clap", "Closed Hat", "Shaker", "Conga", "Clave", "Perc Loop", "Bass", "Vocal", "Stab", "FX"]),
    Section("BREAK", 8, ["Shaker", "Conga", "Clave", "Vocal"]),
    Section("DROP2", 16, ["Kick", "Clap", "Closed Hat", "Shaker", "Conga", "Clave", "Perc Loop", "Bass", "Vocal", "Stab", "FX"]),
    Section("OUTRO", 8, ["Conga", "Clave", "Shaker"]),
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


def build_arrangement_mute_actions(
    *,
    project_identity: str,
    arrangement: list[Section] | None = None,
) -> list:
    """Generate SET_TRACK_MUTE actions that realize the arrangement.

    For each section, mute the tracks not in `active` and unmute the active ones.
    Emits the full mute state per section (idempotent; safe on a live set).
    """
    from copilot.musicplan import build_set_track_mute_action

    arrangement = arrangement or TECH_HOUSE_ARRANGEMENT
    actions = []
    for sec in arrangement:
        for track_name in ALL_TRACKS:
            mute = track_name not in sec.active
            actions.append(
                build_set_track_mute_action(
                    track=_vtrack(track_name),
                    project_identity=project_identity,
                    mute=mute,
                    reason=f"ARRANGEMENT {sec.name} ({sec.bars} bars): {'mute' if mute else 'active'} {track_name}",
                    evidence_refs=[],
                )
            )
    return actions
