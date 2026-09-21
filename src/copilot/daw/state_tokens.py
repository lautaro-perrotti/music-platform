"""Observable state tokens. Not a local mutation counter.

PROJECT / AUDIBLE / TARGET are derived from a canonical snapshot. Same
semantic state → byte-identical JSON → same SHA-256. Volatile fields
(playback cursor, selection, runtime IDs, timestamps) are excluded.

Integer session.revision remains an in-process observation counter derived
from these tokens. Plans and cache keys must use the tokens, not the integer.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from copilot.schemas.session import SessionState, TrackState

CANON_VERSION = "state-canon-1"
FLOAT_PLACES = 6


class StateScope(StrEnum):
    PROJECT = "PROJECT"
    AUDIBLE = "AUDIBLE"
    TARGET = "TARGET"


def _q(value: float) -> float:
    return round(float(value), FLOAT_PLACES)


def dumps_canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def token_of(payload: dict[str, Any]) -> str:
    return hashlib.sha256(dumps_canonical(payload)).hexdigest()


def _notes(clip: Any) -> list[dict[str, Any]]:
    rows = []
    for note in clip.notes:
        rows.append(
            {
                "pitch": int(note.pitch),
                "start_time": _q(note.start_time),
                "duration": _q(note.duration),
                "velocity": int(note.velocity),
                "mute": bool(note.mute),
            }
        )
    rows.sort(
        key=lambda row: (
            row["start_time"],
            row["pitch"],
            row["duration"],
            row["velocity"],
            row["mute"],
        )
    )
    return rows


def _devices_structural(track: TrackState) -> list[dict[str, Any]]:
    rows = []
    for device in track.devices:
        rows.append(
            {
                "index": int(device.index),
                "name": device.name,
                "class_name": device.class_name,
                "enabled": bool(device.enabled),
            }
        )
    rows.sort(key=lambda row: row["index"])
    return rows


def _devices_audible(track: TrackState) -> list[dict[str, Any]]:
    rows = []
    for device in track.devices:
        params = []
        for parameter in device.parameters:
            params.append(
                {
                    "index": int(parameter.index),
                    "name": parameter.name,
                    "value": _q(parameter.value),
                }
            )
        params.sort(key=lambda row: row["index"])
        rows.append(
            {
                "index": int(device.index),
                "name": device.name,
                "class_name": device.class_name,
                "enabled": bool(device.enabled),
                "parameters": params,
            }
        )
    rows.sort(key=lambda row: row["index"])
    return rows


def _clips_structural(track: TrackState) -> list[dict[str, Any]]:
    rows = []
    for clip in track.clips:
        rows.append(
            {
                "slot_index": int(clip.slot_index),
                "name": clip.name,
                "length_beats": _q(clip.length_beats),
                "is_midi": bool(clip.is_midi),
                "note_count": len(clip.notes),
            }
        )
    rows.sort(key=lambda row: row["slot_index"])
    return rows


def _clips_audible(track: TrackState) -> list[dict[str, Any]]:
    rows = []
    for clip in track.clips:
        rows.append(
            {
                "slot_index": int(clip.slot_index),
                "name": clip.name,
                "length_beats": _q(clip.length_beats),
                "is_midi": bool(clip.is_midi),
                "notes": _notes(clip),
            }
        )
    rows.sort(key=lambda row: row["slot_index"])
    return rows


def _routing(track: TrackState) -> dict[str, Any]:
    routing = getattr(track, "routing", None)
    if routing is None:
        return {
            "input_type": "",
            "input_channel": "",
            "output_type": "",
            "output_channel": "",
            "monitoring": "",
        }
    return {
        "input_type": str(getattr(routing, "input_type", "") or ""),
        "input_channel": str(getattr(routing, "input_channel", "") or ""),
        "output_type": str(getattr(routing, "output_type", "") or ""),
        "output_channel": str(getattr(routing, "output_channel", "") or ""),
        "monitoring": str(getattr(routing, "monitoring", "") or ""),
    }


def _sends(track: TrackState) -> list[dict[str, Any]]:
    rows = []
    for send in getattr(track, "sends", None) or []:
        rows.append(
            {
                "index": int(getattr(send, "index", 0)),
                "name": str(getattr(send, "name", "") or ""),
                "value": _q(float(getattr(send, "value", 0.0))),
            }
        )
    rows.sort(key=lambda row: row["index"])
    return rows


def _mixer(track: TrackState) -> dict[str, Any]:
    mixer = track.mixer
    return {
        "volume": _q(mixer.volume),
        "pan": _q(mixer.pan),
        "mute": bool(mixer.mute),
        "solo": bool(mixer.solo),
        "arm": bool(mixer.arm),
    }


def canonical_project(session: SessionState) -> dict[str, Any]:
    """Structural state for identity and plans. Session order is part of structure."""
    tracks = []
    for track in session.tracks:
        tracks.append(
            {
                "name": track.name,
                "role": track.role,
                "grouped": bool(getattr(track, "grouped", False)),
                "foldable": bool(getattr(track, "foldable", False)),
                "routing": _routing(track),
                "devices": _devices_structural(track),
                "clips": _clips_structural(track),
            }
        )
    return {
        "canon": CANON_VERSION,
        "scope": StateScope.PROJECT.value,
        "tempo": _q(session.transport.tempo),
        "signature_numerator": int(session.transport.signature_numerator),
        "signature_denominator": int(session.transport.signature_denominator),
        "tracks": tracks,
    }


def _audible_track_body(track: TrackState) -> dict[str, Any]:
    return {
        "role": track.role,
        "mixer": _mixer(track),
        "routing": _routing(track),
        "sends": _sends(track),
        "devices": _devices_audible(track),
        "clips": _clips_audible(track),
    }


def canonical_audible(
    session: SessionState, *, track_names: frozenset[str] | None = None
) -> dict[str, Any]:
    """State that can change captured audio. Track order and names are excluded.

    Session-level (track_names is None) is conservative: every track in the
    set can feed Main. Scoped isolate uses only the named sources.
    """
    selected = session.tracks
    if track_names is not None:
        selected = [track for track in session.tracks if track.name in track_names]
    bodies = [_audible_track_body(track) for track in selected]
    bodies.sort(key=lambda row: dumps_canonical(row))
    return {
        "canon": CANON_VERSION,
        "scope": StateScope.AUDIBLE.value,
        "sources": None if track_names is None else sorted(track_names),
        "tempo": _q(session.transport.tempo),
        "signature_numerator": int(session.transport.signature_numerator),
        "signature_denominator": int(session.transport.signature_denominator),
        "tracks": bodies,
    }


def canonical_target(track: TrackState) -> dict[str, Any]:
    """State relevant to one object. No index. No runtime id."""
    return {
        "canon": CANON_VERSION,
        "scope": StateScope.TARGET.value,
        "name": track.name,
        "role": track.role,
        "grouped": bool(getattr(track, "grouped", False)),
        "foldable": bool(getattr(track, "foldable", False)),
        "mixer": _mixer(track),
        "routing": _routing(track),
        "sends": _sends(track),
        "devices": _devices_audible(track),
        "clips": _clips_audible(track),
    }


def project_token(session: SessionState) -> str:
    return token_of(canonical_project(session))


def audible_token(
    session: SessionState, *, track_names: frozenset[str] | None = None
) -> str:
    return token_of(canonical_audible(session, track_names=track_names))


def target_token(track: TrackState) -> str:
    return token_of(canonical_target(track))


def project_identity_payload(
    session: SessionState,
    *,
    path: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """How we know which Live Set this is.

    Prefer Live's file path when present. Untitled/unsaved sets fall back to a
    structural fingerprint. That fingerprint can collide across similar
    templates — documented as LIMITED, not a Live UUID.
    """
    if path:
        return {
            "kind": "live_set_path",
            "path": path,
            "name": name or "",
        }
    return {
        "kind": "structural_fingerprint",
        "name": name or "",
        "token": project_token(session),
    }


def project_identity_token(
    session: SessionState, *, path: str | None = None, name: str | None = None
) -> str:
    return token_of(project_identity_payload(session, path=path, name=name))


def attach_tokens(
    session: SessionState, *, path: str | None = None, name: str | None = None
) -> SessionState:
    # Re-attaching tokens is common during validation and must not erase the
    # authoritative project identity discovered by the DAW adapter. Callers
    # may still override either value explicitly, but omission means
    # "preserve the current session metadata".
    if path is None:
        path = session.project_path
    if name is None:
        name = session.project_name
    session.project_path = path
    session.project_name = name
    session.project_identity = project_identity_token(session, path=path, name=name)
    session.project_token = project_token(session)
    session.audible_token = audible_token(session)
    combined = dumps_canonical(
        {"project": session.project_token, "audible": session.audible_token}
    )
    session.state_hash = hashlib.sha256(combined).hexdigest()
    return session


def token_for_scope(
    session: SessionState,
    scope: StateScope,
    *,
    track: TrackState | None = None,
    track_names: frozenset[str] | None = None,
) -> str:
    if scope is StateScope.PROJECT:
        return project_token(session)
    if scope is StateScope.AUDIBLE:
        return audible_token(session, track_names=track_names)
    if track is None:
        raise ValueError("TARGET scope requires a track")
    return target_token(track)


# mutation → which tokens must change. Used as the executable contract table.
MUTATION_TABLE: list[dict[str, Any]] = [
    {
        "mutation": "track_volume",
        "project": False,
        "audible": True,
        "target": True,
    },
    {
        "mutation": "track_mute",
        "project": False,
        "audible": True,
        "target": True,
    },
    {
        "mutation": "routing",
        "project": True,
        "audible": True,
        "target": True,
    },
    {
        "mutation": "rename_target",
        "project": True,
        "audible": False,
        "target": True,
    },
    {
        "mutation": "reorder_target",
        "project": True,
        "audible": False,
        "target": False,
    },
    {
        "mutation": "insert_unrelated_track",
        "project": True,
        "audible_session": True,
        "audible_target_scope": False,
        "target": False,
    },
    {
        "mutation": "target_midi",
        "project": True,
        "audible": True,
        "target": True,
    },
    {
        "mutation": "device_parameter",
        "project": False,
        "audible": True,
        "target": True,
    },
    {
        "mutation": "playback_cursor",
        "project": False,
        "audible": False,
        "target": False,
    },
    {
        "mutation": "track_selection",
        "project": False,
        "audible": False,
        "target": False,
    },
]
