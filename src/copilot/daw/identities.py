from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from copilot.schemas.session import ClipState, DeviceState, TrackState


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


@dataclass
class TrackRecord:
    stable_id: str
    last_index: int
    name: str
    role: str
    device_names: tuple[str, ...] = field(default_factory=tuple)
    clip_names: tuple[str, ...] = field(default_factory=tuple)


class IdentityRegistry:
    """Session-scoped IDs. Live index is a locator, never the identity."""

    def __init__(self) -> None:
        self._tracks: list[TrackRecord] = []
        self._clips: dict[tuple[str, int], str] = {}
        self._devices: dict[tuple[str, int, str], str] = {}

    def clip_id(self, track_id: str, slot_index: int) -> str:
        key = (track_id, slot_index)
        if key not in self._clips:
            self._clips[key] = new_id("clip")
        return self._clips[key]

    def device_id(self, track_id: str, index: int, name: str) -> str:
        key = (track_id, index, name)
        if key not in self._devices:
            self._devices[key] = new_id("dev")
        return self._devices[key]

    def forget_track(self, track: TrackState) -> None:
        self._tracks = [item for item in self._tracks if item.stable_id != track.stable_id]
        for clip in track.clips:
            self._clips.pop((track.stable_id, clip.slot_index), None)
        for device in track.devices:
            self._devices.pop((track.stable_id, device.index, device.name), None)

    def attach(
        self,
        tracks: list[TrackState],
        clips: dict[int, list[ClipState]],
        devices: dict[int, list[DeviceState]],
    ) -> list[TrackState]:
        incoming: list[tuple[TrackState, tuple[str, ...], tuple[str, ...]]] = []
        for track in tracks:
            device_names = tuple(device.name for device in devices.get(track.index, []))
            clip_names = tuple(clip.name for clip in clips.get(track.index, []))
            incoming.append((track, device_names, clip_names))

        assigned: dict[int, str] = {}
        used_records: set[str] = set()

        name_groups: dict[tuple[str, str], list[int]] = {}
        for idx, (track, _, _) in enumerate(incoming):
            name_groups.setdefault((track.name, track.role), []).append(idx)
        for key, indexes in name_groups.items():
            if len(indexes) != 1:
                continue
            matches = [
                rec
                for rec in self._tracks
                if rec.name == key[0] and rec.role == key[1] and rec.stable_id not in used_records
            ]
            if len(matches) == 1:
                assigned[indexes[0]] = matches[0].stable_id
                used_records.add(matches[0].stable_id)

        for idx, (track, device_names, clip_names) in enumerate(incoming):
            if idx in assigned:
                continue
            matches = [
                rec
                for rec in self._tracks
                if rec.stable_id not in used_records
                and rec.role == track.role
                and rec.device_names == device_names
                and rec.clip_names == clip_names
            ]
            if len(matches) == 1:
                assigned[idx] = matches[0].stable_id
                used_records.add(matches[0].stable_id)

        for idx, (track, _, _) in enumerate(incoming):
            if idx in assigned:
                continue
            matches = [
                rec
                for rec in self._tracks
                if rec.stable_id not in used_records
                and rec.last_index == track.index
                and rec.role == track.role
            ]
            if len(matches) == 1:
                assigned[idx] = matches[0].stable_id
                used_records.add(matches[0].stable_id)

        next_records: list[TrackRecord] = []
        attached: list[TrackState] = []
        for idx, (track, device_names, clip_names) in enumerate(incoming):
            stable_id = assigned.get(idx) or new_id("trk")
            track.stable_id = stable_id
            track.clips = []
            for clip in clips.get(track.index, []):
                clip.stable_id = self.clip_id(stable_id, clip.slot_index)
                track.clips.append(clip)
            track.devices = []
            for device in devices.get(track.index, []):
                device.stable_id = self.device_id(stable_id, device.index, device.name)
                track.devices.append(device)
            next_records.append(
                TrackRecord(
                    stable_id=stable_id,
                    last_index=track.index,
                    name=track.name,
                    role=track.role,
                    device_names=device_names,
                    clip_names=clip_names,
                )
            )
            attached.append(track)
        self._tracks = next_records
        return attached


    def dump(self) -> list[dict]:
        return [
            {
                "stable_id": rec.stable_id,
                "last_index": rec.last_index,
                "name": rec.name,
                "role": rec.role,
                "device_names": list(rec.device_names),
                "clip_names": list(rec.clip_names),
            }
            for rec in self._tracks
        ]

    def load(self, records: list[dict]) -> None:
        self._tracks = [
            TrackRecord(
                stable_id=item["stable_id"],
                last_index=int(item["last_index"]),
                name=item["name"],
                role=item["role"],
                device_names=tuple(item.get("device_names", ())),
                clip_names=tuple(item.get("clip_names", ())),
            )
            for item in records
        ]


def fingerprint_track(track: TrackState) -> dict:
    return {
        "kind": "track",
        "role": track.role,
        "device_names": [device.name for device in track.devices],
        "clip_slots": [clip.slot_index for clip in track.clips],
        "clip_names": [clip.name for clip in track.clips],
        "note_counts": [len(clip.notes) for clip in track.clips],
    }


def fingerprints_equal(left: dict, right: dict) -> bool:
    keys = ("kind", "role", "device_names", "clip_slots", "clip_names", "note_counts")
    return all(left.get(key) == right.get(key) for key in keys)


def resolve_by_fingerprint(
    session: "SessionState",
    expected: dict,
    stable_id: str = "",
    session_incarnation_id: str = "",
) -> "TrackState | None":
    """Exactly one safe match, or None. Never pick the closest target."""
    fp_matches = [
        track
        for track in session.tracks
        if fingerprints_equal(fingerprint_track(track), expected)
    ]
    same_incarnation = (
        bool(session_incarnation_id)
        and session.session_incarnation_id == session_incarnation_id
    )
    if same_incarnation and stable_id:
        id_matches = [
            track for track in session.tracks if track.stable_id == stable_id
        ]
        if len(id_matches) == 1 and id_matches[0] in fp_matches:
            return id_matches[0]
    if len(fp_matches) == 1:
        return fp_matches[0]
    return None


def empty_session(connected: bool = False):
    from copilot.schemas.session import SessionState

    return SessionState(connected=connected, revision=0)
