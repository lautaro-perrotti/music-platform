from copilot.daw.mock import MockAbletonAdapter
from copilot.musicplan import (
    build_create_track_action,
    build_device_load_action,
    build_device_tweak_action,
    build_duplicate_clip_to_arrangement_action,
    build_sample_load_action,
    create_controlled_volume_plan,
)
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.runtime.safe_write import build_safe_write_executor
from copilot.schemas.musicplan import (
    DiagnosisBinding,
    MusicPlan,
    PlanIntentClass,
    PlanStatus,
    ProductionActionKind,
)
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
    assert result.status == "UNCERTIFIED_ACTION"
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
