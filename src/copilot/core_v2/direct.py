"""Small typed hot path to Live, deliberately parallel to SafeWrite V1.

Only a disposable untitled set (or one explicitly owned by this process) may
be mutated. No arbitrary LOM method names are accepted from callers.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
import time
from typing import Callable, TypeVar

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.detect import detect_ableton
from copilot.platform.modals import KnownModalHandler
from copilot.schemas.session import MidiNote, SessionState


T = TypeVar("T")


@dataclass(frozen=True)
class TrackBatch:
    name: str
    source_name: str
    source_kind: str  # sample | device
    notes: tuple[MidiNote, ...]
    volume: float


@dataclass(frozen=True)
class AbletonBatch:
    tempo: float
    length_beats: float
    tracks: tuple[TrackBatch, ...]

    def validate(self) -> None:
        if not math.isfinite(self.tempo) or not 30 <= self.tempo <= 300:
            raise ValueError("tempo out of range")
        if not math.isfinite(self.length_beats) or not 0 < self.length_beats <= 128:
            raise ValueError("clip length out of range")
        if not 1 <= len(self.tracks) <= 8 or len({t.name for t in self.tracks}) != len(self.tracks):
            raise ValueError("invalid track set")
        for track in self.tracks:
            if not track.name.startswith("FAST V2 - ") or not track.source_name:
                raise ValueError("unnamespaced track or missing source")
            if track.source_kind not in {"sample", "device"}:
                raise ValueError("unknown source type")
            if not math.isfinite(track.volume) or not 0 < track.volume <= 0.85:
                raise ValueError("volume out of range")
            if not track.notes or len(track.notes) > 512:
                raise ValueError("invalid notes")
            for note in track.notes:
                if not 0 <= note.pitch <= 127 or not 1 <= note.velocity <= 127 or not (
                    math.isfinite(note.start_time) and math.isfinite(note.duration)
                    and 0 <= note.start_time < self.length_beats
                    and 0 < note.duration <= self.length_beats - note.start_time + 1e-6
                ):
                    raise ValueError("note out of clip bounds")


def is_pristine_default_set(session: SessionState, arrangement: list[dict]) -> bool:
    """An untitled set is disposable only while its Live defaults are untouched."""
    expected = [("1-MIDI", "midi"), ("2-MIDI", "midi"),
                ("3-Audio", "audio"), ("4-Audio", "audio")]
    return (not arrangement and [(t.name, t.role) for t in session.tracks] == expected
            and all(not t.clips and not t.devices for t in session.tracks))


class DirectAbletonSession:
    def __init__(self) -> None:
        self.daw = AbletonTcpAdapter()
        self.process: subprocess.Popen[bytes] | None = None
        self.pid: int | None = None
        self.initial_path = ""

    def connect(self, deadline_s: float = 180.0) -> "DirectAbletonSession":
        detected = detect_ableton()
        if not detected.exe_path:
            raise RuntimeError("ABLETON_NOT_INSTALLED")
        if not detected.process_running:
            self.process = subprocess.Popen(
                [detected.exe_path], cwd=str(Path(detected.exe_path).parent),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.pid = self.process.pid
        else:
            self.pid = detected.process_pid
        deadline = time.monotonic() + deadline_s
        modal = KnownModalHandler()
        last_error = ""
        while time.monotonic() < deadline:
            try:
                self.daw.connect()
                self.initial_path = str(self.daw.get_session_path().get("path") or "")
                self.daw.get_session_info()
            except Exception as exc:  # bridge may not be up yet
                last_error = str(exc)
                self.daw.disconnect()
                observation = modal.inspect()
                if observation.get("kind") == "TRIAL_STATUS_ACKNOWLEDGEMENT":
                    modal.acknowledge_trial(observation)
                elif observation.get("status") == "MODAL_PRESENT":
                    raise RuntimeError(f"UNKNOWN_ABLETON_MODAL: {observation.get('kind')}") from exc
                if self.process is not None and self.process.poll() is not None:
                    raise RuntimeError("ABLETON_EXITED_BEFORE_BRIDGE") from exc
                time.sleep(0.5)
                continue
            if self.initial_path:
                self.daw.disconnect()
                raise RuntimeError(f"UNKNOWN_SAVED_PROJECT: {self.initial_path}")
            return self
        raise RuntimeError(f"BRIDGE_TIMEOUT: {last_error}")

    def reconnect(self) -> AbletonTcpAdapter:
        self.daw.disconnect()
        self.daw = AbletonTcpAdapter()
        self.daw.connect()
        if str(self.daw.get_session_path().get("path") or "") != self.initial_path:
            raise RuntimeError("SESSION_CHANGED_AFTER_TIMEOUT")
        return self.daw

    def close(self) -> None:
        self.daw.disconnect()


def resolve_timeout(
    invoke: Callable[[], T], reconnect_and_snapshot: Callable[[], SessionState],
    postcondition: Callable[[SessionState], bool],
) -> T | None:
    """On transport timeout, read actual Live state once; never blindly retry."""
    try:
        return invoke()
    except Exception as exc:
        if "Timeout waiting for Ableton" not in str(exc):
            raise
        if postcondition(reconnect_and_snapshot()):
            return None
        raise RuntimeError("DIRECT_WRITE_TIMEOUT_POSTCONDITION_MISSING") from exc


class DirectAbletonExecutor:
    def __init__(self, session: DirectAbletonSession) -> None:
        self.session = session
        self.timeouts = 0

    @property
    def daw(self) -> AbletonTcpAdapter:
        return self.session.daw

    def _source_uri(self, name: str) -> str:
        found = self.daw.search_browser(name.rsplit(".", 1)[0], "all")
        rows = [r for r in (found.get("items") or found.get("results") or [])
                if r.get("name") == name and r.get("is_loadable")]
        if len(rows) != 1:
            raise RuntimeError(f"BROWSER_SOURCE_NOT_UNAMBIGUOUS: {name} matches={len(rows)}")
        return str(rows[0]["uri"])

    def _load(self, track_index: int, spec: TrackBatch, uri: str) -> None:
        def postcondition(s: SessionState) -> bool:
            target = next((t for t in s.tracks if t.index == track_index and t.name == spec.name), None)
            return target is not None and bool(target.devices)
        try:
            resolve_timeout(
                lambda: (self.daw.load_browser_item(track_index, uri, 0)
                         if spec.source_kind == "sample" else self.daw.load_instrument_or_effect(track_index, uri)),
                lambda: self.session.reconnect().snapshot(include_notes=False), postcondition,
            )
        except RuntimeError as exc:
            if "DIRECT_WRITE_TIMEOUT" in str(exc):
                self.timeouts += 1
            raise

    def apply(self, batch: AbletonBatch) -> dict:
        batch.validate()
        info = self.daw.get_session_info()
        if self.session.initial_path or self.daw.get_session_path().get("path"):
            raise RuntimeError("DIRECT_EXECUTOR_REQUIRES_DISPOSABLE_UNTITLED_SET")
        before = self.daw.snapshot(include_notes=False)
        if not is_pristine_default_set(before, self.daw.get_arrangement_clips().get("clips") or []):
            raise RuntimeError("UNTITLED_SET_CONTAINS_UNKNOWN_WORK")
        requested = {t.name for t in batch.tracks}
        if requested.intersection(t.name for t in before.tracks):
            raise RuntimeError("DIRECT_TRACKS_ALREADY_EXIST")
        self.daw.stop_playback()
        self.daw.set_tempo(batch.tempo)
        created: dict[str, int] = {}
        for spec in batch.tracks:
            uri = self._source_uri(spec.source_name)
            created[spec.name] = int(self.daw.create_midi_track(spec.name)["index"])
            index = created[spec.name]
            self._load(index, spec, uri)
            self.daw.create_midi_clip(index, 0, batch.length_beats)
            self.daw.replace_clip_notes(index, 0, list(spec.notes))
            self.daw.duplicate_clip_to_arrangement(index, 0, 0.0)
            self.daw.set_mixer_volume(index, spec.volume)
        final = self.daw.snapshot(include_notes=False)
        arrangement = self.daw.get_arrangement_clips().get("clips") or []
        details: dict[str, dict] = {}
        for spec in batch.tracks:
            track = next((t for t in final.tracks if t.name == spec.name), None)
            if track is None or not track.devices:
                raise RuntimeError(f"TRACK_OR_DEVICE_READBACK_FAILED: {spec.name}")
            notes = self.daw.get_clip_notes(track.index, 0).get("notes") or []
            placed = [c for c in arrangement if c.get("track_index") == track.index]
            if len(notes) != len(spec.notes) or not placed:
                raise RuntimeError(f"CLIP_OR_ARRANGEMENT_READBACK_FAILED: {spec.name}")
            details[spec.name] = {"index": track.index, "devices": [d.name for d in track.devices],
                                  "note_count": len(notes), "arrangement": placed,
                                  "volume": track.mixer.volume}
        if abs(final.transport.tempo - batch.tempo) > 0.01:
            raise RuntimeError("TEMPO_READBACK_FAILED")
        return {"tempo": final.transport.tempo, "tracks": details,
                "track_count": len(final.tracks), "snapshot_calls": self.daw.snapshot_calls,
                "rpc": self.daw.tcp_stats(), "timeouts": self.timeouts,
                "initial_track_count": info["track_count"]}
