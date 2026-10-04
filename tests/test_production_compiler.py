from copilot.daw.mock import MockAbletonAdapter
from copilot.musicplan import (
    build_create_track_action,
    build_device_load_action,
    build_device_tweak_action,
    build_duplicate_clip_to_arrangement_action,
    build_place_audio_clip_once_action,
    build_sample_load_action,
    create_controlled_volume_plan,
)
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.musicplan import (
    ActionTarget,
    ArrangementDuplicateActionParams,
    DeviceLoadActionParams,
    DiagnosisBinding,
    ExpectedEffect,
    MusicPlan,
    PatternActionParams,
    PlanAction,
    PlanIntentClass,
    PlanStatus,
    ProductionActionKind,
)
from copilot.schemas.session import MidiNote
from copilot.schemas.musicplan import RollbackSpec
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.human_eval.store import now_iso


def _session():
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track("Compiler Test")
    session = daw.snapshot()
    return daw, session


def test_compiler_emits_core_intent_for_certified_volume():
    _daw, session = _session()
    track = session.tracks[0]
    plan = create_controlled_volume_plan(session=session, track=track, delta=-0.15)
    result = ProductionCompiler().compile(plan, session=session)
    assert result.status == "COMPILED"
    assert result.intent is not None
    assert result.intent.executions[0].certified is True
    assert result.intent.executions[0].action_type == "SET_TRACK_VOLUME"


def test_compiler_rejects_actions_outside_producer_execution_v1():
    _daw, session = _session()
    track = session.tracks[0]
    plan = create_controlled_volume_plan(session=session, track=track, delta=-0.15)
    plan.actions[0].action_type = ProductionActionKind.CREATE_PATTERN
    result = ProductionCompiler().compile(plan, session=session)
    assert result.status == "PLAN_REJECTED"
    assert "CREATE_PATTERN_REQUIRES_COPILOT_TRACK_COMPOUND_PLAN" in result.reasons
    assert result.intent is None


def test_sample_load_executes_and_rolls_back_through_safewrite(tmp_path):
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_audio_track("Sample Target")
    session = daw.snapshot()
    attach_tokens(session)
    track = session.tracks[0]
    action = build_sample_load_action(
        track=track,
        project_identity=session.project_identity,
        clip_index=0,
        sample_uri="library://kick.wav",
        reason="load the selected kick",
        evidence_refs=[],
        session_incarnation_id=session.session_incarnation_id,
    )
    plan = _create_plan(session).model_copy(update={
        "actions": [action],
        "plan_id": "load_sample_test",
        "target_state_tokens": {track.stable_id: target_token(track)},
    })
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED", compiled.reasons
    executor = build_safe_write_executor(daw, journal_path=tmp_path / "journal.jsonl", persist_dir=tmp_path)
    result = executor.run(compiled.intent)
    assert result.ok is True, result.to_dict()
    assert result.readbacks[0].matched is True
    assert daw.snapshot().tracks[0].clips[0].sample_uri == "library://kick.wav"
    assert executor._rollback_applied(result, compiled.intent) == ""
    assert not daw.snapshot().tracks[0].clips


def test_sample_load_accepts_sparse_live_simpler_readback(tmp_path):
    class SparseLiveMock(MockAbletonAdapter):
        def load_browser_item(self, track_index, item_uri, clip_index=None):
            result = super().load_browser_item(track_index, item_uri, clip_index)
            device = self.tracks[track_index]["devices"][-1]
            device["name"] = "Abletunes_RAH_Clap_58"
            device["sample_uri"] = None
            result.pop("device_index", None)
            return result

    daw = SparseLiveMock()
    daw.connect()
    daw.create_midi_track("Sparse Live Target")
    session = daw.snapshot()
    attach_tokens(session)
    track = session.tracks[0]
    action = build_sample_load_action(
        track=track,
        project_identity=session.project_identity,
        clip_index=0,
        sample_uri="query:CurrentProject#Samples:Imported:Abletunes_RAH_Clap_58.wav",
        reason="load a browser sample with sparse Live readback",
        evidence_refs=[],
        session_incarnation_id=session.session_incarnation_id,
    )
    plan = _create_plan(session).model_copy(update={
        "actions": [action],
        "plan_id": "sparse_live_sample_test",
        "target_state_tokens": {track.stable_id: target_token(track)},
    })
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED", compiled.reasons
    executor = build_safe_write_executor(
        daw, journal_path=tmp_path / "journal.jsonl", persist_dir=tmp_path
    )
    result = executor.run(compiled.intent)
    assert result.ok is True, result.to_dict()
    assert result.readbacks[0].matched is True
    assert result.readbacks[0].observed == "Abletunes_RAH_Clap_58"
    assert executor._rollback_applied(result, compiled.intent) == ""


