from __future__ import annotations

import json

import pytest

from copilot.producer.state import ProducerPhase, ProducerStateStore
from copilot.producer.track_spec import parse_track_spec, track_spec_from_planner_payload


def _payload() -> dict:
    return {
        "title": "Night Shift",
        "intent": "underground groovy tech house with a restrained second drop",
        "bpm": 127,
        "key": "F minor",
        "style": "tech house",
        "primary_hook": "staccato vocal chop",
        "sections": [
            {"name": "OPEN", "bars": 8, "energy": 0.25, "active_roles": ["Kick", "Closed Hat"]},
            {"name": "DROP", "bars": 16, "energy": 0.85, "active_roles": ["Kick", "Bass", "Closed Hat"]},
            {"name": "EXIT", "bars": 8, "energy": 0.2, "active_roles": ["Kick", "Closed Hat"]},
        ],
    }


def test_track_spec_is_strict_and_converts_to_existing_sections():
    spec = parse_track_spec(_payload())
    assert spec.duration_bars == 32
    arrangement = spec.to_arrangement(known_roles={"Kick", "Bass", "Closed Hat"})
    assert [(item.name, item.bars) for item in arrangement] == [
        ("OPEN", 8),
        ("DROP", 16),
        ("EXIT", 8),
    ]


def test_track_spec_accepts_json_fences_and_planner_wrapper():
    raw = "```json\n" + json.dumps(_payload()) + "\n```"
    spec = track_spec_from_planner_payload(json.loads(json.dumps({"track_spec": _payload()})))
    assert spec is not None
    assert parse_track_spec(raw.split("\n", 1)[1].rsplit("\n", 1)[0]).title == "Night Shift"


def test_track_spec_rejects_duration_mismatch_and_unknown_roles():
    payload = _payload()
    payload["duration_bars"] = 64
    with pytest.raises(ValueError, match="DURATION_MISMATCH"):
        parse_track_spec(payload)

    spec = parse_track_spec(_payload())
    with pytest.raises(ValueError, match="UNKNOWN_ROLES"):
        spec.to_arrangement(known_roles={"Kick"})


def test_producer_state_is_atomic_and_identity_scoped(tmp_path):
    store = ProducerStateStore(tmp_path)
    state = store.create(session_id="session-1", project_identity="project-a")
    next_state = state.record(
        "PLAN_ACCEPTED",
        phase=ProducerPhase.EXECUTING,
        section_name="DROP",
        payload={"active_roles": ["Kick", "Bass"]},
    )
    path = store.save(next_state)
    restored = store.load("session-1")
    assert path.is_file()
    assert restored is not None
    assert restored.phase == ProducerPhase.EXECUTING
    assert restored.section_name == "DROP"
    assert restored.events[-1].kind == "PLAN_ACCEPTED"

    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        store.save(next_state.model_copy(update={"project_identity": "project-b"}))


def test_lucas_core_intent_gate_accepts_track_spec_roles():
    from copilot.integration.lucas_core_v1 import constrain_plan_to_lucas_intent
    from copilot.musicplan.tech_house import build_tech_house_plan
    from copilot.daw.mock import MockAbletonAdapter
    from copilot.daw.state_tokens import attach_tokens
    from copilot.sample_library.schemas import LibraryIndex

    # The gate is tested with a minimal empty plan; it must use TrackSpec roles
    # as the allow-list even when legacy `arrangement` metadata is absent.
    daw = MockAbletonAdapter()
    daw.connect()
    session = daw.snapshot()
    attach_tokens(session)
    plan = build_tech_house_plan(index=LibraryIndex(assets={}), session=session)
    constrained, metadata = constrain_plan_to_lucas_intent(
        plan,
        {"track_spec": {"sections": [{"active_roles": ["Kick"]}]}},
    )
    target_names = {
        str((action.target.ref or {}).get("name") or "")
        for action in constrained.actions
        if (action.target.ref or {}).get("name")
    }
    assert target_names <= {"Kick"}
    assert metadata["core_intent_gate"]["allowed_tracks"] == ["Kick"]
