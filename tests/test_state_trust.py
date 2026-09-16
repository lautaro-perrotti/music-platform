from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from copilot.audio.asset_cache import (
    REVISION_FENCE_REQUIRED,
    AssetCache,
    AssetCacheKey,
    lookup,
    make_key,
)
from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.object_ref import ResolveStatus, ref_from_track, resolve_track
from copilot.daw.plan_envelope import authorize_execution, observe_plan
from copilot.daw.state_errors import (
    PROJECT_MISMATCH,
    STALE_PLAN,
    TARGET_AMBIGUOUS,
    TARGET_NOT_FOUND,
    StateTrustError,
)
from copilot.daw.state_tokens import (
    MUTATION_TABLE,
    StateScope,
    audible_token,
    canonical_audible,
    canonical_project,
    canonical_target,
    dumps_canonical,
    project_identity_token,
    project_token,
    target_token,
    token_of,
)
from copilot.schemas.session import MidiNote


def _daw(*names: str) -> MockAbletonAdapter:
    daw = MockAbletonAdapter()
    daw.connect()
    daw.session_path = r"D:\sets\lab.als"
    daw.session_name = "lab"
    for name in names:
        daw.create_midi_track(name)
    return daw


def _reconnect(daw: MockAbletonAdapter) -> MockAbletonAdapter:
    other = MockAbletonAdapter()
    other.tracks = deepcopy(daw.tracks)
    other.transport = daw.transport.model_copy()
    other.session_path = daw.session_path
    other.session_name = daw.session_name
    other.connect()
    return other


def test_same_semantic_state_same_tokens() -> None:
    daw = _daw("Kick")
    first = daw.snapshot()
    second = daw.snapshot()
    assert first.project_token == second.project_token
    assert first.audible_token == second.audible_token
    assert first.project_identity == second.project_identity
    kick = first.track_by_name("Kick")
    assert kick is not None
    assert target_token(kick) == target_token(second.track_by_name("Kick"))


def test_canonical_bytes_stable_under_dict_reorder() -> None:
    daw = _daw("A")
    session = daw.snapshot()
    body = canonical_project(session)
    shuffled = json.loads(json.dumps(body))
    assert dumps_canonical(body) == dumps_canonical(shuffled)
    assert token_of(body) == token_of(shuffled)


def test_canonical_excludes_runtime_and_volatile_fields() -> None:
    daw = _daw("Kick")
    session = daw.snapshot()
    blob = dumps_canonical(canonical_project(session)).decode("utf-8")
    blob += dumps_canonical(canonical_audible(session)).decode("utf-8")
    kick = session.track_by_name("Kick")
    assert kick is not None
    blob += dumps_canonical(canonical_target(kick)).decode("utf-8")
    assert "stable_id" not in blob
    assert "sess_" not in blob
    assert "trk_" not in blob
    assert "position_beats" not in blob
    assert "selected_track" not in blob


def test_playback_and_selection_do_not_change_tokens() -> None:
    daw = _daw("Kick")
    before = daw.snapshot()
    daw.transport.playing = True
    daw.transport.position_beats = 9.25
    after = daw.snapshot()
    after.selected_track_id = after.tracks[0].stable_id
    assert after.project_token == before.project_token
    assert after.audible_token == before.audible_token
    assert target_token(after.tracks[0]) == target_token(before.tracks[0])


def test_mutation_table_volume_routing_rename_reorder_midi() -> None:
    assert any(row["mutation"] == "track_volume" for row in MUTATION_TABLE)
    daw = _daw("Kick", "Pad")
    base = daw.snapshot()
    kick = base.track_by_name("Kick")
    pad = base.track_by_name("Pad")
    assert kick is not None and pad is not None
    kick_ref = ref_from_track(kick, project_identity=base.project_identity)
    kick_target = target_token(kick)
    kick_audible = audible_token(base, track_names=frozenset({"Kick"}))
    project = base.project_token

    daw.set_mixer_volume(kick.index, 0.2)
    vol = daw.snapshot()
    assert vol.project_token == project
    assert vol.audible_token != base.audible_token
    assert target_token(vol.track_by_name("Kick")) != kick_target
    assert audible_token(vol, track_names=frozenset({"Pad"})) == audible_token(
        base, track_names=frozenset({"Pad"})
    )

    daw = _daw("Kick", "Pad")
    base = daw.snapshot()
    daw.set_track_output_routing(0, "Sends Only", "")
    routed = daw.snapshot()
    assert routed.project_token != base.project_token
    assert routed.audible_token != base.audible_token

    daw = _daw("Kick", "Pad")
    base = daw.snapshot()
    daw.set_track_name(0, "KickRenamed")
    renamed = daw.snapshot()
    assert renamed.project_token != base.project_token
    assert renamed.audible_token == base.audible_token
    assert target_token(renamed.track_by_name("KickRenamed")) != target_token(
        base.track_by_name("Kick")
    )

    daw = _daw("Kick", "Pad")
    base = daw.snapshot()
    daw.tracks[0], daw.tracks[1] = daw.tracks[1], daw.tracks[0]
    reordered = daw.snapshot()
    assert reordered.project_token != base.project_token
    assert reordered.audible_token == base.audible_token
    assert target_token(reordered.track_by_name("Kick")) == target_token(
        base.track_by_name("Kick")
    )

    daw = _daw("Kick")
    daw.create_midi_clip(0, 0, 4.0)
    base = daw.snapshot()
    daw.replace_clip_notes(0, 0, [MidiNote(pitch=60, start_time=0.0, duration=1.0)])
    midi = daw.snapshot()
    assert midi.project_token != base.project_token
    assert midi.audible_token != base.audible_token
    assert target_token(midi.tracks[0]) != target_token(base.tracks[0])


