"""Arrangement timeline builder: duplicate session clips into the Arrangement.

Uses Track.duplicate_clip_to_arrangement (Ableton LOM) to place each active
track's clip into the timeline over the section structure — deterministically,
with no recording. Clips are first looped to their rhythmic division (kick =
1 beat for four-on-the-floor, clap = 2 beats, hats = 0.5 beats, everything
else = 1 bar), then tiled one bar at a time across their section.
"""

from __future__ import annotations

from typing import Any

from copilot.musicplan.arrangement import TECH_HOUSE_ARRANGEMENT, Section

# Rhythmic division (beats) per element, for one-shot samples that need a
# repeating pattern. MIDI percussion already carries its own pattern (1 bar).
LOOP_DIVISION: dict[str, float] = {
    "Kick": 1.0,        # four-on-the-floor
    "Clap": 2.0,        # on 2 & 4
    "Closed Hat": 0.5,  # offbeat 8ths
}


def section_timeline(arrangement: list[Section] | None = None) -> list[dict[str, Any]]:
    """Compute beat offsets + lengths for each section (4/4, 4 beats per bar)."""
    arrangement = arrangement or TECH_HOUSE_ARRANGEMENT
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for sec in arrangement:
        length_beats = float(sec.bars * 4)
        timeline.append(
            {
                "name": sec.name,
                "bars": sec.bars,
                "start_beat": cursor,
                "length_beats": length_beats,
                "active": list(sec.active),
            }
        )
        cursor += length_beats
    return timeline


def _loop_division(track_name: str) -> float:
    return LOOP_DIVISION.get(track_name, 4.0)


def build_arrangement(
    daw,
    *,
    session,
    arrangement: list[Section] | None = None,
) -> dict[str, Any]:
    """Loop clips to their rhythmic division, then tile them across the timeline.

    `session` is the current SessionState (tracks carry name/index/clips).
    Returns a report with counts; failures are collected, not fatal.
    """
    arrangement = arrangement or TECH_HOUSE_ARRANGEMENT
    by_name = {t.name: t for t in session.tracks}
    timeline = section_timeline(arrangement)
    placed: list[dict[str, Any]] = []
    looped: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    unmuted: int = 0

    # 0) unmute every element track: the timeline (not the session mute state)
    #    determines what plays in each section.
    for t in by_name.values():
        if not t.clips:
            continue
        try:
            daw.set_track_mute(int(t.index), False)
            unmuted += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"unmute {t.name}: {exc}")

    # 1) loop each clip once to its rhythmic division
    for name, t in by_name.items():
        if not t.clips:
            continue
        div = _loop_division(name)
        try:
            daw.set_clip_loop(int(t.index), 0, 0.0, div, True)
            looped.append(f"{name}:{div}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"loop {name}: {exc}")

    # 2) tile each active clip across its sections (per-bar copies)
    for sec in timeline:
        for track_name in sec["active"]:
            t = by_name.get(track_name)
            if t is None or not t.clips:
                skipped.append(f"{sec['name']}:{track_name}")
                continue
            try:
                r = daw.duplicate_clip_to_arrangement(
                    int(t.index), 0, sec["start_beat"], sec["length_beats"]
                )
                placed.append(
                    {
                        "section": sec["name"],
                        "track": track_name,
                        "start_beat": sec["start_beat"],
                        "copies": r.get("copies"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{sec['name']}:{track_name}: {exc}")

    return {
        "placed": len(placed),
        "looped": len(looped),
        "unmuted": unmuted,
        "skipped": skipped,
        "errors": errors,
        "timeline": timeline,
    }
