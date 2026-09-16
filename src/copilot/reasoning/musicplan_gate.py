"""MusicPlan gate: fail-closed token freshness before any OPEN.

No Ableton imports. No writes. Diagnosis status alone cannot open the gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

GateDecision = Literal["OPEN", "CLOSED"]

STALE_PROJECT_STATE = "STALE_PROJECT_STATE"
STALE_AUDIBLE_STATE = "STALE_AUDIBLE_STATE"
STALE_TARGET_STATE = "STALE_TARGET_STATE"
TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
PROJECT_MISMATCH = "PROJECT_MISMATCH"
EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


@dataclass(frozen=True)
class MusicPlanGateResult:
    gate: GateDecision
    reason: str
    code: str | None = None
    detail: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "MUSICPLAN_GATE": self.gate,
            "MUSICPLAN_GATE_REASON": self.reason,
            "MUSICPLAN_GATE_CODE": self.code,
            "MUSICPLAN_GATE_DETAIL": self.detail or {},
        }


def _closed(reason: str, code: str | None = None, **detail: Any) -> MusicPlanGateResult:
    return MusicPlanGateResult(gate="CLOSED", reason=reason, code=code, detail=detail or None)


def evaluate_musicplan_gate(
    *,
    diagnosis_accepted: bool,
    diagnosis_status: str | None,
    actionable: bool = False,
    cause_status: str | None = None,
    require_cause_supported: bool = False,
    evidence_project_token: str | None,
    evidence_audible_token: str | None = None,
    evidence_target_token: str | None = None,
    live_project_token: str | None,
    live_audible_token: str | None = None,
    live_target_token: str | None = None,
    require_audible: bool = True,
    require_target: bool = True,
    target_resolve_status: str | None = None,
    project_path_match: bool | None = None,
) -> MusicPlanGateResult:
    """Fail-closed. Path identity never authorizes OPEN under token mismatch."""

    if target_resolve_status == TARGET_AMBIGUOUS:
        return _closed("target_ambiguous", TARGET_AMBIGUOUS)
    if target_resolve_status == TARGET_NOT_FOUND:
        return _closed("target_not_found", TARGET_NOT_FOUND)
    if target_resolve_status == PROJECT_MISMATCH:
        return _closed("project_mismatch", PROJECT_MISMATCH)

    if not evidence_project_token or not live_project_token:
        return _closed(
            "evidence_incomplete_project_token",
            EVIDENCE_INCOMPLETE,
            evidence_project_token=evidence_project_token,
            live_project_token=live_project_token,
        )
    if evidence_project_token != live_project_token:
        return _closed(
            "stale_project_state",
            STALE_PROJECT_STATE,
            evidence_project_token=evidence_project_token,
            live_project_token=live_project_token,
            project_path_match=project_path_match,
        )

    if require_audible:
        if not evidence_audible_token or not live_audible_token:
            return _closed(
                "evidence_incomplete_audible_token",
                EVIDENCE_INCOMPLETE,
                evidence_audible_token=evidence_audible_token,
                live_audible_token=live_audible_token,
            )
        if evidence_audible_token != live_audible_token:
            return _closed(
                "stale_audible_state",
                STALE_AUDIBLE_STATE,
                evidence_audible_token=evidence_audible_token,
                live_audible_token=live_audible_token,
                project_path_match=project_path_match,
            )

    if require_target:
        if not evidence_target_token or not live_target_token:
            return _closed(
                "evidence_incomplete_target_token",
                EVIDENCE_INCOMPLETE,
                evidence_target_token=evidence_target_token,
                live_target_token=live_target_token,
            )
        if evidence_target_token != live_target_token:
            return _closed(
                "stale_target_state",
                STALE_TARGET_STATE,
                evidence_target_token=evidence_target_token,
                live_target_token=live_target_token,
                project_path_match=project_path_match,
            )

    # Tokens fresh — diagnosis may continue evaluating for OPEN.
    if not diagnosis_accepted:
        return _closed("diagnosis_not_accepted")
    if require_cause_supported and cause_status != "CAUSE_SUPPORTED":
        return _closed("cause_unresolved")
    if diagnosis_status == "NO_ACTION_REQUIRED":
        return _closed("no_action_required_intentional_or_structural")
    if diagnosis_status in {"INSUFFICIENT_EVIDENCE", "DIAGNOSIS_UNSTABLE"}:
        return _closed(f"status_{diagnosis_status}")
    if diagnosis_status in {"SUPPORTED", "WEAKLY_SUPPORTED"} and actionable:
        return MusicPlanGateResult(
            gate="OPEN",
            reason="supported_cause_and_actionable_musical_target",
            code=None,
            detail={
                "tokens_fresh": True,
                "project_path_match": project_path_match,
            },
        )
    if diagnosis_status in {"SUPPORTED", "WEAKLY_SUPPORTED"}:
        return _closed("supported_but_no_unambiguous_reversible_target")
    return _closed(f"status_{diagnosis_status or 'unknown'}")