def test_insert_unrelated_preserves_target_and_scoped_audible() -> None:
    daw = _daw("Kick")
    before = daw.snapshot()
    kick = before.track_by_name("Kick")
    assert kick is not None
    ref = ref_from_track(kick, project_identity=before.project_identity)
    scoped = audible_token(before, track_names=frozenset({"Kick"}))
    daw.create_midi_track("Unrelated", index=0)
    after = daw.snapshot()
    assert after.project_token != before.project_token
    assert after.audible_token != before.audible_token
    assert audible_token(after, track_names=frozenset({"Kick"})) == scoped
    resolved = resolve_track(after, ref)
    assert resolved.status is ResolveStatus.RESOLVED
    assert after.tracks[resolved.track_index].name == "Kick"


def test_rename_and_reorder_resolve_identity() -> None:
    daw = _daw("Kick", "Pad")
    daw.create_midi_clip(0, 0, 4.0)
    before = daw.snapshot()
    kick = before.track_by_name("Kick")
    assert kick is not None
    runtime_id = kick.stable_id
    ref = ref_from_track(kick, project_identity=before.project_identity)
    daw.set_track_name(kick.index, "Kicker")
    daw.tracks[0], daw.tracks[1] = daw.tracks[1], daw.tracks[0]
    after = daw.snapshot()
    resolved = resolve_track(after, ref)
    assert resolved.status is ResolveStatus.RESOLVED
    found = after.tracks[resolved.track_index]
    assert found.name == "Kicker"
    assert found.stable_id == runtime_id


def test_delete_is_not_found_and_duplicates_are_ambiguous() -> None:
    daw = _daw("Kick")
    before = daw.snapshot()
    ref = ref_from_track(before.tracks[0], project_identity=before.project_identity)
    daw.delete_track(0)
    gone = daw.snapshot()
    assert resolve_track(gone, ref).status is ResolveStatus.TARGET_NOT_FOUND

    daw = _daw("Kick")
    daw.create_midi_track("Kick")
    session = daw.snapshot()
    ref = ref_from_track(session.tracks[0], project_identity=session.project_identity)
    assert resolve_track(session, ref).status is ResolveStatus.TARGET_AMBIGUOUS


def test_reconnect_same_project_does_not_need_runtime_id() -> None:
    daw = _daw("Kick", "Pad")
    before = daw.snapshot()
    kick = before.track_by_name("Kick")
    assert kick is not None
    ref = ref_from_track(kick, project_identity=before.project_identity)
    old_runtime = kick.stable_id
    other = _reconnect(daw)
    after = other.snapshot()
    assert after.session_incarnation_id != before.session_incarnation_id
    assert after.project_identity == before.project_identity
    resolved = resolve_track(after, ref)
    assert resolved.status is ResolveStatus.RESOLVED
    found = after.tracks[resolved.track_index]
    assert found.name == "Kick"
    assert found.stable_id != old_runtime


def test_different_project_mismatches() -> None:
    daw = _daw("Kick")
    before = daw.snapshot()
    ref = ref_from_track(before.tracks[0], project_identity=before.project_identity)
    other = _reconnect(daw)
    other.session_path = r"D:\sets\other.als"
    other.session_name = "other"
    after = other.snapshot()
    assert after.project_identity != before.project_identity
    assert resolve_track(after, ref).status is ResolveStatus.PROJECT_MISMATCH


def test_stale_plan_relevant_vs_irrelevant() -> None:
    daw = _daw("Kick", "Pad")
    session = daw.snapshot()
    kick = session.track_by_name("Kick")
    assert kick is not None
    volume_plan = observe_plan(
        session, scope=StateScope.TARGET, track=kick, intended_operation="set_volume"
    )
    project_plan = observe_plan(
        session, scope=StateScope.PROJECT, track=kick, intended_operation="reorder"
    )
    authorize_execution(session, volume_plan)

    daw.set_mixer_volume(kick.index, 0.1)
    with pytest.raises(StateTrustError) as exc:
        authorize_execution(daw.snapshot(), volume_plan)
    assert exc.value.code == STALE_PLAN

    daw = _daw("Kick", "Pad")
    session = daw.snapshot()
    kick = session.track_by_name("Kick")
    target_plan = observe_plan(session, scope=StateScope.TARGET, track=kick)
    daw.set_mixer_volume(session.track_by_name("Pad").index, 0.1)
    authorize_execution(daw.snapshot(), target_plan)

    daw = _daw("Kick", "Pad")
    session = daw.snapshot()
    kick = session.track_by_name("Kick")
    project_plan = observe_plan(session, scope=StateScope.PROJECT, track=kick)
    daw.tracks[0], daw.tracks[1] = daw.tracks[1], daw.tracks[0]
    with pytest.raises(StateTrustError) as exc:
        authorize_execution(daw.snapshot(), project_plan)
    assert exc.value.code == STALE_PLAN


