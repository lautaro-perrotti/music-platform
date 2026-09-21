from __future__ import annotations

import pytest

from copilot.audio.capture_scalability_v2 import validate_capture_pool_transition
from copilot.audio.producer_analyze_v1 import apply_reasoning_result
from copilot.reasoning.errors import ReasoningFailure
from copilot.reasoning.fixtures import pack_clear_no_action
from copilot.reasoning.pipeline import reason
from copilot.reasoning.provider import FailingProvider, ReasoningProvider
from copilot.runtime.capabilities import cap_capture_sources
from copilot.runtime.context import OperationContext
from copilot.schemas.session import SessionState, TrackState, TransportState
from copilot.daw.state_tokens import attach_tokens


def _session(*names: str) -> SessionState:
    session = SessionState(
        project_path="C:/p/working.als",
        project_name="working",
        tracks=[
            TrackState(
                stable_id=f"track-{index}",
                index=index,
                name=name,
                role="audio",
            )
            for index, name in enumerate(names)
        ],
        transport=TransportState(tempo=126.0, playing=False),
    )
    return attach_tokens(session)


def test_capture_pool_expansion_is_the_only_allowed_post_capture_delta() -> None:
    before = _session("Kick", "Copilot Capture", "Copilot Capture Bass")
    after = _session(
        "Kick",
        "Copilot Capture",
        "Copilot Capture Bass",
        "Copilot Capture 3",
        "Copilot Capture 4",
    )

    transition = validate_capture_pool_transition(before, after)

    assert transition["ok"] is True
    assert transition["added_hosts"] == ["Copilot Capture 3", "Copilot Capture 4"]
    assert transition["unexpected_added"] == []
    assert transition["before_track_count"] == 3
    assert transition["after_track_count"] == 5


def test_unrelated_project_delta_remains_fail_closed() -> None:
    before = _session("Kick", "Copilot Capture", "Copilot Capture Bass")
    after = _session("Kick (changed)", "Copilot Capture", "Copilot Capture Bass")

    transition = validate_capture_pool_transition(before, after)

    assert transition["ok"] is False
    assert transition["prefix_matches"] is False


class _InvalidProvider(ReasoningProvider):
    identity = "invalid-fixture"
    version = "invalid-1"

    def reason(self, prompt: str, *, timeout_s: float = 30.0) -> str:
        del prompt, timeout_s
        return "not-json"


@pytest.mark.parametrize(
    "provider,expected_failure",
    [
        (FailingProvider(ReasoningFailure.MODEL_TIMEOUT, "timeout"), ReasoningFailure.MODEL_TIMEOUT),
        (FailingProvider(ReasoningFailure.MODEL_RATE_LIMITED, "rate limited"), ReasoningFailure.MODEL_RATE_LIMITED),
        (_InvalidProvider(), ReasoningFailure.MODEL_OUTPUT_INVALID),
    ],
)
def test_provider_failure_after_evidence_abstains_without_writes(provider, expected_failure) -> None:
    result = reason(pack_clear_no_action(), provider, timeout_s=0.01)
    applied = apply_reasoning_result(result)

    assert result.accepted is False
    assert result.failure is expected_failure
    assert result.musical_writes == 0
    assert applied["status"] == "DIAGNOSIS_UNSTABLE"
    assert applied["gate"]["decision"] == "ABSTAIN"
    assert applied["gate"]["proceed_to_write"] is False


def test_timeout_terminal_state_uses_rebound_post_capture_tokens() -> None:
    before = _session("Kick", "Copilot Capture", "Copilot Capture Bass")
    after = _session(
        "Kick",
        "Copilot Capture",
        "Copilot Capture Bass",
        "Copilot Capture 3",
    )
    ctx = OperationContext.create()
    ctx.bind_tokens(before)

    transition = validate_capture_pool_transition(before, after)
    assert transition["ok"] is True
    ctx.bind_tokens(after)

    assert ctx.tokens_match(after) is True
    assert ctx.project_identity == after.project_identity
    assert ctx.project_state_token == after.project_token
    assert ctx.audible_state_token == after.audible_token


def test_capture_capability_rebinds_only_after_validated_pool_expansion(monkeypatch, tmp_path) -> None:
    before = _session("Kick", "Copilot Capture", "Copilot Capture Bass")
    after = _session(
        "Kick",
        "Copilot Capture",
        "Copilot Capture Bass",
        "Copilot Capture 3",
    )

    class FakeDaw:
        def snapshot(self, *, include_notes: bool = False):
            del include_notes
            return after

    monkeypatch.setattr(
        "copilot.audio.producer_analyze_v1.capture_bounded_sources",
        lambda *args, **kwargs: [],
    )
    ctx = OperationContext.create(evidence_root=tmp_path)
    ctx.bind_tokens(before)
    blackboard = {
        "daw": FakeDaw(),
        "session": before,
        "ready": {},
        "isolation": {"bounded_targets": []},
        "region": {"id": "R", "start_qn": 0.0, "end_qn": 8.0},
        "evidence": tmp_path,
    }

    result = cap_capture_sources(ctx, blackboard, node=None)

    assert result["state_transition"]["ok"] is True
    assert blackboard["session"] is after
    assert ctx.project_state_token == after.project_token
    assert ctx.audible_state_token == after.audible_token
