import json

import pytest

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.musicplan.astra_plan import build_plan_from_prompt
from copilot.producer.goal import ProducerGoal
from copilot.sample_library.schemas import LibraryIndex, SampleAsset, SampleType


class PlanningProvider:
    def __init__(self, payload):
        self.payload = payload

    def reason(self, prompt, *, timeout_s):
        return json.dumps(self.payload)


@pytest.fixture
def producer_inputs(monkeypatch, tmp_path):
    from copilot.musicplan import astra_plan
    from copilot.sample_library import context_comparison

    monkeypatch.setattr(
        astra_plan, "build_candidate_context",
        lambda *args, **kwargs: {"Kick": [{"sha256": "sample", "filename": "kick.wav", "bpm": None}]},
    )
    monkeypatch.setattr(
        context_comparison, "compare_shortlist",
        lambda _index, candidates, **_kwargs: candidates,
    )
    daw = MockAbletonAdapter()
    daw.connect()
    session = attach_tokens(daw.snapshot())
    goal = ProducerGoal.from_prompt("BPM: 127\nDuración: 8 compases")
    return dict(index=LibraryIndex(), session=session, intent=goal.prompt, goal=goal,
                preview_root=tmp_path, authorized_library_root=tmp_path)


def test_strict_planner_rejects_missing_plan_without_recipe(producer_inputs):
    with pytest.raises(ValueError, match="TRACK_SPEC_REQUIRED"):
        build_plan_from_prompt(
            **producer_inputs,
            provider=PlanningProvider({"selections": {"Kick": 1}}),
        )


def test_strict_planner_rejects_missing_or_invalid_sample_choice(producer_inputs):
    payload = {
        "selections": {},
        "mix_decisions": {
            "eq_eight": {"decision": "NONE", "reason": "No measured problem yet"},
            "limiter": {"decision": "NONE", "reason": "Preserve transients"},
        },
        "track_spec": {
            "bpm": 127, "primary_hook": "kick rhythm", "hook_role": "Kick", "sections": [
                {"name": "Groove", "bars": 8, "energy": .6, "active_roles": ["Kick"]}
            ],
        },
    }
    with pytest.raises(ValueError, match="SAMPLE_SELECTION_MISSING"):
        build_plan_from_prompt(**producer_inputs, provider=PlanningProvider(payload))
    payload["selections"] = {"Kick": 4}
    payload["selection_reasons"] = {"Kick": "Fits the low end"}
    with pytest.raises(ValueError, match="SELECTION_UNRESOLVED"):
        build_plan_from_prompt(**producer_inputs, provider=PlanningProvider(payload))


def test_strict_planner_rejects_wrong_duration_before_recipe(producer_inputs):
    payload = {
        "selections": {"Kick": 1},
        "mix_decisions": {
            "eq_eight": {"decision": "NONE", "reason": "No measured problem yet"},
            "limiter": {"decision": "NONE", "reason": "Preserve transients"},
        },
        "selection_reasons": {"Kick": "Fits the low end"},
        "track_spec": {
            "bpm": 127, "primary_hook": "kick rhythm", "hook_role": "Kick", "sections": [
                {"name": "Groove", "bars": 4, "energy": .6, "active_roles": ["Kick"]}
            ],
        },
    }
    with pytest.raises(ValueError, match="DURATION_MISMATCH"):
        build_plan_from_prompt(**producer_inputs, provider=PlanningProvider(payload))


def test_strict_planner_requires_typed_physical_mix_target(producer_inputs):
    payload = {
        "selections": {"Kick": 1},
        "selection_reasons": {"Kick": "Anchors the arrangement"},
        "mix_decisions": {
            "eq_eight": {"decision": "NONE", "reason": "No measured need"},
            "limiter": {"decision": "APPLY", "reason": "Peaks need restraint"},
        },
        "track_spec": {
            "bpm": 127, "primary_hook": "kick pulse", "hook_role": "Kick",
            "sections": [{"name": "A", "bars": 8, "energy": .5, "active_roles": ["Kick"]}],
        },
    }
    with pytest.raises(ValueError, match="MIX_HYPOTHESIS_INVALID"):
        build_plan_from_prompt(**producer_inputs, provider=PlanningProvider(payload))
    payload["mix_decisions"]["limiter"].update(
        objective="reduce_peak", hypothesis="Limiter should reduce measured crest",
    )
    with pytest.raises(ValueError, match="MIX_ACTION_INVALID"):
        build_plan_from_prompt(**producer_inputs, provider=PlanningProvider(payload))
    payload["mix_decisions"]["limiter"]["action"] = {
        "track_name": "Master", "control": "ceiling", "value": -1.0, "unit": "db",
    }
    # LibraryIndex fixture deliberately lacks the referenced asset; once the
    # typed decision is accepted the next fail-closed boundary is the index.
    with pytest.raises(ValueError, match="SAMPLE_UNAVAILABLE"):
        build_plan_from_prompt(**producer_inputs, provider=PlanningProvider(payload))


