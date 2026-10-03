"""Original prompt-groove candidate generation never consumes reference data."""

import pytest

from copilot.producer.prompt_groove_v1 import (
    generate_prompt_groove_candidates, select_structural_candidate, validate_candidate,
)
from copilot.producer.track_spec import SectionSpec, TrackSpec
from copilot.musicplan.prompt_groove_v1 import build_prompt_groove_musicplan
from copilot.runtime.production_compiler import ProductionCompiler
from copilot.schemas.session import SessionState, TransportState
from copilot.reasoning.provider import OpenAICompatibleProvider


def _spec(key="D minor"):
    return TrackSpec(
        title="Original dark groove", intent="dark hypnotic tech house",
        bpm=125, key=key, meter_numerator=4, meter_denominator=4,
        vocals="none", style="tech house", duration_bars=16,
        sections=[
            SectionSpec(name="Opening", bars=4, energy=.3, active_roles=["KICK", "HAT", "BASS"]),
            SectionSpec(name="Body", bars=4, energy=.55, active_roles=["KICK", "HAT", "BASS", "HARMONY"]),
            SectionSpec(name="Hook", bars=4, energy=.75, active_roles=["KICK", "HAT", "BASS", "HARMONY", "HOOK"]),
            SectionSpec(name="Return", bars=4, energy=.6, active_roles=["KICK", "HAT", "BASS", "HOOK"]),
        ],
    )


def test_three_original_candidates_are_distinct_bounded_and_structurally_selected():
    spec = _spec()
    candidates = generate_prompt_groove_candidates(spec)
    assert [c.candidate_id for c in candidates] == ["Candidate A", "Candidate B", "Candidate C"]
    assert len({tuple((n.pitch, n.start_time) for n in c.notes_by_role["BASS"]) for c in candidates}) == 3
    assert all(c.musical_winner is None and c.structural_checks for c in candidates)
    assert all(not [n for n in c.notes_by_role["HOOK"] if n.start_time < 32] for c in candidates)
    chosen, reason = select_structural_candidate(candidates)
    assert chosen in candidates
    assert "not a sound-quality judgment" in reason


def test_missing_producer_tonal_decision_fails_closed():
    with pytest.raises(ValueError, match="PRODUCER_KEY_REQUIRED"):
        generate_prompt_groove_candidates(_spec(key=None))


def test_out_of_form_note_is_rejected():
    spec = _spec()
    candidate = generate_prompt_groove_candidates(spec)[0]
    broken = candidate.model_copy(deep=True)
    broken.notes_by_role["BASS"][0].start_time = 64
    with pytest.raises(ValueError, match="CANDIDATE_NOTE_OUTSIDE_FORM"):
        validate_candidate(broken, spec)


def test_unknown_role_or_meter_is_rejected():
    spec = _spec()
    spec.sections[0].active_roles.append("VOCAL")
    with pytest.raises(ValueError, match="V1_UNKNOWN_ROLE"):
        generate_prompt_groove_candidates(spec)
    spec = _spec()
    spec.meter_numerator = 3
    with pytest.raises(ValueError, match="V1_REQUIRES_16_BARS_4_4"):
        generate_prompt_groove_candidates(spec)


def test_combined_plan_uses_only_certified_compound_action_vocabulary():
    spec = _spec()
    candidate = generate_prompt_groove_candidates(spec)[0]
    session = SessionState(
        connected=True, project_path="C:/controlled/working.als", project_identity="working-copy-id",
        project_token="project-token", audible_token="audible-token",
        session_incarnation_id="session-id", state_hash="state-hash",
        transport=TransportState(tempo=125, signature_numerator=4, signature_denominator=4),
    )
    plan = build_prompt_groove_musicplan(
        spec=spec, candidate=candidate, session=session,
        drum_sample_uris={"KICK": "Samples/kick.wav", "HAT": "Samples/hat.wav"},
        stock_device_uris={"BASS": "browser://operator", "HARMONY": "browser://operator", "HOOK": "browser://operator"},
    )
    assert len(plan.actions) == 18
    assert all(action.action_type.value != "SAMPLE_LOAD" for action in plan.actions)
    assert all("206 BPM" not in ref for ref in plan.evidence_refs)
    result = ProductionCompiler().compile(plan, session=session)
    assert result.status == "COMPILED", result.reasons


def test_producer_json_uses_its_own_response_format_not_diagnosis_schema(monkeypatch):
    provider = OpenAICompatibleProvider(base_url="https://example.invalid/v1", model="gpt-6-astra", api_key="test")
    observed = {}

    def post(path, payload, *, timeout_s):
        observed.update(path=path, payload=payload, timeout_s=timeout_s)
        return {"output_text": '{"track_spec": {}}'}

    monkeypatch.setattr(provider, "_post", post)
    assert provider.reason_json_object("producer prompt", timeout_s=12) == '{"track_spec": {}}'
    assert observed["payload"]["text"]["format"] == {"type": "json_object"}
    assert observed["timeout_s"] == 12
