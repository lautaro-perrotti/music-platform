from __future__ import annotations

from typing import Any

from copilot.daw.adapter import DawError

PROTOCOL_VERSION = "1"
BRIDGE_VERSION = "copilot-prelive-1"

CAPABILITIES = frozenset(
    {
        "health",
        "session.read",
        "session.transport",
        "track.create_midi",
        "track.delete",
        "track.rename",
        "track.volume",
        "track.mute",
        "clip.create",
        "clip.delete",
        "clip.rename",
        "clip.read_notes",
        "clip.write_notes",
        "clip.fire",
        "device.set_parameter",
        "device.load",
        "audio.capture_master",
    }
)

COMMAND_CAPABILITY = {
    "health_check": "health",
    "protocol_hello": "health",
    "get_session_info": "session.read",
    "get_playback_position": "session.read",
    "get_all_track_names": "session.read",
    "get_track_info": "session.read",
    "get_tracks_info": "session.read",
    "get_capture_topology": "session.read",
    "get_master_info": "session.read",
    "get_device_parameters": "session.read",
    "get_device_parameter": "session.read",
    "get_track_sends": "session.read",
    "browse_path": "session.read",
    "search_browser": "session.read",
    "create_midi_track": "track.create_midi",
    "delete_track": "track.delete",
    "set_track_name": "track.rename",
    "set_track_volume": "track.volume",
    "set_track_mute": "track.mute",
    "set_track_solo": "track.mute",
    "create_clip": "clip.create",
    "delete_clip": "clip.delete",
    "set_clip_name": "clip.rename",
    "get_clip_notes": "clip.read_notes",
    "add_notes_to_clip": "clip.write_notes",
    "fire_clip": "clip.fire",
    "fire_clips": "clip.fire",
    "stop_clip": "clip.fire",
    "stop_clips": "clip.fire",
    "set_device_parameter": "device.set_parameter",
    "set_device_parameters": "device.set_parameter",
    "load_instrument_or_effect": "device.load",
    "load_browser_item": "device.load",
    "load_browser_item_by_path": "browser.load",
    "delete_device": "device.load",
    "move_device": "device.load",
    "move_device_right": "device.load",
    "start_playback": "session.transport",
    "stop_playback": "session.transport",
    "set_current_song_time": "session.transport",
    "jump_to_time": "session.transport",
    "scrub_by": "session.transport",
    "continue_playing": "session.transport",
    "start_playback_at_qn": "session.transport",
    "set_arrangement_loop": "session.transport",
    "set_tempo": "session.transport",
    "create_audio_track": "track.create_midi",
    "get_available_inputs": "session.read",
    "get_available_outputs": "session.read",
    "get_track_input_routing": "session.read",
    "get_track_output_routing": "session.read",
    "get_track_available_input_types": "session.read",
    "get_track_available_output_types": "session.read",
    "get_session_automation_record": "session.read",
    "get_clip_automation": "session.read",
    "set_track_input_routing": "track.mute",
    "set_track_output_routing": "track.mute",
    "set_device_input_routing": "device.set_parameter",
    "set_track_monitoring": "track.mute",
    "get_track_monitoring": "session.read",
    "get_track_delay": "session.read",
    "get_session_path": "session.read",
    "set_track_arm": "track.mute",
    "set_send_level": "track.volume",
    "get_send_level": "session.read",
    "set_return_volume": "track.volume",
    "get_return_tracks": "session.read",
    "create_return_track": "track.create_midi",
    "create_group_track": "track.create_midi",
    "set_signature": "session.transport",
    "get_clip_warp_info": "session.read",
    "set_clip_warp_mode": "clip.write_notes",
    "create_audio_clip": "clip.create",
    "set_clip_warping": "clip.write_notes",
    "set_clip_loop": "clip.write_notes",
}

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ProtocolError(DawError):
    pass


class UnsupportedCapability(DawError):
    pass


def require_local_host(host: str) -> None:
    if host not in LOCAL_HOSTS:
        raise DawError(
            f"LOCALHOST_TRUST_BOUNDARY: refusing non-local host {host!r}"
        )


def handshake_payload() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "bridge_version": BRIDGE_VERSION,
        "capabilities": sorted(CAPABILITIES),
        "bind": "127.0.0.1",
    }


def require_capability(capabilities: set[str], command_type: str) -> None:
    needed = COMMAND_CAPABILITY.get(command_type)
    if needed is None:
        raise UnsupportedCapability(f"Unknown command {command_type!r}")
    if needed not in capabilities:
        raise UnsupportedCapability(
            f"required capability {needed} not advertised for {command_type}"
        )
