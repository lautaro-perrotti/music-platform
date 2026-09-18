from __future__ import annotations

from copy import deepcopy
from typing import Any

from uuid import uuid4

from copilot.daw.adapter import DawAdapter, DawError
from copilot.daw.identities import IdentityRegistry
from copilot.daw.state_hash import ObservedRevision
from copilot.daw.write import WriteInDoubt
from copilot.schemas.session import (
    ClipState,
    DeviceParameter,
    DeviceState,
    MidiNote,
    MixerState,
    RoutingState,
    SendState,
    SessionState,
    TrackState,
    TransportState,
)


class MockAbletonAdapter(DawAdapter):
    """In-process Ableton stand-in that speaks our typed adapter, not LOM."""

    def __init__(self, slot_count: int = 8) -> None:
        self.slot_count = slot_count
        self._connected = False
        self.ids = IdentityRegistry()
        self.observed = ObservedRevision()
        self.session_incarnation_id = f"sess_{uuid4().hex[:12]}"
        self.transport = TransportState()
        self.tracks: list[dict[str, Any]] = []
        self._ensure_eq_device = True
        self.fail_after_writes: int | None = None
        self.lose_ack = False
        self._write_ops = 0
        self.session_path: str | None = None
        self.session_name: str = "Mock Set"

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def health(self) -> dict[str, Any]:
        self._require()
        return {
            "status": "ok",
            "backend": "mock",
            "tempo": self.transport.tempo,
            "is_playing": self.transport.playing,
            "track_count": len(self.tracks),
        }

    def snapshot(self) -> SessionState:
        self._require()
        tracks: list[TrackState] = []
        clips: dict[int, list[ClipState]] = {}
        devices: dict[int, list[DeviceState]] = {}
        for index, raw in enumerate(self.tracks):
            role = "midi" if raw["is_midi"] else "audio"
            tracks.append(
                TrackState(
                    stable_id="",
                    index=index,
                    name=raw["name"],
                    role=role,
                    mixer=MixerState(**raw["mixer"]),
                    routing=RoutingState(**raw.get("routing") or {}),
                    sends=[SendState(**send) for send in raw.get("sends") or []],
                    grouped=bool(raw.get("grouped", False)),
                    foldable=bool(raw.get("foldable", False)),
                )
            )
            clips[index] = []
            for slot_index, clip in enumerate(raw["clips"]):
                if clip is None:
                    continue
                is_audio = bool(clip.get("is_audio", False))
                clips[index].append(
                    ClipState(
                        stable_id="",
                        slot_index=slot_index,
                        name=clip["name"],
                        length_beats=clip["length"],
                        is_midi=not is_audio,
                        notes=[] if is_audio else [MidiNote(**note) for note in clip["notes"]],
                        sample_uri=clip.get("sample_uri"),
                    )
                )
            devices[index] = [
                DeviceState(
                    stable_id="",
                    index=dev_index,
                    name=device["name"],
                    class_name=device["class_name"],
                    enabled=device["enabled"],
                    parameters=[
                        DeviceParameter(**parameter)
                        for parameter in device["parameters"]
                    ],
                )
                for dev_index, device in enumerate(raw["devices"])
            ]
        attached = self.ids.attach(tracks, clips, devices)
        session = SessionState(
            daw="mock",
            connected=True,
            session_incarnation_id=self.session_incarnation_id,
            project_path=self.session_path,
            project_name=self.session_name,
            transport=self.transport.model_copy(),
            tracks=attached,
        )
        return self.observed.observe(session)

    def create_midi_track(self, name: str, index: int = -1) -> dict[str, Any]:
        self._require()
        track = {
            "name": name or f"MIDI {len(self.tracks) + 1}",
            "is_midi": True,
            "mixer": MixerState().model_dump(),
            "routing": RoutingState(output_type="Main", monitoring="in").model_dump(),
            "sends": [],
            "clips": [None] * self.slot_count,
            "devices": [],
        }
        if self._ensure_eq_device:
            track["devices"].append(
                {
                    "name": "EQ Eight",
                    "class_name": "Eq8",
                    "enabled": True,
                    "parameters": [
                        {
                            "index": 0,
                            "name": "1 Gain A",
                            "value": 0.5,
                            "min": 0.0,
                            "max": 1.0,
                        }
                    ],
                }
            )
        self._before_write("create_midi_track")
        if index < 0 or index >= len(self.tracks):
            self.tracks.append(track)
            new_index = len(self.tracks) - 1
        else:
            self.tracks.insert(index, track)
            new_index = index
        return self._after_write(
            "create_midi_track",
            {"index": new_index, "name": self.tracks[new_index]["name"]},
        )

    def delete_track(self, track_index: int) -> dict[str, Any]:
        self._before_write("delete_track")
        snap = self.snapshot()
        if 0 <= track_index < len(snap.tracks):
            self.ids.forget_track(snap.tracks[track_index])
        track = self._track(track_index)
        name = track["name"]
        del self.tracks[track_index]
        return self._after_write(
            "delete_track",
            {"deleted": True, "track_index": track_index, "track_name": name},
        )

    def set_track_name(self, track_index: int, name: str) -> dict[str, Any]:
        self._before_write("set_track_name")
        track = self._track(track_index)
        track["name"] = name
        return self._after_write("set_track_name", {"name": name})

    def set_mixer_volume(self, track_index: int, volume: float) -> dict[str, Any]:
        self._before_write("set_mixer_volume")
        track = self._track(track_index)
        track["mixer"]["volume"] = max(0.0, min(1.0, volume))
        return self._after_write(
            "set_mixer_volume",
            {"track_index": track_index, "volume": track["mixer"]["volume"]},
        )

    def set_track_mute(self, track_index: int, mute: bool) -> dict[str, Any]:
        self._before_write("set_track_mute")
        track = self._track(track_index)
        track["mixer"]["mute"] = bool(mute)
        return self._after_write("set_track_mute", {"mute": bool(mute)})

    def set_track_output_routing(
        self, track_index: int, routing_type: str, routing_channel: str = ""
    ) -> dict[str, Any]:
        self._before_write("set_track_output_routing")
        track = self._track(track_index)
        routing = track.setdefault("routing", RoutingState().model_dump())
        routing["output_type"] = routing_type
        routing["output_channel"] = routing_channel
        return self._after_write(
            "set_track_output_routing",
            {"output_type": routing_type, "output_channel": routing_channel},
        )

    def get_session_path(self) -> dict[str, Any]:
        return {"path": self.session_path, "name": self.session_name}

    def create_midi_clip(
        self, track_index: int, clip_index: int, length_beats: float
    ) -> dict[str, Any]:
        self._before_write("create_midi_clip")
        track = self._track(track_index)
        self._slot(track, clip_index)
        if track["clips"][clip_index] is not None:
            raise DawError("Clip slot already has a clip")
        track["clips"][clip_index] = {
            "name": "",
            "length": float(length_beats),
            "notes": [],
        }
        return self._after_write(
            "create_midi_clip", {"name": "", "length": float(length_beats)}
        )

    def delete_clip(self, track_index: int, clip_index: int) -> dict[str, Any]:
        self._before_write("delete_clip")
        track = self._track(track_index)
        self._slot(track, clip_index)
        if track["clips"][clip_index] is None:
            raise DawError("No clip in slot")
        track["clips"][clip_index] = None
        return self._after_write("delete_clip", {"deleted": True})

    def set_clip_name(
        self, track_index: int, clip_index: int, name: str
    ) -> dict[str, Any]:
        self._before_write("set_clip_name")
        clip = self._clip(track_index, clip_index)
        clip["name"] = name
        return self._after_write("set_clip_name", {"name": name})

    def replace_clip_notes(
        self, track_index: int, clip_index: int, notes: list[MidiNote]
    ) -> dict[str, Any]:
        self._before_write("replace_clip_notes")
        clip = self._clip(track_index, clip_index)
        clip["notes"] = [note.model_dump() for note in notes]
        return self._after_write("replace_clip_notes", {"note_count": len(notes)})

    def get_clip_notes(self, track_index: int, clip_index: int) -> dict[str, Any]:
        clip = self._clip(track_index, clip_index)
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "clip_name": clip["name"],
            "length": clip["length"],
            "note_count": len(clip["notes"]),
            "notes": deepcopy(clip["notes"]),
        }

    def set_device_parameter(
        self,
        track_index: int,
        device_index: int,
        parameter_index: int,
        value: float,
    ) -> dict[str, Any]:
        self._before_write("set_device_parameter")
        track = self._track(track_index)
        if device_index < 0 or device_index >= len(track["devices"]):
            raise DawError("Device index out of range")
        device = track["devices"][device_index]
        if parameter_index < 0 or parameter_index >= len(device["parameters"]):
            raise DawError("Parameter index out of range")
        parameter = device["parameters"][parameter_index]
        parameter["value"] = max(parameter["min"], min(parameter["max"], value))
        return self._after_write(
            "set_device_parameter",
            {
                "track_index": track_index,
                "device_index": device_index,
                "parameter_index": parameter_index,
                "value": parameter["value"],
            },
        )

    def load_instrument_or_effect(self, track_index: int, uri: str) -> dict[str, Any]:
        self._before_write("load_instrument_or_effect")
        track = self._track(track_index)
        name = uri.rsplit("/", 1)[-1] if "/" in uri else uri
        device = {
            "name": name,
            "class_name": name,
            "enabled": True,
            "parameters": [
                {"index": 0, "name": "Device On", "value": 1.0, "min": 0.0, "max": 1.0},
                {"index": 1, "name": "Mix", "value": 0.5, "min": 0.0, "max": 1.0},
            ],
        }
        track["devices"].append(device)
        device_index = len(track["devices"]) - 1
        return self._after_write(
            "load_instrument_or_effect",
            {"device_index": device_index, "device_name": name},
        )

    def delete_device(self, track_index: int, device_index: int) -> dict[str, Any]:
        self._before_write("delete_device")
        track = self._track(track_index)
        if device_index < 0 or device_index >= len(track["devices"]):
            raise DawError("Device index out of range")
        name = track["devices"][device_index]["name"]
        del track["devices"][device_index]
        return self._after_write(
            "delete_device",
            {"deleted": True, "device_index": device_index, "device_name": name},
        )

    def load_browser_item(
        self, track_index: int, item_uri: str, clip_index: int | None = None
    ) -> dict[str, Any]:
        # SAMPLE_SWAP primitive. Loads a sample into a clip slot (audio clip).
        self._before_write("load_browser_item")
        track = self._track(track_index)
        if clip_index is None:
            clip_index = next(
                (i for i, c in enumerate(track["clips"]) if c is None), 0
            )
        self._slot(track, clip_index)
        clip = track["clips"][clip_index]
        if clip is None:
            clip = {
                "name": item_uri.rsplit("/", 1)[-1],
                "length": 4.0,
                "notes": [],
                "is_audio": True,
                "sample_uri": item_uri,
            }
        else:
            clip["is_audio"] = True
            clip["sample_uri"] = item_uri
        track["clips"][clip_index] = clip
        return self._after_write(
            "load_browser_item",
            {"track_index": track_index, "clip_index": clip_index, "item_uri": item_uri},
        )

    def _before_write(self, operation: str) -> None:
        if (
            self.fail_after_writes is not None
            and self._write_ops >= self.fail_after_writes
        ):
            raise DawError(f"injected write failure before {operation}")

    def _after_write(self, operation: str, result: dict[str, Any]) -> dict[str, Any]:
        self._write_ops += 1
        if self.lose_ack:
            raise WriteInDoubt(operation)
        return result

    def _require(self) -> None:
        if not self._connected:
            raise DawError("Not connected")

    def _track(self, track_index: int) -> dict[str, Any]:
        self._require()
        if track_index < 0 or track_index >= len(self.tracks):
            raise DawError("Track index out of range")
        return self.tracks[track_index]

    def _slot(self, track: dict[str, Any], clip_index: int) -> None:
        if clip_index < 0 or clip_index >= len(track["clips"]):
            raise DawError("Clip index out of range")

    def _clip(self, track_index: int, clip_index: int) -> dict[str, Any]:
        track = self._track(track_index)
        self._slot(track, clip_index)
        clip = track["clips"][clip_index]
        if clip is None:
            raise DawError("No clip in slot")
        return clip
