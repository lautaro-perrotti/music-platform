import json

import pytest

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.producer.revision import volume_revision_action


def test_revision_maps_small_model_proposal_to_typed_musicplan_action():
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_audio_track("Kick")
    session = attach_tokens(daw.snapshot())
    volume = session.track_by_name("Kick").mixer.volume
    action = volume_revision_action(
        json.dumps({"track_name": "Kick", "target_volume": volume - .03,
                    "reason": "measured peak risks clipping"}),
        session=session, evidence_refs=["capture-1"],
    )
    assert action.action_type.value == "SET_TRACK_VOLUME"
    assert action.params.expected_before == volume
    assert action.rollback.prepared


@pytest.mark.parametrize("proposal", [
    {"track_name": "Kick", "target_volume": .01, "reason": "lower"},
    {"track_name": "absent", "target_volume": .8, "reason": "lower"},
    {"track_name": "Kick", "target_volume": .8, "reason": "lower", "lom": "eval"},
])
def test_revision_rejects_arbitrary_or_unbounded_model_proposals(proposal):
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_audio_track("Kick")
    with pytest.raises(ValueError):
        volume_revision_action(
            json.dumps(proposal), session=attach_tokens(daw.snapshot()),
            evidence_refs=["capture-1"],
        )
