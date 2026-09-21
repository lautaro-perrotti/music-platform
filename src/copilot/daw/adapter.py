from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from copilot.schemas.session import MidiNote, SessionState


class DawError(RuntimeError):
    pass


class DawAdapter(ABC):
    """DAW-independent control surface. Never expose raw LOM or eval."""

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    @abstractmethod
    def snapshot(self) -> SessionState: ...

    @abstractmethod
    def create_midi_track(self, name: str, index: int = -1) -> dict[str, Any]: ...

    @abstractmethod
    def create_audio_track(self, name: str, index: int = -1) -> dict[str, Any]: ...

    @abstractmethod
    def delete_track(self, track_index: int) -> dict[str, Any]: ...

    @abstractmethod
    def set_track_name(self, track_index: int, name: str) -> dict[str, Any]: ...

    @abstractmethod
    def set_mixer_volume(self, track_index: int, volume: float) -> dict[str, Any]: ...

    @abstractmethod
    def set_track_mute(self, track_index: int, mute: bool) -> dict[str, Any]: ...

    @abstractmethod
    def set_track_output_routing(
        self, track_index: int, routing_type: str, routing_channel: str = ""
    ) -> dict[str, Any]: ...

    @abstractmethod
    def save_session(self) -> dict[str, Any]: ...

    @abstractmethod
    def set_device_input_routing(
        self, track_index: int, device_index: int, routing_type: str, routing_channel: str = ""
    ) -> dict[str, Any]: ...

    @abstractmethod
    def create_midi_clip(
        self, track_index: int, clip_index: int, length_beats: float
    ) -> dict[str, Any]: ...

    @abstractmethod
    def delete_clip(self, track_index: int, clip_index: int) -> dict[str, Any]: ...

    @abstractmethod
    def set_clip_name(
        self, track_index: int, clip_index: int, name: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    def replace_clip_notes(
        self, track_index: int, clip_index: int, notes: list[MidiNote]
    ) -> dict[str, Any]: ...

    @abstractmethod
    def get_clip_notes(
        self, track_index: int, clip_index: int
    ) -> dict[str, Any]: ...

    @abstractmethod
    def set_device_parameter(
        self,
        track_index: int,
        device_index: int,
        parameter_index: int,
        value: float,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def set_device_parameters(self, items: list[dict[str, Any]]) -> dict[str, Any]: ...

    @abstractmethod
    def get_device_parameters(
        self, track_index: int, device_index: int
    ) -> dict[str, Any]: ...

    @abstractmethod
    def load_instrument_or_effect(
        self, track_index: int, uri: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    def load_browser_item(
        self, track_index: int, item_uri: str, clip_index: int | None = None
    ) -> dict[str, Any]: ...

    @abstractmethod
    def get_device_by_name(self, track_index: int, device_name: str) -> dict[str, Any]: ...

    @abstractmethod
    def load_device_preset(
        self, track_index: int, device_index: int, preset_uri: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    def delete_device(
        self, track_index: int, device_index: int
    ) -> dict[str, Any]: ...

    @abstractmethod
    def bridge_command(
        self,
        command_type: str,
        params: dict[str, Any] | None = None,
        *,
        side_effect: bool | None = None,
    ) -> dict[str, Any]: ...