def test_device_tweak_executes_and_rolls_back_through_safewrite(tmp_path):
    daw, session = _session()
    attach_tokens(session)
    track = session.tracks[0]
    action = build_device_tweak_action(
        track=track,
        project_identity=session.project_identity,
        device_index=0,
        parameter_name="1 Gain A",
        expected_before=0.5,
        intended_after=0.72,
        reason="set the native EQ target",
        evidence_refs=[],
        session_incarnation_id=session.session_incarnation_id,
    )
    plan = _create_plan(session).model_copy(update={
        "actions": [action],
        "plan_id": "device_tweak_test",
        "target_state_tokens": {track.stable_id: target_token(track)},
    })
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED", compiled.reasons
    executor = build_safe_write_executor(daw, journal_path=tmp_path / "journal.jsonl", persist_dir=tmp_path)
    result = executor.run(compiled.intent)
    assert result.ok is True, result.to_dict()
    assert result.readbacks[0].matched is True
    assert daw.snapshot().tracks[0].devices[0].parameters[0].value == 0.72
    assert executor._rollback_applied(result, compiled.intent) == ""
    assert daw.snapshot().tracks[0].devices[0].parameters[0].value == 0.5


def test_duplicate_clip_to_arrangement_executes_and_rolls_back_through_safewrite(tmp_path):
    daw, _ = _session()
    daw.create_midi_clip(0, 0, 4.0)
    session = daw.snapshot()
    attach_tokens(session)
    track = session.tracks[0]
    action = build_duplicate_clip_to_arrangement_action(
        track=track,
        project_identity=session.project_identity,
        clip_index=0,
        destination_time=8.0,
        length=4.0,
        reason="place the one-bar groove in the Arrangement",
        evidence_refs=[],
        session_incarnation_id=session.session_incarnation_id,
    )
    plan = _create_plan(session).model_copy(update={
        "actions": [action],
        "plan_id": "arrangement_duplicate_test",
        "target_state_tokens": {track.stable_id: target_token(track)},
    })
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED", compiled.reasons
    executor = build_safe_write_executor(daw, journal_path=tmp_path / "journal.jsonl", persist_dir=tmp_path)
    result = executor.run(compiled.intent)
    assert result.ok is True, result.to_dict()
    assert result.readbacks[0].matched is True
    assert len(daw.get_arrangement_clips()["clips"]) == 1
    assert executor._rollback_applied(result, compiled.intent) == ""
    assert daw.get_arrangement_clips()["clips"] == []


def test_four_beat_loop_still_tiles_sixteen_beats(tmp_path):
    daw, _ = _session()
    daw.create_midi_clip(0, 0, 4.0)
    session = daw.snapshot()
    action = build_duplicate_clip_to_arrangement_action(
        track=session.tracks[0], project_identity=session.project_identity,
        clip_index=0, destination_time=0.0, length=16.0,
        reason="repeat one-bar loop", evidence_refs=[],
        session_incarnation_id=session.session_incarnation_id,
    )
    plan = _create_plan(session).model_copy(update={
        "actions": [action],
        "target_state_tokens": {session.tracks[0].stable_id: target_token(session.tracks[0])},
    })
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED"
    result = build_safe_write_executor(daw, journal_path=tmp_path / "loop.jsonl", persist_dir=tmp_path).run(compiled.intent)
    assert result.ok, result.to_dict()
    assert len(daw.get_arrangement_clips()["clips"]) == 4


def _single_audio_plan(daw, *, source_length=4.0):
    daw.create_audio_track("Generated Audio")
    daw.load_browser_item(0, "Samples/Imported/generated.wav", clip_index=0)
    daw.tracks[0]["clips"][0]["length"] = source_length
    session = daw.snapshot()
    action = build_place_audio_clip_once_action(
        track=session.tracks[0], project_identity=session.project_identity,
        clip_index=0, destination_time=0.0,
        reason="place complete generated audio once", evidence_refs=[],
        session_incarnation_id=session.session_incarnation_id,
    )
    return session, _create_plan(session).model_copy(update={
        "actions": [action],
        "target_state_tokens": {session.tracks[0].stable_id: target_token(session.tracks[0])},
    })


