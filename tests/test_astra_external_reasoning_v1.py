from __future__ import annotations

from pathlib import Path

import pytest

from copilot.audio.astra_external_reasoning_v1 import (
    EXPECTED_PACK_ID,
    classify_rejection,
    evidence_sufficiency_audit,
    load_persisted_pack,
    pack_payload_hash,
    reconstruct_input,
    replay_persisted_pack,
)
from copilot.audio.producer_analyze_v1 import apply_reasoning_result, gate_read_only
from copilot.reasoning.errors import ReasoningFailure
from copilot.reasoning.fixtures import (
    output_clear_no_action,
    output_hallucination_trap,
    pack_clear_no_action,
    pack_hallucination_trap,
)
from copilot.reasoning.grounding import validate_reasoning
from copilot.reasoning.pipeline import reason
from copilot.reasoning.provider import FailingProvider, ScriptedProvider
from copilot.reasoning.schema import GroundedHypothesis, ReasoningOutput
from copilot.schemas.diagnosis import CandidateActionType, Confidence, DiagnosisStatus, FindingType
from copilot.schemas.evidence import EvidenceRequest, EvidenceRequestKind


def _ie_output(evidence_id: str) -> ReasoningOutput:
    return ReasoningOutput(
        category=FindingType.INSUFFICIENT_EVIDENCE,
        summary="Facts are present; no mix problem is established.",
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        confidence=Confidence.LOW,
        hypotheses=[
            GroundedHypothesis(
                claim="Current measurements do not establish a mix diagnosis.",
                evidence_refs=[evidence_id],
                reasoning_summary="Signal presence is not a quality judgment.",
                confidence=Confidence.LOW,
                alternatives_considered=["LEVEL_IMBALANCE", "NO_ACTION_REQUIRED"],
                contradicting_evidence_refs=[],
                missing_evidence=["READ_DEVICE_PARAMETERS"],
                limitations=["ALIGNMENT_LIMITED", "MIDI_UNREAD"],
            )
        ],
        evidence_refs=[evidence_id],
        contradicting_evidence_refs=[],
        limitations=["ALIGNMENT_LIMITED", "MIDI_UNREAD"],
        candidate_actions=[],
        requested_evidence=[
            EvidenceRequest(
                request_kind=EvidenceRequestKind.READ_DEVICE_PARAMETERS,
                why_needed="MIDI may be present while post-mixer level is unexplained.",
                target="target source",
                region="current region",
                expected_information_gain="Whether gain/dynamics parameters explain the level.",
                goal="DEVICE_CAUSAL_CONTEXT",
                required_evidence_kinds=["FACT"],
                priority="HIGH",
            )
        ],
        entity_refs=[],
        question="Is a mix change warranted from current measurements?",
        scope="current region",
    )


def test_timeout_is_not_musical_insufficient_evidence() -> None:
    result = reason(
        pack_clear_no_action(),
        FailingProvider(ReasoningFailure.MODEL_TIMEOUT, "timed out"),
    )
    applied = apply_reasoning_result(result)
    assert result.accepted is False
    assert applied["status"] == DiagnosisStatus.DIAGNOSIS_UNSTABLE.value
    assert applied["gate"]["reason"] == ReasoningFailure.MODEL_TIMEOUT.value
    assert applied["gate"]["reason"] != "reason_not_accepted"
    assert applied["gate"]["accepted"] is False
    classified = classify_rejection(
        accepted=False,
        failure=ReasoningFailure.MODEL_TIMEOUT.value,
        parse_error=None,
        issues=result.audit.issues,
        output=None,
    )
    assert classified["primary"] == "L"


def test_valid_ie_is_accepted_and_not_no_action() -> None:
    pack = pack_clear_no_action()
    output = _ie_output("ev.kick.count")
    result = reason(pack, ScriptedProvider({pack.pack_id: output}))
    applied = apply_reasoning_result(result)
    assert result.accepted is True
    assert applied["status"] == DiagnosisStatus.INSUFFICIENT_EVIDENCE.value
    assert applied["gate"]["accepted"] is True
    assert applied["status"] != DiagnosisStatus.NO_ACTION_REQUIRED.value
    no_action = gate_read_only(
        diagnosis_status="NO_ACTION_REQUIRED",
        diagnosis_accepted=True,
        candidate_actions=[{"action_type": "NO_CHANGE"}],
    )
    assert no_action["milestone_status"] == "NO_ACTION_REQUIRED"
    assert applied["status"] != no_action["milestone_status"]


def test_invented_refs_numeric_temporal_still_rejected() -> None:
    report = validate_reasoning(output_hallucination_trap(), pack_hallucination_trap())
    kinds = {issue.kind for issue in report.issues}
    assert report.accepted is False
    assert ReasoningFailure.UNKNOWN_EVIDENCE_REF in kinds
    applied = apply_reasoning_result(
        reason(pack_hallucination_trap(), ScriptedProvider({"*": output_hallucination_trap()}))
    )
    assert applied["gate"]["accepted"] is False
    assert applied["status"] == DiagnosisStatus.DIAGNOSIS_UNSTABLE.value


def test_no_action_scripted_still_accepted() -> None:
    result = reason(pack_clear_no_action(), ScriptedProvider({"CLEAR_NO_ACTION": output_clear_no_action()}))
    applied = apply_reasoning_result(result)
    assert applied["gate"]["accepted"] is True
    assert applied["status"] == DiagnosisStatus.NO_ACTION_REQUIRED.value


# The canonical pack must come from a committed fixture, not from
# logs/evidence_pack_v1.json: that path is a single runtime slot which every
# producer-analyze run overwrites, so pinning a pack_id there makes this test
# fail whenever the product is run on a different project. See
# fixtures/frozen/README.md.
FROZEN_PACK_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "frozen"
    / "evidence_pack_v1_astra_external_reasoning.json"
)


def test_persisted_pack_hash_and_scripted_ie(tmp_path: Path) -> None:
    if not FROZEN_PACK_PATH.is_file():
        pytest.skip(
            "frozen canonical pack absent; re-seed per fixtures/frozen/README.md"
        )
    pack = load_persisted_pack(FROZEN_PACK_PATH)
    assert pack.pack_id == EXPECTED_PACK_ID
    reconstructed = reconstruct_input(pack)
    assert reconstructed["payload_sha256"] == pack_payload_hash(pack)
    assert reconstructed["fact_problems"] == []
    sufficiency = evidence_sufficiency_audit(pack)
    assert "ACCEPTED INSUFFICIENT_EVIDENCE" in sufficiency["overall"]
    first = pack.items[0].evidence_id
    report = replay_persisted_pack(
        evidence=tmp_path,
        pack_path=FROZEN_PACK_PATH,
        provider=ScriptedProvider({pack.pack_id: _ie_output(first)}),
    )
    assert report["accepted"] is True
    assert report["ASTRA RESULT"] == DiagnosisStatus.INSUFFICIENT_EVIDENCE.value
    assert report["MUSICAL WRITES"] == 0
    assert report["NO LIVE"] is True
    assert report["NO RECAPTURE"] is True
    assert (tmp_path / "astra_external_reasoning_v1.json").is_file()
