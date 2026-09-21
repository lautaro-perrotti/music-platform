from copilot.daw.mock import MockAbletonAdapter
from copilot.musicplan import create_controlled_volume_plan
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.schemas.musicplan import ProductionActionKind


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


def test_compiler_rejects_lucas_actions_until_certified():
    _daw, session = _session()
    track = session.tracks[0]
    plan = create_controlled_volume_plan(session=session, track=track, delta=-0.15)
    plan.actions[0].action_type = ProductionActionKind.SAMPLE_LOAD
    result = ProductionCompiler().compile(plan, session=session)
    assert result.status == "UNCERTIFIED_ACTION"
    assert result.intent is None