def test_long_audio_single_placement_readback_and_owned_rollback(tmp_path):
    daw = MockAbletonAdapter()
    daw.connect()
    session, plan = _single_audio_plan(daw, source_length=64.0)
    # A long audio source and an unusual loop marker must not become bar tiling.
    plan = MusicPlan.model_validate(plan.model_dump(mode="json"))
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED"
    assert compiled.intent.executions[0].arguments["length"] is None
    assert compiled.intent.executions[0].arguments["placement_mode"] == "SINGLE_AUDIO"
    executor = build_safe_write_executor(daw, journal_path=tmp_path / "single.jsonl", persist_dir=tmp_path)
    result = executor.run(compiled.intent)
    assert result.ok, result.to_dict()
    assert result.readbacks[0].matched is True
    assert len(result.readbacks[0].expected) == 1
    clips = daw.get_arrangement_clips()["clips"]
    assert len(clips) == 1
    assert clips[0]["start_time"] == 0.0
    assert executor._rollback_applied(result, compiled.intent) == ""
    assert daw.get_arrangement_clips()["clips"] == []


def test_single_audio_rejects_multi_clip_bridge_readback(tmp_path):
    class TilingBridge(MockAbletonAdapter):
        def duplicate_clip_to_arrangement(self, track_index, clip_index, destination_time, length=None):
            return super().duplicate_clip_to_arrangement(track_index, clip_index, destination_time, 16.0)

    daw = TilingBridge()
    daw.connect()
    session, plan = _single_audio_plan(daw)
    compiled = ProductionCompiler().compile(plan, session=session)
    result = build_safe_write_executor(daw, journal_path=tmp_path / "bad.jsonl", persist_dir=tmp_path).run(compiled.intent)
    assert not result.ok
    assert daw.get_arrangement_clips()["clips"] == []


def _create_plan(session):
    attach_tokens(session)
    action = build_create_track_action(
        project_identity=session.project_identity,
        track_name="Created By SafeWrite",
        track_kind="midi",
        reason="create the first producer track",
        evidence_refs=[],
    )
    return MusicPlan(
        plan_id="create_track_test",
        status=PlanStatus.READY_FOR_EXECUTION,
        intent_class=PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT,
        diagnosis=DiagnosisBinding(
            diagnosis_id="test",
            diagnosis_status="SUPPORTED",
            diagnosis_accepted=True,
        ),
        project_state_token=session.project_token,
        audible_state_token=session.audible_token,
        created_at=now_iso(),
        actions=[action],
    )


def test_create_track_executes_and_rolls_back_through_safewrite(tmp_path):
    daw, session = _session()
    plan = _create_plan(session)
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED", compiled.reasons
    assert compiled.intent is not None
    executor = build_safe_write_executor(
        daw, journal_path=tmp_path / "journal.jsonl", persist_dir=tmp_path
    )
    result = executor.run(compiled.intent)
    assert result.ok is True, result.to_dict()
    assert result.readbacks[0].matched is True
    created_id = compiled.intent.targets[0].stable_id
    assert created_id
    assert any(track.stable_id == created_id for track in daw.snapshot().tracks)

    rollback_error = executor._rollback_applied(result, compiled.intent)
    assert rollback_error == ""
    assert not any(track.stable_id == created_id for track in daw.snapshot().tracks)


