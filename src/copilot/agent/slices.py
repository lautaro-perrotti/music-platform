from __future__ import annotations

import logging
from typing import Any

from copilot.agent.tools import AgentTools, quarter_notes_c3
from copilot.agent.transactions import TransactionManager
from copilot.audio.compare import compare_observations
from copilot.audio.measure import measure_audio
from copilot.audio.render import render_clip_audio, render_with_excess_bass
from copilot.daw.adapter import DawAdapter, DawError
from copilot.daw.write import WriteInDoubt
from copilot.midi.time import bars_to_beats
from copilot.schemas.plan import LegacyMusicPlan, PlannedAction

from copilot.schemas.transaction import TransactionStatus

logger = logging.getLogger("copilot.slices")


def run_create_c3_clip(tools: AgentTools, track_name: str = "AI Test") -> dict[str, Any]:
    with tools.lock.write():
        return _run_create_c3_clip_locked(tools, track_name)


def _run_create_c3_clip_locked(tools: AgentTools, track_name: str) -> dict[str, Any]:
    before = tools.get_session_snapshot()
    txn = tools.transactions.begin(
        user_intent=f"Create MIDI track {track_name} with 4 bars of C3 quarter notes",
        session=before,
    )
    try:
        if before.track_by_name(track_name) is not None:
            raise DawError(f"Track {track_name!r} already exists")
        created = tools.create_midi_track(
            track_name, expected_revision=before.revision
        )
        beats_per_bar = int(
            bars_to_beats(
                1,
                before.transport.signature_numerator,
                before.transport.signature_denominator,
            )
        )
        clip_length = bars_to_beats(
            4,
            before.transport.signature_numerator,
            before.transport.signature_denominator,
        )
        tools.create_midi_clip(
            created["index"],
            0,
            clip_length,
            name="C3 Quarters",
        )
        tools.replace_clip_notes(
            created["index"],
            0,
            quarter_notes_c3(
                bars=4,
                beats_per_bar=beats_per_bar,
            ),
        )
        verification = tools.verify_track_clip_notes(
            track_name,
            expected_note_count=beats_per_bar * 4,
            expected_pitch=60,
            bars=4,
            beats_per_bar=beats_per_bar,
        )
        after = tools.get_session_snapshot()
        tools.transactions.commit(verification, session=after)
        logger.info("slice1 applied track=%s notes=16", verification["track_id"])
        return {
            "transaction_id": txn.transaction_id,
            "status": TransactionStatus.VERIFIED.value,
            "before_tracks": [track.name for track in before.tracks],
            "after_tracks": [track.name for track in after.tracks],
            "verification": verification,
        }
    except WriteInDoubt as exc:
        tools.transactions.mark_in_doubt(str(exc), getattr(exc, "command_id", ""))
        raise
    except Exception as exc:
        tools.transactions.abort(str(exc))
        raise


def run_undo_last(tools: AgentTools, expected_track: str = "AI Test") -> dict[str, Any]:
    txn = tools.transactions.rollback_last()
    after = tools.get_session_snapshot()
    if txn.status.value == "ROLLBACK_CONFLICT":
        logger.error("rollback conflict txn=%s error=%s", txn.transaction_id, txn.error)
        return {
            "transaction_id": txn.transaction_id,
            "status": txn.status.value,
            "error": txn.error,
            "tracks": [track.name for track in after.tracks],
        }
    leftover = after.track_by_name(expected_track)
    if leftover is not None:
        raise DawError(f"Rollback failed; {expected_track!r} still exists")
    logger.info("slice1 rollback verified txn=%s", txn.transaction_id)
    return {
        "transaction_id": txn.transaction_id,
        "status": txn.status.value,
        "tracks": [track.name for track in after.tracks],
    }