def test_stale_plan_journal_fields() -> None:
    daw = _daw("Kick")
    session = daw.snapshot()
    plan = observe_plan(session, scope=StateScope.TARGET, track=session.tracks[0])
    daw.set_mixer_volume(0, 0.3)
    with pytest.raises(StateTrustError) as exc:
        authorize_execution(daw.snapshot(), plan)
    journal = exc.value.details["journal"]
    assert journal["observed_project_token"] == session.project_identity
    assert journal["observed_state_token"] == plan.observed_state_token
    assert journal["current_pre_write_token"]
    assert journal["target_resolution"]["status"] == "RESOLVED"
    assert journal["code"] == STALE_PLAN


def test_ambiguity_fails_closed_no_write() -> None:
    daw = _daw("Kick")
    daw.create_midi_track("Kick")
    session = daw.snapshot()
    plan = observe_plan(session, scope=StateScope.TARGET, track=session.tracks[0])
    with pytest.raises(StateTrustError) as exc:
        authorize_execution(session, plan)
    assert exc.value.code == TARGET_AMBIGUOUS


def test_asset_cache_reuse_and_invalidation(tmp_path: Path) -> None:
    assert REVISION_FENCE_REQUIRED is False
    daw = _daw("Kick", "Vocal")
    session = daw.snapshot()
    wav = tmp_path / "kick.wav"
    wav.write_bytes(b"RIFF0000WAVEfake")
    cache = AssetCache()
    kick_key = make_key(
        session,
        region="REGION_A",
        source="Kick",
        view="TRACK_ISOLATED",
        signal_point="TRACK_POST_MIXER",
        capture_protocol="3",
        sample_rate=44100,
        source_name="Kick",
    )
    master_key = make_key(
        session,
        region="REGION_A",
        source="MASTER",
        view="MASTER_CONTEXT",
        signal_point="MAIN_FINAL",
        capture_protocol="3",
        sample_rate=44100,
    )
    vocal_key = make_key(
        session,
        region="REGION_A",
        source="Vocal",
        view="TRACK_ISOLATED",
        signal_point="TRACK_POST_MIXER",
        capture_protocol="3",
        sample_rate=44100,
        source_name="Vocal",
    )
    payload = {
        "file_path": str(wav),
        "sha256": __import__("hashlib").sha256(wav.read_bytes()).hexdigest(),
        "journal_status": "VERIFIED",
        "payload": "kick-wav",
    }
    cache.put(kick_key, payload)
    cache.put(master_key, {**payload, "payload": "master-wav"})
    cache.put(vocal_key, {**payload, "payload": "vocal-wav"})
    assert cache.lookup(kick_key)["payload"] == "kick-wav"

    daw.set_mixer_volume(session.track_by_name("Kick").index, 0.2)
    mutated = daw.snapshot()
    kick_after = make_key(
        mutated,
        region="REGION_A",
        source="Kick",
        view="TRACK_ISOLATED",
        signal_point="TRACK_POST_MIXER",
        capture_protocol="3",
        sample_rate=44100,
        source_name="Kick",
    )
    vocal_after = make_key(
        mutated,
        region="REGION_A",
        source="Vocal",
        view="TRACK_ISOLATED",
        signal_point="TRACK_POST_MIXER",
        capture_protocol="3",
        sample_rate=44100,
        source_name="Vocal",
    )
    master_after = make_key(
        mutated,
        region="REGION_A",
        source="MASTER",
        view="MASTER_CONTEXT",
        signal_point="MAIN_FINAL",
        capture_protocol="3",
        sample_rate=44100,
    )
    assert cache.lookup(kick_after) is None
    assert cache.lookup(vocal_after)["payload"] == "vocal-wav"
    assert cache.lookup(master_after) is None


def test_asset_cache_legacy_lookup_helper() -> None:
    key = AssetCacheKey(
        project_identity="p",
        audible_token="a",
        region="REGION_A",
        source="MASTER",
        view="MASTER_CONTEXT",
        signal_point="MAIN_FINAL",
        capture_protocol="3",
        sample_rate=44100,
    )
    store = {AssetCache()._slot(key): {"payload": "hit", "key": key.as_dict()}}
    assert lookup(key, store) == "hit"


def test_project_identity_uses_path_not_track_layout() -> None:
    daw = _daw("Kick")
    first = daw.snapshot()
    daw.create_midi_track("Other")
    second = daw.snapshot()
    assert first.project_identity == second.project_identity
    assert first.project_token != second.project_token
    assert project_identity_token(first, path=first.project_path, name=first.project_name) == (
        first.project_identity
    )