def _multi_role_plan(session):
    actions = []
    for role, pitch in (("Bass", 36), ("Drums", 42), ("Harmony", 60)):
        name = f"Copilot {role}"
        create = build_create_track_action(
            project_identity=session.project_identity,
            track_name=name,
            track_kind="midi",
            reason=f"create {role.lower()} role",
            evidence_refs=[f"role:{role.upper()}"],
        )
        ref = {"object_type": "track", "project_identity": session.project_identity, "role": "midi", "name": name}
        device = PlanAction(
            action_id=f"device_{role.lower()}", action_type=ProductionActionKind.LOAD_DEVICE,
            target=ActionTarget(ref=ref),
            params=DeviceLoadActionParams(device_name="Operator", device_uri="Operator"),
            reason=f"load {role.lower()} instrument", expected_effect=ExpectedEffect(
                affected_target=f"{name}.devices", direction="add", description="load device",
            ), rollback=RollbackSpec(parameter="device", unit="device", restore_value=-1, prepared=True),
        )
        pattern = PlanAction(
            action_id=f"pattern_{role.lower()}", action_type=ProductionActionKind.CREATE_PATTERN,
            target=ActionTarget(ref=ref),
            params=PatternActionParams(clip_index=0, length_beats=4, notes=[MidiNote(pitch=pitch, start_time=0, duration=1)]),
            reason=f"write {role.lower()} pattern", expected_effect=ExpectedEffect(
                affected_target=f"{name}.clip", direction="add", description="write MIDI",
            ), rollback=RollbackSpec(parameter="pattern", unit="clip", restore_value=-1, prepared=True),
        )
        arrangement = PlanAction(
            action_id=f"arrangement_{role.lower()}", action_type=ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT,
            target=ActionTarget(ref=ref),
            params=ArrangementDuplicateActionParams(clip_index=0, destination_time=0, length=None),
            reason=f"arrange {role.lower()} pattern", expected_effect=ExpectedEffect(
                affected_target=f"{name}.arrangement", direction="add", description="place MIDI",
            ), rollback=RollbackSpec(parameter="arrangement", unit="clip", restore_value=0, prepared=True),
        )
        actions.extend([create, device, pattern, arrangement])
    return _create_plan(session).model_copy(update={"plan_id": "multi_role_plan", "actions": actions})


def test_multi_role_plan_is_one_compiled_safewrite_transaction(tmp_path):
    daw, session = _session()
    plan = _multi_role_plan(session)
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED", compiled.reasons
    assert compiled.intent is not None
    assert len(compiled.intent.executions) == 12
    executor = build_safe_write_executor(daw, journal_path=tmp_path / "multi.jsonl", persist_dir=tmp_path)
    result = executor.run(compiled.intent)
    assert result.ok is True, result.to_dict()
    assert result.musical_writes == 12
    assert len(daw.snapshot().tracks) == 4
    assert len(daw.get_arrangement_clips()["clips"]) == 3
    assert executor._rollback_applied(result, compiled.intent) == ""
    assert len(daw.snapshot().tracks) == 1
    assert daw.get_arrangement_clips()["clips"] == []


def test_multi_role_failure_rolls_back_all_prior_roles(tmp_path):
    class FailingPatternMock(MockAbletonAdapter):
        def create_midi_clip(self, track_index, clip_index, length_beats):
            if self.tracks[track_index]["name"] == "Copilot Drums":
                raise RuntimeError("drum role rejected")
            return super().create_midi_clip(track_index, clip_index, length_beats)

    daw = FailingPatternMock()
    daw.connect()
    daw.create_midi_track("Compiler Test")
    session = daw.snapshot()
    plan = _multi_role_plan(session)
    compiled = ProductionCompiler().compile(plan, session=session)
    executor = build_safe_write_executor(daw, journal_path=tmp_path / "multi-fail.jsonl", persist_dir=tmp_path)
    result = executor.run(compiled.intent)
    assert result.ok is False
    assert result.failure.value in {"PARTIAL_FAILURE", "EXECUTION_FAILED"}
    assert [track.name for track in daw.snapshot().tracks] == ["Compiler Test"]
    assert daw.get_arrangement_clips()["clips"] == []


def test_load_device_executes_and_rolls_back_through_safewrite(tmp_path):
    daw, session = _session()
    attach_tokens(session)
    track = session.tracks[0]
    action = build_device_load_action(
        track=track,
        project_identity=session.project_identity,
        device_name="Glue Compressor",
        device_uri="native://Glue Compressor",
        reason="add native glue",
        evidence_refs=[],
    )
    plan = _create_plan(session).model_copy(update={
        "actions": [action],
        "plan_id": "load_device_test",
        "target_state_tokens": {track.stable_id: target_token(track)},
    })
    compiled = ProductionCompiler().compile(plan, session=session)
    assert compiled.status == "COMPILED", compiled.reasons
    executor = build_safe_write_executor(
        daw, journal_path=tmp_path / "journal.jsonl", persist_dir=tmp_path
    )
    result = executor.run(compiled.intent)
    assert result.ok is True, result.to_dict()
    assert result.readbacks[0].matched is True
