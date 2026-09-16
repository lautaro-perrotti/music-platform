from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import uuid4

from copilot.agent.transactions import TransactionManager
from copilot.daw.adapter import DawAdapter, DawError
from copilot.daw.identities import fingerprint_track
from copilot.daw.lock import SessionMutationLock
from copilot.daw.protocol import CAPABILITIES, require_capability
from copilot.daw.validation import (
    validate_device_parameter,
    validate_midi_notes,
    validate_mixer_volume,
)
from copilot.daw.write import ReconcileResult, WriteClass, WriteInDoubt, spec_for
from copilot.schemas.session import MidiNote, SessionState, TrackState
from copilot.schemas.transaction import TargetFingerprint, TargetLocator

logger = logging.getLogger("copilot.write")

ABLETON_C3 = 60  # Live's C3 display name only; canonical pitch is 60.


class AgentTools:
    """Explicit, typed agent tools. No arbitrary Live execution."""

    def __init__(self, daw: DawAdapter, transactions: TransactionManager) -> None:
        self.daw = daw
        self.transactions = transactions
        self.lock = SessionMutationLock()
        advertised = getattr(daw, "capabilities", None)
        self.capabilities: set[str] = set(advertised or CAPABILITIES)

    def get_session_snapshot(self) -> SessionState:
        state = self.daw.snapshot()
        logger.debug(
            json.dumps(
                {
                    "operation": "get_session_snapshot",
                    "revision": state.revision,
                    "state_hash": state.state_hash,
                    "tracks": len(state.tracks),
                    "tempo": state.transport.tempo,
                    "result": "ok",
                },
                sort_keys=True,
            )
        )
        return state

    def require_revision(
        self, expected: int, expected_hash: str | None = None
    ) -> SessionState:
        current = self.daw.snapshot()
        if current.revision != expected:
            raise DawError(
                f"Stale revision: expected {expected}, live snapshot is {current.revision}"
            )
        if expected_hash and current.state_hash != expected_hash:
            raise DawError("Stale state hash")
        return current

    def authorize_plan(self, envelope) -> SessionState:
        from copilot.daw.plan_envelope import authorize_execution

        current = self.daw.snapshot()
        authorize_execution(current, envelope)
        return current

    def create_midi_track(
        self, name: str, index: int = -1, expected_revision: int | None = None
    ) -> dict[str, Any]:
        with self.lock.write():
            before_state = self._pre_write(
                "create_midi_track", expected_revision=expected_revision
            )
            command_id = self._command_id()
            before = {"exists": False, "name": name, "track_count": len(before_state.tracks)}
            expected_after = {"exists": True, "name": name, "track_count": len(before_state.tracks) + 1}
            self.transactions.plan_write(
                command_id=command_id,
                operation="create_midi_track",
                expected_revision=before_state.revision,
                before=before,
                expected_after=expected_after,
                target_name_at_apply=name,
            )
            created = self._execute_write(
                "create_midi_track",
                command_id,
                before,
                expected_after,
                lambda: self.daw.create_midi_track(name, index),
            )
            track = self._track_at(int(created["index"]))
            self.transactions.record(
                target_stable_id=track.stable_id,
                target_locator_at_apply=TargetLocator(track_index=track.index),
                target_fingerprint=TargetFingerprint(**fingerprint_track(track)),
                target_name_at_apply=track.name,
                operation="create_midi_track",
                before=before,
                after=created,
                expected_after=expected_after,
                inverse_operation="delete_track",
                inverse_params={},
                asset_id=track.stable_id,
                command_id=command_id,
                expected_revision=before_state.revision,
            )
            self._log_write(
                "create_midi_track",
                command_id,
                track,
                before_state.revision,
                "APPLIED",
            )
            return created

    def create_midi_clip(
        self, track_index: int, clip_index: int, length_beats: float, name: str = ""
    ) -> dict[str, Any]:
        with self.lock.write():
            before_state = self._pre_write("create_midi_clip")
            command_id = self._command_id()
            before = {"exists": False}
            expected_after = {"exists": True, "clip_index": clip_index}
            self.transactions.plan_write(
                command_id=command_id,
                operation="create_midi_clip",
                expected_revision=before_state.revision,
                before=before,
                expected_after=expected_after,
            )
            created = self._execute_write(
                "create_midi_clip",
                command_id,
                before,
                expected_after,
                lambda: self.daw.create_midi_clip(track_index, clip_index, length_beats),
            )
            if name:
                created = self.daw.set_clip_name(track_index, clip_index, name)
                created["length"] = length_beats
            track = self._track_at(track_index)
            self.transactions.record(
                target_stable_id=track.stable_id,
                target_locator_at_apply=TargetLocator(
                    track_index=track.index, clip_index=clip_index
                ),
                target_fingerprint=TargetFingerprint(**fingerprint_track(track)),
                target_name_at_apply=track.name,
                operation="create_midi_clip",
                before=before,
                after={"track_index": track_index, "clip_index": clip_index, **created},
                expected_after=expected_after,
                inverse_operation="delete_clip",
                inverse_params={},
                asset_id=f"{track.stable_id}:clip:{clip_index}",
                command_id=command_id,
                expected_revision=before_state.revision,
            )
            return created

    def replace_clip_notes(
        self, track_index: int, clip_index: int, notes: list[MidiNote]
    ) -> dict[str, Any]:
        notes = validate_midi_notes(notes)
        with self.lock.write():
            before_state = self._pre_write("replace_clip_notes")
            command_id = self._command_id()
            previous = self.daw.get_clip_notes(track_index, clip_index)
            before = {"notes": previous.get("notes", [])}
            expected_after = {"note_count": len(notes), "pitch": notes[0].pitch if notes else None}
            self.transactions.plan_write(
                command_id=command_id,
                operation="replace_clip_notes",
                expected_revision=before_state.revision,
                before=before,
                expected_after=expected_after,
            )
            result = self._execute_write(
                "replace_clip_notes",
                command_id,
                before,
                expected_after,
                lambda: self.daw.replace_clip_notes(track_index, clip_index, notes),
            )
            track = self._track_at(track_index)
            self.transactions.record(
                target_stable_id=track.stable_id,
                target_locator_at_apply=TargetLocator(
                    track_index=track.index, clip_index=clip_index
                ),
                target_fingerprint=TargetFingerprint(**fingerprint_track(track)),
                target_name_at_apply=track.name,
                operation="replace_clip_notes",
                before=before,
                after={"notes": [note.model_dump() for note in notes]},
                expected_after=expected_after,
                inverse_operation="replace_clip_notes",
                inverse_params={"notes": previous.get("notes", [])},
                command_id=command_id,
                expected_revision=before_state.revision,
            )
            return result

    def set_mixer_volume(self, track_index: int, volume: float) -> dict[str, Any]:
        volume = validate_mixer_volume(volume)
        with self.lock.write():
            before_state = self._pre_write("set_mixer_volume")
            command_id = self._command_id()
            track = self._track_at(track_index)
            before_volume = track.mixer.volume
            before = {"volume": before_volume}
            expected_after = {"volume": volume}
            self.transactions.plan_write(
                command_id=command_id,
                operation="set_mixer_volume",
                expected_revision=before_state.revision,
                before=before,
                expected_after=expected_after,
                target_stable_id=track.stable_id,
            )
            result = self._execute_write(
                "set_mixer_volume",
                command_id,
                before,
                expected_after,
                lambda: self.daw.set_mixer_volume(track_index, volume),
            )
            track = self._track_at(track_index)
            self.transactions.record(
                target_stable_id=track.stable_id,
                target_locator_at_apply=TargetLocator(track_index=track.index),
                target_fingerprint=TargetFingerprint(**fingerprint_track(track)),
                target_name_at_apply=track.name,
                operation="set_mixer_volume",
                before=before,
                after={"volume": result["volume"]},
                expected_after=expected_after,
                inverse_operation="set_mixer_volume",
                inverse_params={"volume": before_volume},
                command_id=command_id,
                expected_revision=before_state.revision,
            )
            return result

    def set_device_parameter(
        self,
        track_index: int,
        device_index: int,
        parameter_index: int,
        value: float,
        previous: float,
    ) -> dict[str, Any]:
        track = self._track_at(track_index)
        parameter = None
        if track.devices and 0 <= device_index < len(track.devices):
            device = track.devices[device_index]
            if 0 <= parameter_index < len(device.parameters):
                parameter = device.parameters[parameter_index]
        value = validate_device_parameter(value, parameter)
        with self.lock.write():
            before_state = self._pre_write("set_device_parameter")
            command_id = self._command_id()
            before = {"value": previous}
            expected_after = {"value": value}
            self.transactions.plan_write(
                command_id=command_id,
                operation="set_device_parameter",
                expected_revision=before_state.revision,
                before=before,
                expected_after=expected_after,
                target_stable_id=track.stable_id,
            )
            result = self._execute_write(
                "set_device_parameter",
                command_id,
                before,
                expected_after,
                lambda: self.daw.set_device_parameter(
                    track_index, device_index, parameter_index, value
                ),
            )
            track = self._track_at(track_index)
            self.transactions.record(
                target_stable_id=track.stable_id,
                target_locator_at_apply=TargetLocator(
                    track_index=track.index,
                    device_index=device_index,
                    parameter_index=parameter_index,
                ),
                target_fingerprint=TargetFingerprint(**fingerprint_track(track)),
                target_name_at_apply=track.name,
                operation="set_device_parameter",
                before=before,
                after={"value": result["value"]},
                expected_after=expected_after,
                inverse_operation="set_device_parameter",
                inverse_params={"value": previous},
                command_id=command_id,
                expected_revision=before_state.revision,
            )
            return result

    def _pre_write(
        self, operation: str, expected_revision: int | None = None
    ) -> SessionState:
        write_class, _permission = spec_for(operation)
        if write_class is WriteClass.READ_ONLY:
            raise DawError("read-only operation cannot write")
        wire = {
            "create_midi_track": "create_midi_track",
            "create_midi_clip": "create_clip",
            "replace_clip_notes": "add_notes_to_clip",
            "set_mixer_volume": "set_track_volume",
            "set_device_parameter": "set_device_parameter",
            "delete_track": "delete_track",
            "delete_clip": "delete_clip",
            "set_track_name": "set_track_name",
            "set_clip_name": "set_clip_name",
        }.get(operation, operation)
        require_capability(self.capabilities, wire)
        state = self.get_session_snapshot()
        if expected_revision is not None:
            state = self.require_revision(expected_revision)
        return state

    def _execute_write(
        self,
        operation: str,
        command_id: str,
        before: dict[str, Any],
        expected_after: dict[str, Any],
        send,
    ) -> dict[str, Any]:
        write_class, _permission = spec_for(operation)
        self.transactions.mark_sent(command_id, operation)
        started = time.perf_counter()
        try:
            result = send()
        except WriteInDoubt as exc:
            session = self.get_session_snapshot()
            outcome = self.transactions.reconcile(
                operation=operation,
                before=before,
                expected_after=expected_after,
                session=session,
            )
            if outcome is ReconcileResult.SATISFIED:
                logger.info(
                    json.dumps(
                        {
                            "operation": operation,
                            "command_id": command_id,
                            "result": "APPLIED",
                            "reconciled": True,
                            "duration": time.perf_counter() - started,
                        },
                        sort_keys=True,
                    )
                )
                recovered = {"reconciled": True, **expected_after}
                if operation == "create_midi_track":
                    matches = [
                        track
                        for track in session.tracks
                        if track.name == expected_after.get("name")
                    ]
                    if len(matches) == 1:
                        recovered["index"] = matches[0].index
                        recovered["name"] = matches[0].name
                return recovered
            if outcome is ReconcileResult.ABSENT and write_class is WriteClass.IDEMPOTENT_WRITE:
                result = send()
                return result
            if outcome is ReconcileResult.ABSENT and write_class is WriteClass.NON_IDEMPOTENT_WRITE:
                result = send()
                return result
            raise WriteInDoubt(operation, command_id) from exc
        return result

    def _track_at(self, track_index: int) -> TrackState:
        state = self.get_session_snapshot()
        if track_index < 0 or track_index >= len(state.tracks):
            raise DawError("Track index out of range after mutation")
        return state.tracks[track_index]

    def _command_id(self) -> str:
        return f"cmd_{uuid4().hex[:12]}"

    def _log_write(
        self,
        operation: str,
        command_id: str,
        track: TrackState,
        revision_before: int,
        result: str,
    ) -> None:
        after = self.get_session_snapshot()
        payload = {
            "transaction_id": getattr(self.transactions._open, "transaction_id", None),
            "command_id": command_id,
            "session_id": after.session_incarnation_id,
            "target_stable_id": track.stable_id,
            "locator": track.index,
            "revision_before": revision_before,
            "revision_after": after.revision,
            "operation": operation,
            "result": result,
        }
        logger.info(json.dumps(payload, sort_keys=True))

    def verify_track_clip_notes(
        self,
        track_name: str,
        expected_note_count: int,
        expected_pitch: int,
        bars: int,
        beats_per_bar: int,
    ) -> dict[str, Any]:
        state = self.get_session_snapshot()
        track = state.track_by_name(track_name)
        if track is None:
            raise DawError(f"Track {track_name!r} not found after apply")
        if not track.clips:
            raise DawError(f"Track {track_name!r} has no clips")
        clip = track.clips[0]
        notes = clip.notes
        if len(notes) != expected_note_count:
            raise DawError(
                f"Expected {expected_note_count} notes, found {len(notes)}"
            )
        if any(note.pitch != expected_pitch for note in notes):
            raise DawError("Unexpected pitch in written notes")
        starts = [note.start_time for note in notes]
        expected_starts = [
            float(bar * beats_per_bar + beat)
            for bar in range(bars)
            for beat in range(beats_per_bar)
        ]
        if starts != expected_starts:
            raise DawError(f"Unexpected note times: {starts}")
        if abs(clip.length_beats - float(bars * beats_per_bar)) > 1e-6:
            raise DawError(f"Unexpected clip length: {clip.length_beats}")
        return {
            "track_id": track.stable_id,
            "track_index": track.index,
            "clip_id": clip.stable_id,
            "note_count": len(notes),
            "pitch": expected_pitch,
            "postcondition": "SATISFIED",
        }


def quarter_notes_c3(bars: int = 4, beats_per_bar: int = 4) -> list[MidiNote]:
    from copilot.midi.time import notes_on_beats

    return notes_on_beats(pitch=ABLETON_C3, bars=bars, numerator=beats_per_bar, denominator=4)