def test_strict_planner_rejects_unexplained_ab_candidate(monkeypatch, producer_inputs):
    from copilot.musicplan import astra_plan

    monkeypatch.setattr(astra_plan, "build_candidate_context", lambda *_args, **_kwargs: {
        "Kick": [
            {"sha256": "one", "filename": "one.wav"},
            {"sha256": "two", "filename": "two.wav"},
        ],
    })
    payload = {
        "selections": {"Kick": 1},
        "selection_reasons": {"Kick": "More low transient by index measurement"},
        "mix_decisions": {
            "eq_eight": {"decision": "NONE", "reason": "No factual issue yet"},
            "limiter": {"decision": "NONE", "reason": "No factual issue yet"},
        },
        "track_spec": {
            "bpm": 127, "primary_hook": "kick pulse", "hook_role": "Kick",
            "sections": [{"name": "A", "bars": 8, "energy": .5, "active_roles": ["Kick"]}],
        },
    }
    with pytest.raises(ValueError, match="AB_REJECTIONS_MISSING"):
        build_plan_from_prompt(**producer_inputs, provider=PlanningProvider(payload))
    payload["rejected_candidates"] = {"Kick": {"2": "Measured transient is weaker"}}
    with pytest.raises(ValueError, match="SAMPLE_UNAVAILABLE"):
        build_plan_from_prompt(**producer_inputs, provider=PlanningProvider(payload))


def test_strict_planner_preserves_grounded_criteria_and_comparisons(producer_inputs):
    producer_inputs["index"] = LibraryIndex(assets={"sample": SampleAsset(
        id="sample", path="kick.wav", filename="kick.wav",
        library_root=".", relative_path="kick.wav", extension=".wav",
        size_bytes=1024, sha256="sample", sample_type=SampleType.ONE_SHOT,
    )})
    payload = {
        "selections": {"Kick": 1},
        "selection_reasons": {"Kick": "Transient dominates the low-end reference"},
        "rejected_candidates": {"Kick": {}},
        "mix_decisions": {
            "eq_eight": {"decision": "NONE", "reason": "No measured deficiency"},
            "limiter": {"decision": "NONE", "reason": "Avoid unnecessary gain"},
        },
        "producer_criteria": {
            "primary_hook": "kick rhythm", "hook_role": "Kick",
            "uncertainty": "No semantic audition of the complete groove",
            "sections": [{
                "section_name": "Groove", "perceptual_goal": "Keep a clear pulse",
                "lead_role": "Kick", "low_end_owner": "Kick",
                "space_roles": ["Bass"], "hook_usage": "foreground",
                "energy_rationale": "Reduced density leaves pulse exposed",
                "variation_hypothesis": "Accents may help transitions",
                "claim_kind": "ARTISTIC_PREFERENCE", "evidence_refs": ["sample"],
            }],
        },
        "track_spec": {
            "bpm": 127, "primary_hook": "kick rhythm", "hook_role": "Kick",
            "sections": [{"name": "Groove", "bars": 8, "energy": .6,
                          "active_roles": ["Kick"]}],
        },
    }
    _, metadata = build_plan_from_prompt(
        **producer_inputs, provider=PlanningProvider(payload),
    )
    assert metadata["producer_criteria"]["sections"][0]["evidence_refs"] == ["sample"]
    assert metadata["sample_comparisons"]["Kick"][0]["sha256"] == "sample"
    payload["producer_criteria"]["sections"][0]["evidence_refs"] = ["invented"]
    with pytest.raises(ValueError, match="CRITERIA_SECTION_UNGROUNDED"):
        build_plan_from_prompt(**producer_inputs, provider=PlanningProvider(payload))


def test_planner_schema_places_producer_criteria_at_response_root():
    from copilot.integration.autonomous_producer_alpha_v1 import LucasPlanningProviderAdapter

    schema = LucasPlanningProviderAdapter._schema()
    properties = schema["properties"]
    assert "producer_criteria" in properties
    assert "producer_criteria" in schema["required"]
    assert "producer_criteria" not in properties["mix_decisions"]["properties"]["eq_eight"]["properties"]["action"]["properties"]