def run_listen_region(tools: AgentTools, track_name: str, sample_rate: int = 44100) -> dict[str, Any]:
    state = tools.get_session_snapshot()
    track = state.track_by_name(track_name)
    if track is None or not track.clips:
        raise DawError("No captured region: track/clip missing")
    clip = track.clips[0]
    audio = render_clip_audio(track, clip, state.transport.tempo, sample_rate)
    observation = measure_audio(
        audio,
        sample_rate,
        source="offline-render",
        region=f"{track.stable_id}:{clip.stable_id}",
        tempo_hint_bpm=state.transport.tempo,
    )
    logger.info(
        "listen rms=%.4f peak=%.4f lufs=%s centroid=%s",
        observation.signal.rms,
        observation.signal.peak,
        observation.signal.lufs,
        observation.signal.spectral_centroid_hz,
    )
    return {"observation": observation.model_dump(), "sample_count": int(audio.size)}


def run_conservative_bass_cut(tools: AgentTools, track_name: str) -> dict[str, Any]:
    state = tools.get_session_snapshot()
    track = state.track_by_name(track_name)
    if track is None or not track.clips:
        raise DawError("Target track/clip missing")
    clip = track.clips[0]
    before_audio = render_with_excess_bass(track, clip, state.transport.tempo)
    before_obs = measure_audio(
        before_audio,
        44100,
        source="offline-render",
        region=f"{track.stable_id}:{clip.stable_id}:before",
        tempo_hint_bpm=state.transport.tempo,
    )
    if before_obs.signal.bass_energy_ratio is None:
        raise DawError("Bass energy could not be measured")
    plan = LegacyMusicPlan(
        goal="Reduce excess low-frequency energy without changing musical content",
        target=track.stable_id,
        constraints=["Do not change notes", "Conservative EQ only"],
        musical_intent="Clear rumble while keeping the C3 part audible",
        actions=[
            PlannedAction(
                operation="set_device_parameter",
                target=track.devices[0].stable_id if track.devices else None,
                params={"parameter": "1 Gain A", "delta": -0.25},
            )
        ],
        validation_rules=["bass_energy_ratio must decrease", "notes must be unchanged"],
        expected_effect="Lower bass_energy_ratio and slightly lower LUFS",
    )
    txn = tools.transactions.begin(
        user_intent="This part has too much bass. Make a conservative correction.",
        session=state,
    )
    if not track.devices:
        tools.transactions.fail("No EQ device available")
        raise DawError("No EQ device available")
    previous = track.devices[0].parameters[0].value
    tools.set_device_parameter(track.index, 0, 0, max(0.0, previous - 0.25), previous)
    after_state = tools.get_session_snapshot()
    after_track = after_state.track_by_id(track.stable_id)
    after_clip = after_track.clips[0]
    after_audio = render_with_excess_bass(
        after_track, after_clip, after_state.transport.tempo
    )
    after_obs = measure_audio(
        after_audio,
        44100,
        source="offline-render",
        region=f"{after_track.stable_id}:{after_clip.stable_id}:after",
        tempo_hint_bpm=after_state.transport.tempo,
    )
    comparison = compare_observations(before_obs, after_obs)
    if (
        after_obs.signal.bass_energy_ratio is None
        or before_obs.signal.bass_energy_ratio is None
        or after_obs.signal.bass_energy_ratio >= before_obs.signal.bass_energy_ratio
    ):
        tools.transactions.fail("Bass energy did not decrease")
        raise DawError("Verification failed: bass energy did not decrease")
    tools.transactions.commit(
        {
            "plan": plan.model_dump(),
            "comparison": comparison,
            "before_obs": before_obs.model_dump(),
            "after_obs": after_obs.model_dump(),
        },
        session=after_state,
    )
    logger.info("listen-modify-compare %s", comparison)
    return {
        "transaction_id": txn.transaction_id,
        "plan": plan.model_dump(),
        "comparison": comparison,
        "before": before_obs.model_dump(),
        "after": after_obs.model_dump(),
    }


def connect_live() -> DawAdapter:
    from copilot.daw.ableton_tcp import AbletonTcpAdapter

    live = AbletonTcpAdapter()
    live.connect()
    if (getattr(live, "handshake_info", {}) or {}).get("backend") == "mock":
        live.disconnect()
        raise DawError("BLOCKED_BY_ENVIRONMENT: mock TCP backend is not Live")
    live.health()
    return live


def connect_mock() -> DawAdapter:
    from copilot.daw.mock import MockAbletonAdapter

    mock = MockAbletonAdapter()
    mock.connect()
    return mock
