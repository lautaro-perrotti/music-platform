from __future__ import annotations

import json
import socket
import threading
from typing import Any

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.protocol import handshake_payload, require_local_host
from copilot.daw.track_monitoring_v1 import NOT_APPLICABLE, read_monitoring
from copilot.schemas.session import MidiNote


class MockRemoteScriptServer:
    """Speaks the AbletonMCP JSON-over-TCP protocol without Live."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        require_local_host(host)
        self.host = host
        self.port = port
        self.backend = MockAbletonAdapter()
        self.backend.connect()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.drop_response = False
        self.invalid_json = False
        self.partial_json = False
        self.wrong_request_id = False
        self.duplicate_response = False
        self.slow_seconds = 0.0
        self.disconnect_after_recv = False
        self.advertise_compound = False
        self.fail_mutation_step: str | None = None
        self.disconnect_during_mutation = False

    def start(self) -> tuple[str, int]:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self.host, self.port

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(1.0)

    def _serve(self) -> None:
        assert self._sock is not None
        while self._running:
            try:
                self._sock.settimeout(0.25)
                client, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle, args=(client,), daemon=True
            ).start()

    def _handle(self, client: socket.socket) -> None:
        buffer = b""
        try:
            while self._running:
                chunk = client.recv(8192)
                if not chunk:
                    break
                buffer += chunk
                try:
                    command = json.loads(buffer.decode("utf-8"))
                    buffer = b""
                except json.JSONDecodeError:
                    continue
                if self.disconnect_after_recv:
                    break
                if self.slow_seconds:
                    threading.Event().wait(self.slow_seconds)
                response = self._process(command)
                if self.drop_response:
                    continue
                if self.invalid_json:
                    client.sendall(b"{not-json")
                    continue
                if self.partial_json:
                    client.sendall(b'{"status":"success"')
                    continue
                if self.wrong_request_id:
                    response["request_id"] = "req_other"
                payload = json.dumps(response).encode("utf-8")
                client.sendall(payload)
                if self.duplicate_response:
                    client.sendall(payload)
        finally:
            client.close()

    def _process(self, command: dict[str, Any]) -> dict[str, Any]:
        command_type = command.get("type", "")
        params = command.get("params") or {}
        request_id = command.get("request_id")
        try:
            result = self._dispatch(command_type, params)
            response = {"status": "success", "result": result}
        except Exception as exc:  # noqa: BLE001 - protocol must always answer
            response = {"status": "error", "message": str(exc)}
        if request_id:
            response["request_id"] = request_id
        return response

    def _dispatch(self, command_type: str, params: dict[str, Any]) -> dict[str, Any]:
        if command_type == "protocol_hello":
            payload = {**handshake_payload(), "backend": "mock"}
            if self.advertise_compound:
                caps = set(payload.get("capabilities") or [])
                caps.add("compound.temporary_mutation")
                payload["capabilities"] = sorted(caps)
                payload["compound_mutation_version"] = "1"
                payload["compound_supported_operations"] = [
                    "SET_TRACK_MONITORING",
                    "SET_TRACK_INPUT_ROUTING",
                    "SET_TRACK_OUTPUT_ROUTING",
                    "SET_DEVICE_PARAMETER",
                    "SET_SEND_LEVEL",
                ]
            return payload
        if command_type == "execute_mutation_batch":
            return self._execute_mutation_batch(params)
        if command_type == "health_check":
            return self.backend.health()
        if command_type == "get_session_info":
            snap = self.backend.snapshot()
            return {
                "tempo": snap.transport.tempo,
                "signature_numerator": snap.transport.signature_numerator,
                "signature_denominator": snap.transport.signature_denominator,
                "track_count": len(snap.tracks),
                "return_track_count": 0,
            }
        if command_type == "get_session_path":
            return {
                "path": self.backend.session_path,
                "name": self.backend.session_name,
            }
        if command_type == "get_playback_position":
            return {
                "current_song_time": self.backend.transport.position_beats,
                "is_playing": self.backend.transport.playing,
                "tempo": self.backend.transport.tempo,
                "signature_numerator": self.backend.transport.signature_numerator,
                "signature_denominator": self.backend.transport.signature_denominator,
            }
        if command_type == "get_all_track_names":
            return {"names": [track["name"] for track in self.backend.tracks]}
        if command_type == "get_track_info":
            snap = self.backend.snapshot()
            track = snap.tracks[int(params["track_index"])]
            return {
                "index": track.index,
                "name": track.name,
                "is_audio_track": track.role == "audio",
                "is_midi_track": track.role == "midi",
                "mute": track.mixer.mute,
                "solo": track.mixer.solo,
                "arm": track.mixer.arm,
                "volume": track.mixer.volume,
                "panning": track.mixer.pan,
                "monitoring": read_monitoring(
                    can_be_armed=track.role in {"audio", "midi"},
                    is_main=track.role == "master",
                    is_return=track.role == "return",
                    raw_state=0,
                ),
                "can_be_armed": track.role in {"audio", "midi"},
                "input_routing_type": "",
                "input_routing_channel": "",
                "output_routing_type": "Main",
                "output_routing_channel": "",
                "sends": [],
                "taps": [],
                "clip_slots": [
                    {
                        "index": clip.slot_index,
                        "has_clip": True,
                        "clip": {"name": clip.name, "length": clip.length_beats},
                    }
                    for clip in track.clips
                ],
                "devices": [
                    {
                        "index": device.index,
                        "name": device.name,
                        "class_name": device.class_name,
                        "enabled": device.enabled,
                        "parameters": [
                            {
                                "index": parameter.index,
                                "name": parameter.name,
                                "value": parameter.value,
                            }
                            for parameter in device.parameters
                        ],
                    }
                    for device in track.devices
                ],
            }
        if command_type == "get_master_info":
            return {
                "name": "Master",
                "devices": [],
                "device_count": 0,
                "taps": [],
                "sends": [],
                "monitoring": NOT_APPLICABLE,
                "can_be_armed": False,
            }
        if command_type == "get_tracks_info":
            snap = self.backend.snapshot()
            indices = params.get("indices") or [track.index for track in snap.tracks]
            return {
                "tracks": [
                    self._dispatch("get_track_info", {"track_index": int(index)})
                    for index in indices
                ],
                "track_count": len(snap.tracks),
            }
        if command_type in {"get_capture_hosts_state", "get_tracks_state"}:
            return self._dispatch("get_tracks_info", params)
        if command_type == "get_tracks_sends":
            payload = self._dispatch("get_tracks_info", params)
            return {
                "tracks": [
                    {
                        "track_index": int(item["index"]),
                        "sends": list(item.get("sends") or []),
                    }
                    for item in payload.get("tracks") or []
                    if "index" in item
                ]
            }
        if command_type == "get_capture_topology":
            return {
                "session": self._dispatch("get_session_info", {}),
                "playback": self._dispatch("get_playback_position", {}),
                "master": self._dispatch("get_master_info", {}),
                "tracks": self._dispatch("get_tracks_info", {}).get("tracks") or [],
                "return_tracks": {
                    "return_tracks": [
                        {
                            "index": 0,
                            "name": "A",
                            "monitoring": NOT_APPLICABLE,
                            "can_be_armed": False,
                        }
                    ],
                    "return_track_count": 1,
                },
                "project": {"path": None, "name": "Mock Set"},
            }
        if command_type == "get_device_parameter":
            return {
                "track_index": int(params.get("track_index", 0)),
                "device_index": int(params.get("device_index", 0)),
                "parameter_index": int(params.get("parameter_index", 0)),
                "parameter_name": "Slot",
                "value": float(params.get("value", 0.0) or 0.0),
            }
        if command_type == "get_device_parameters":
            return {"parameters": []}
        if command_type == "get_track_sends":
            return {"track_index": int(params.get("track_index", 0)), "sends": []}
        if command_type == "get_return_tracks":
            return {"return_tracks": [], "return_track_count": 0}
        if command_type == "create_midi_track":
            created = self.backend.create_midi_track("", int(params.get("index", -1)))
            return created
        if command_type == "set_track_name":
            return self.backend.set_track_name(
                int(params["track_index"]), str(params["name"])
            )
        if command_type == "delete_track":
            return self.backend.delete_track(int(params["track_index"]))
        if command_type == "set_track_volume":
            return self.backend.set_mixer_volume(
                int(params["track_index"]), float(params["volume"])
            )
        if command_type == "create_clip":
            return self.backend.create_midi_clip(
                int(params["track_index"]),
                int(params["clip_index"]),
                float(params.get("length", 4.0)),
            )
        if command_type == "delete_clip":
            return self.backend.delete_clip(
                int(params["track_index"]), int(params["clip_index"])
            )
        if command_type == "set_clip_name":
            return self.backend.set_clip_name(
                int(params["track_index"]),
                int(params["clip_index"]),
                str(params["name"]),
            )
        if command_type == "add_notes_to_clip":
            notes = [MidiNote(**note) for note in params.get("notes", [])]
            return self.backend.replace_clip_notes(
                int(params["track_index"]), int(params["clip_index"]), notes
            )
        if command_type == "get_clip_notes":
            return self.backend.get_clip_notes(
                int(params["track_index"]), int(params["clip_index"])
            )
        if command_type == "set_device_parameter":
            return self.backend.set_device_parameter(
                int(params["track_index"]),
                int(params["device_index"]),
                int(params["parameter_index"]),
                float(params["value"]),
            )
        if command_type == "set_device_parameters":
            results = []
            ok = True
            for item in params.get("items") or []:
                try:
                    row = dict(
                        self.backend.set_device_parameter(
                            int(item["track_index"]),
                            int(item["device_index"]),
                            int(item["parameter_index"]),
                            float(item["value"]),
                        )
                    )
                    row["ok"] = True
                    row["id"] = item.get("id")
                    results.append(row)
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    results.append({"ok": False, "id": item.get("id"), "error": str(exc)})
            return {"ok": ok, "results": results}
        if command_type == "fire_clips":
            return {
                "ok": True,
                "results": [
                    {"ok": True, "track_index": int(item.get("track_index", 0))}
                    for item in params.get("clips") or []
                ],
            }
        if command_type == "stop_clips":
            return {
                "ok": True,
                "results": [
                    {"ok": True, "track_index": int(item.get("track_index", 0))}
                    for item in params.get("clips") or []
                ],
            }
        raise ValueError(f"Unsupported command: {command_type}")

    def _execute_mutation_batch(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.disconnect_during_mutation:
            raise ConnectionError("disconnect during mutation batch")
        results: list[dict[str, Any]] = []
        stop = False
        for step in params.get("steps") or []:
            step_id = str(step.get("step_id") or "")
            independent = bool(step.get("independent"))
            row = {
                "step_id": step_id,
                "target": step.get("target_ref") or {},
                "operation": step.get("operation"),
                "requested_value": step.get("arguments") or {},
                "host_id": step.get("host_id") or "",
                "purpose": step.get("purpose") or "",
                "observed": None,
                "error": None,
                "status": "NOT_ATTEMPTED",
            }
            if stop and not independent:
                row["error"] = "not_attempted_after_failure"
                results.append(row)
                continue
            if self.fail_mutation_step and step_id == self.fail_mutation_step:
                row["status"] = "FAILED"
                row["error"] = "injected_failure"
                stop = True
                results.append(row)
                continue
            row["status"] = "APPLIED"
            row["observed"] = {"ok": True, "arguments": step.get("arguments") or {}}
            results.append(row)
        applied = [row for row in results if row["status"] == "APPLIED"]
        failed = [row for row in results if row["status"] == "FAILED"]
        if failed:
            status = "PARTIAL_FAILURE"
        elif applied and len(applied) == len(results):
            status = "COMPLETE"
        elif not results:
            status = "COMPLETE"
        else:
            status = "PARTIAL_FAILURE"
        return {
            "batch_id": params.get("batch_id") or "",
            "project_identity": params.get("project_identity") or "",
            "batch_status": status,
            "step_results": results,
        }
