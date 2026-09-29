from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.integration.lucas_core_v1 import _single_action_plan
from copilot.musicplan import build_sample_load_action
from copilot.runtime.production_compiler import ProductionCompiler


def test_midi_sample_targets_instrument_after_pattern_clip_exists():
    daw = MockAbletonAdapter()
    daw._ensure_eq_device = False
    daw.connect()
    daw.session_path = "new-working-copy.als"
    daw.create_midi_track("Shaker")
    daw.create_midi_clip(0, 0, 4)
    session = attach_tokens(daw.snapshot())
    track = session.track_by_name("Shaker")
    action = build_sample_load_action(
        track=track, project_identity=session.project_identity, clip_index=0,
        sample_uri="query:CurrentProject#Samples:shaker.wav",
        reason="load verified shaker into Simpler",
        evidence_refs=["asset-1"], session_incarnation_id=session.session_incarnation_id,
    )
    compiled = ProductionCompiler().compile(
        _single_action_plan(action, session=session, plan_id="midi_sample"),
        session=session,
    )
    assert compiled.status == "COMPILED"
    assert compiled.intent.executions[0].operation == "load_browser_item"
