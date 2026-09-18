"""MusicPlan V1 — Core owns typed volume actions. No Ableton mutations here."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.capture_journal_recovery import unresolved_capture_journals
from copilot.daw.object_ref import (
    PersistentObjectRef,
    ResolveStatus,
    ref_from_track,
    require_resolved,
    resolve_track,
    runtime_from_track,
)
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.human_eval.store import now_iso
from copilot.reasoning.musicplan_gate import evaluate_musicplan_gate
from copilot.schemas.musicplan import (
    SCHEMA_VERSION,
    ActionPrecondition,
    ActionTarget,
    ActionType,
    CompiledExecutionEnvelope,
    DeviceLoadActionParams,
    DeviceTweakActionParams,
    DiagnosisBinding,
    DryRunResult,
    ExecutionVerificationSpec,
    ExpectedEffect,
    MusicPlan,
    MusicalVerificationSpec,
    PlanAction,
    PlanIntentClass,
    PlanStatus,
    RollbackSpec,
    SampleSwapActionParams,
    VerificationSpec,
    VolumeActionParams,
    VolumeOperation,
)
from copilot.schemas.session import SessionState, TrackState

VOLUME_TOLERANCE = 0.02
PLANS_DIR = Path("logs") / "musicplans"


def _as_ref(payload: dict[str, Any] | PersistentObjectRef) -> PersistentObjectRef:
    if isinstance(payload, PersistentObjectRef):
        return payload
    return PersistentObjectRef.model_validate(payload)


def new_plan_id() -> str:
    return f"plan_{uuid4().hex[:12]}"


def new_action_id() -> str:
    return f"act_{uuid4().hex[:10]}"


def new_envelope_id() -> str:
    return f"env_{uuid4().hex[:10]}"


def resolve_intended_after(
    *,
    operation: VolumeOperation,
    expected_before: float,
    target_value: float | None,
    delta: float | None,
) -> float:
    if operation is VolumeOperation.SET:
        if target_value is None:
            raise ValueError("SET requires target_value")
        return float(target_value)
    if delta is None:
        raise ValueError("DELTA requires delta")
    return float(expected_before) + float(delta)


def build_set_track_volume_action(
    *,
    track: TrackState,
    project_identity: str,
    operation: VolumeOperation,
    expected_before: float,
    target_value: float | None = None,
    delta: float | None = None,
    reason: str,
    evidence_refs: list[str],
    session_incarnation_id: str = "",
) -> PlanAction:
    intended = resolve_intended_after(
        operation=operation,
        expected_before=expected_before,
        target_value=target_value,
        delta=delta,
    )
    if not (0.0 <= intended <= 1.0):
        raise ValueError(f"intended_after out of range: {intended}")
    ref = ref_from_track(track, project_identity=project_identity)
    runtime = None
    if session_incarnation_id:
        runtime = runtime_from_track(track, session_incarnation_id=session_incarnation_id)
    params = VolumeActionParams(
        operation=operation,
        target_value=target_value if operation is VolumeOperation.SET else None,
        delta=delta if operation is VolumeOperation.DELTA else None,
        expected_before=float(expected_before),
        intended_after=float(intended),
    )
    rollback = RollbackSpec(restore_value=float(expected_before), prepared=True)
    verification = VerificationSpec(
        execution=ExecutionVerificationSpec(expected_after=float(intended)),
        musical=MusicalVerificationSpec(
            comparison="recapture_post_mixer_vs_baseline_later",
            deferred=True,
        ),
    )
    effect = ExpectedEffect(
        affected_target=track.name,
        direction="decrease" if intended < expected_before else (
            "increase" if intended > expected_before else "none"
        ),
        description="reduce this track's contribution level"
        if intended < expected_before
        else "change this track's contribution level",
        measurement_to_compare_after="track.mixer.volume readback",
        limitations=[
            "Does not claim artistic mix improvement.",
            "Ableton volume unit is linear mixer gain (ableton_volume), not musical loudness.",
        ],
    )
    return PlanAction(
        action_id=new_action_id(),
        action_type=ActionType.SET_TRACK_VOLUME,
        target=ActionTarget(
            ref=ref.model_dump(mode="json"),
            runtime_id=None if runtime is None else runtime.model_dump(mode="json"),
            track_index_locator=track.index,
        ),
        params=params,
        reason=reason,
        evidence_refs=list(evidence_refs),
        expected_effect=effect,
        verification=verification,
        rollback=rollback,
        reversible=True,
        preconditions=[
            ActionPrecondition(code="TARGET_EXISTS", detail="track must resolve uniquely"),
            ActionPrecondition(
                code="EXPECTED_VOLUME_MATCH",
                detail="live volume must match expected_before within tolerance",
                expected=expected_before,
            ),
            ActionPrecondition(code="TOKENS_CURRENT", detail="plan tokens must match live"),
            ActionPrecondition(
                code="NO_UNRESOLVED_CAPTURE_TXN",
                detail="no open PREPARED/RECORDING/FINALIZING capture journals",
            ),
            ActionPrecondition(code="ROLLBACK_PREPARED", detail="rollback value prepared"),
            ActionPrecondition(
                code="VERIFICATION_SPEC_PRESENT",
                detail="execution + musical verification specs required",
            ),
            ActionPrecondition(
                code="VOLUME_IN_RANGE",
                detail="intended_after within [0,1]",
                expected={"min": 0.0, "max": 1.0},
            ),
        ],
    )


def build_device_tweak_action(
    *,
    track: TrackState,
    project_identity: str,
    device_index: int,
    parameter_name: str,
    expected_before: float,
    intended_after: float,
    unit: str = "",
    reason: str,
    evidence_refs: list[str],
    session_incarnation_id: str = "",
    allowed_min: float | None = None,
    allowed_max: float | None = None,
) -> PlanAction:
    """Build a DEVICE_TWEAK action (adjust a native device parameter)."""
    ref = ref_from_track(track, project_identity=project_identity)
    runtime = None
    if session_incarnation_id:
        runtime = runtime_from_track(track, session_incarnation_id=session_incarnation_id)
    params = DeviceTweakActionParams(
        device_index=int(device_index),
        parameter_name=parameter_name,
        unit=unit,
        expected_before=float(expected_before),
        intended_after=float(intended_after),
        allowed_min=allowed_min,
        allowed_max=allowed_max,
    )
    rollback = RollbackSpec(
        parameter=f"device[{device_index}].{parameter_name}",
        unit=unit or "normalized",
        restore_value=float(expected_before),
        prepared=True,
    )
    verification = VerificationSpec(
        execution=ExecutionVerificationSpec(
            parameter=f"device[{device_index}].{parameter_name}",
            expected_after=float(intended_after),
            unit=unit or "normalized",
        ),
        musical=MusicalVerificationSpec(
            comparison="recapture_vs_baseline_later",
            deferred=True,
        ),
    )
    effect = ExpectedEffect(
        affected_target=f"{track.name}.{parameter_name}",
        direction="increase" if intended_after > expected_before else (
            "decrease" if intended_after < expected_before else "none"
        ),
        description=f"adjust {parameter_name} on device[{device_index}] of {track.name}",
        measurement_to_compare_after=f"device[{device_index}].{parameter_name} readback",
        limitations=["Normalized parameter value unless a real unit is supplied."],
    )
    return PlanAction(
        action_id=new_action_id(),
        action_type=ActionType.DEVICE_TWEAK,
        target=ActionTarget(
            ref=ref.model_dump(mode="json"),
            runtime_id=None if runtime is None else runtime.model_dump(mode="json"),
            track_index_locator=track.index,
        ),
        params=params,
        reason=reason,
        evidence_refs=list(evidence_refs),
        expected_effect=effect,
        verification=verification,
        rollback=rollback,
        reversible=True,
        preconditions=[
            ActionPrecondition(code="TARGET_EXISTS", detail="track must resolve uniquely"),
            ActionPrecondition(code="DEVICE_EXISTS", detail="device_index must exist on the track"),
            ActionPrecondition(code="TOKENS_CURRENT", detail="plan tokens must match live"),
            ActionPrecondition(code="ROLLBACK_PREPARED", detail="rollback value prepared"),
            ActionPrecondition(code="VERIFICATION_SPEC_PRESENT", detail="execution + musical verification specs required"),
        ],
    )


def abstain_from_diagnosis(
    *,
    diagnosis: DiagnosisBinding,
    project_state_token: str,
    audible_state_token: str,
    target_state_tokens: dict[str, str] | None = None,
    evidence_refs: list[str] | None = None,
    reason: str | None = None,
) -> MusicPlan:
    """INSUFFICIENT_EVIDENCE / NO_ACTION_REQUIRED → non-executable plan."""
    status = PlanStatus.REJECTED
    rejection = reason or f"diagnosis_status_{diagnosis.diagnosis_status}"
    if diagnosis.diagnosis_status in {"INSUFFICIENT_EVIDENCE", "NO_ACTION_REQUIRED"}:
        rejection = diagnosis.diagnosis_status
    return MusicPlan(
        plan_id=new_plan_id(),
        schema_version=SCHEMA_VERSION,
        status=status,
        intent_class=PlanIntentClass.ABSTENTION,
        diagnosis=diagnosis,
        project_state_token=project_state_token,
        audible_state_token=audible_state_token,
        target_state_tokens=dict(target_state_tokens or {}),
        evidence_refs=list(evidence_refs or []),
        actions=[],
        created_at=now_iso(),
        notes=["No executable production MusicPlan from this diagnosis."],
        rejection_reason=rejection,
        gate={"MUSICPLAN_GATE": "CLOSED", "MUSICPLAN_GATE_REASON": rejection},
    )


def plan_from_region_c_r6(artifact: Path | dict[str, Any]) -> MusicPlan:
    """Required abstention regression: REGION_C r6 → no executable actions."""
    payload = (
        artifact
        if isinstance(artifact, dict)
        else __import__("json").loads(Path(artifact).read_text(encoding="utf-8"))
    )
    row = (payload.get("regions") or [{}])[0]
    out = row.get("output") or {}
    diagnosis_status = str(out.get("status") or "INSUFFICIENT_EVIDENCE")
    snap = payload.get("state_snapshot") or {}
    cause = payload.get("cause_result") or {}
    binding = DiagnosisBinding(
        diagnosis_id=f"{payload.get('source_run', 'session_run1')}:r"
        f"{payload.get('diagnosis_revision', 6)}:{payload.get('region_id', 'REGION_C')}",
        diagnosis_revision=int(payload.get("diagnosis_revision") or 6),
        diagnosis_status=diagnosis_status,
        diagnosis_accepted=bool(row.get("accepted")),
        cause_status=(cause.get("status") if isinstance(cause, dict) else None),
        region_id=str(payload.get("region_id") or "REGION_C"),
        artifact=str(payload.get("artifact") or artifact if not isinstance(artifact, dict) else ""),
    )
    return abstain_from_diagnosis(
        diagnosis=binding,
        project_state_token=str(snap.get("PROJECT_STATE_TOKEN") or ""),
        audible_state_token=str(snap.get("AUDIBLE_STATE_TOKEN") or ""),
        target_state_tokens={
            str(snap.get("TARGET_TRACK") or "Strum"): str(snap.get("TARGET_STATE_TOKEN") or "")
        },
        evidence_refs=["sd.cause_status", "fm.event.2.kind", "sa.decision"],
        reason="INSUFFICIENT_EVIDENCE",
    )


def create_controlled_volume_plan(
    *,
    session: SessionState,
    track: TrackState,
    delta: float,
    diagnosis: DiagnosisBinding | None = None,
    evidence_refs: list[str] | None = None,
) -> MusicPlan:
    """CONTROLLED_ENGINEERING_VALIDATION volume delta. Not autonomous improvement."""
    attach_tokens(session)
    current = float(track.mixer.volume)
    action = build_set_track_volume_action(
        track=track,
        project_identity=session.project_identity or "",
        operation=VolumeOperation.DELTA,
        expected_before=current,
        delta=float(delta),
        reason="Controlled engineering validation of SET_TRACK_VOLUME dry-run/write loop.",
        evidence_refs=list(evidence_refs or ["engineering.validation"]),
        session_incarnation_id=session.session_incarnation_id or "",
    )
    binding = diagnosis or DiagnosisBinding(
        diagnosis_id="controlled_engineering_validation",
        diagnosis_revision=None,
        diagnosis_status="SUPPORTED",
        diagnosis_accepted=True,
        cause_status="CAUSE_SUPPORTED",
        region_id=None,
        artifact=None,
    )
    return MusicPlan(
        plan_id=new_plan_id(),
        status=PlanStatus.DRAFT,
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        diagnosis=binding,
        project_state_token=session.project_token or session.project_identity or "",
        audible_state_token=session.audible_token or "",
        target_state_tokens={track.name: target_token(track)},
        evidence_refs=list(evidence_refs or ["engineering.validation"]),
        actions=[action],
        created_at=now_iso(),
        notes=[
            "CONTROLLED_ENGINEERING_VALIDATION — not autonomous musical improvement.",
            "Must not be treated as evidence that the AI improved music.",
        ],
    )


def _volume_in_range(value: float, lo: float = 0.0, hi: float = 1.0) -> bool:
    return lo <= float(value) <= hi


def validate_musicplan(
    plan: MusicPlan,
    *,
    session: SessionState,
    unresolved_capture: list[dict[str, Any]] | None = None,
) -> MusicPlan:
    """Validate tokens, diagnosis justification, target, preconditions. No writes."""
    attach_tokens(session)
    live_project = session.project_token or session.project_identity or ""
    live_audible = session.audible_token or ""

    if plan.intent_class is PlanIntentClass.ABSTENTION or not plan.actions:
        plan = plan.model_copy(deep=True)
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = plan.rejection_reason or "no_executable_actions"
        plan.gate = {
            "MUSICPLAN_GATE": "CLOSED",
            "MUSICPLAN_GATE_REASON": plan.rejection_reason,
        }
        return plan

    if plan.diagnosis.diagnosis_status in {"INSUFFICIENT_EVIDENCE", "NO_ACTION_REQUIRED"}:
        plan = plan.model_copy(deep=True)
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = plan.diagnosis.diagnosis_status
        plan.actions = []
        plan.gate = {
            "MUSICPLAN_GATE": "CLOSED",
            "MUSICPLAN_GATE_REASON": plan.diagnosis.diagnosis_status,
        }
        return plan

    if plan.intent_class is PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT:
        if not plan.diagnosis.diagnosis_accepted:
            plan = plan.model_copy(deep=True)
            plan.status = PlanStatus.REJECTED
            plan.rejection_reason = "diagnosis_not_accepted"
            return plan

    action = plan.actions[0]
    if action.action_type is not ActionType.SET_TRACK_VOLUME:
        plan = plan.model_copy(deep=True)
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "unsupported_action_type"
        return plan

    target_ref = _as_ref(action.target.ref)
    resolved = resolve_track(session, target_ref)
    live_target = ""
    track: TrackState | None = None
    if resolved.status is ResolveStatus.RESOLVED and resolved.track_index is not None:
        try:
            track = require_resolved(session, target_ref)
            live_target = target_token(track)
        except Exception:
            track = None
            live_target = ""

    require_cause = plan.intent_class is PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT
    gate = evaluate_musicplan_gate(
        diagnosis_accepted=bool(plan.diagnosis.diagnosis_accepted)
        or plan.intent_class is PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        diagnosis_status=(
            "SUPPORTED"
            if plan.intent_class is PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION
            else plan.diagnosis.diagnosis_status
        ),
        actionable=True,
        cause_status=(
            "CAUSE_SUPPORTED"
            if plan.intent_class is PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION
            else plan.diagnosis.cause_status
        ),
        require_cause_supported=require_cause,
        evidence_project_token=plan.project_state_token,
        evidence_audible_token=plan.audible_state_token,
        evidence_target_token=next(iter(plan.target_state_tokens.values()), None),
        live_project_token=live_project,
        live_audible_token=live_audible,
        live_target_token=live_target or None,
        require_target=True,
        target_resolve_status=resolved.status.value,
    )

    plan = plan.model_copy(deep=True)
    plan.gate = gate.as_dict()
    action = plan.actions[0]
    action.target.resolve_status = resolved.status.value
    action.target.track_index_locator = resolved.track_index
    if resolved.runtime_id is not None:
        action.target.runtime_id = resolved.runtime_id.model_dump(mode="json")

    if gate.gate != "OPEN":
        if gate.code in {
            "STALE_PROJECT_STATE",
            "STALE_AUDIBLE_STATE",
            "STALE_TARGET_STATE",
        }:
            plan.status = PlanStatus.STALE
        else:
            plan.status = PlanStatus.REJECTED
        plan.rejection_reason = gate.reason
        return plan

    if resolved.status is not ResolveStatus.RESOLVED or track is None:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = resolved.status.value
        return plan

    open_tx = unresolved_capture
    if open_tx is None:
        open_tx = unresolved_capture_journals()

    current_vol = float(track.mixer.volume)
    intended = float(action.params.intended_after)
    expected_before = float(action.params.expected_before)

    checks: list[ActionPrecondition] = []
    for pre in action.preconditions:
        item = pre.model_copy(deep=True)
        if pre.code == "TARGET_EXISTS":
            item.satisfied = True
            item.observed = resolved.status.value
        elif pre.code == "EXPECTED_VOLUME_MATCH":
            item.observed = current_vol
            item.satisfied = abs(current_vol - expected_before) <= float(
                action.params.readback_tolerance
            )
        elif pre.code == "TOKENS_CURRENT":
            item.satisfied = gate.gate == "OPEN"
            item.observed = {
                "live_project": live_project,
                "live_audible": live_audible,
                "live_target": live_target,
            }
        elif pre.code == "NO_UNRESOLVED_CAPTURE_TXN":
            item.observed = open_tx
            item.satisfied = len(open_tx) == 0
        elif pre.code == "ROLLBACK_PREPARED":
            item.satisfied = bool(action.rollback and action.rollback.prepared)
            item.observed = action.rollback.restore_value if action.rollback else None
        elif pre.code == "VERIFICATION_SPEC_PRESENT":
            item.satisfied = bool(
                action.verification
                and action.verification.execution
                and action.verification.musical
            )
        elif pre.code == "VOLUME_IN_RANGE":
            item.observed = intended
            item.satisfied = _volume_in_range(intended)
        else:
            item.satisfied = False
            item.detail = f"unknown_precondition:{pre.code}"
        checks.append(item)
    action.preconditions = checks

    if not action.rollback or not action.rollback.prepared:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "missing_rollback"
        return plan
    if not action.verification or not action.verification.execution:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "missing_verification_spec"
        return plan
    if any(p.required and p.satisfied is False for p in checks):
        failed = [p.code for p in checks if p.required and p.satisfied is False]
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "precondition_failed:" + ",".join(failed)
        return plan

    # Authoritative rollback from current readback at validation time.
    action.rollback = RollbackSpec(
        restore_value=current_vol,
        prepared=True,
        source="authoritative_prewrite_readback",
    )
    action.params.expected_before = current_vol
    if action.params.operation is VolumeOperation.DELTA and action.params.delta is not None:
        action.params.intended_after = current_vol + float(action.params.delta)
    action.verification.execution.expected_after = action.params.intended_after

    if not _volume_in_range(action.params.intended_after):
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "out_of_range_volume"
        return plan

    plan.status = PlanStatus.READY_FOR_EXECUTION
    plan.rejection_reason = None
    return plan


def validate_device_tweak_plan(
    plan: MusicPlan,
    *,
    session: SessionState,
) -> MusicPlan:
    """Validate a DEVICE_TWEAK plan (tokens, target, device/param readback). No writes."""
    attach_tokens(session)
    if not plan.actions:
        plan = plan.model_copy(deep=True)
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "no_actions"
        return plan
    action = plan.actions[0]
    if action.action_type is not ActionType.DEVICE_TWEAK:
        plan = plan.model_copy(deep=True)
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "not_device_tweak"
        return plan

    live_project = session.project_token or session.project_identity or ""
    live_audible = session.audible_token or ""

    target_ref = _as_ref(action.target.ref)
    resolved = resolve_track(session, target_ref)
    track: TrackState | None = None
    live_target = ""
    if resolved.status is ResolveStatus.RESOLVED and resolved.track_index is not None:
        try:
            track = require_resolved(session, target_ref)
            live_target = target_token(track)
        except Exception:
            track = None

    gate = evaluate_musicplan_gate(
        diagnosis_accepted=True,
        diagnosis_status="SUPPORTED",
        actionable=True,
        cause_status="CAUSE_SUPPORTED",
        require_cause_supported=False,
        evidence_project_token=plan.project_state_token,
        evidence_audible_token=plan.audible_state_token,
        evidence_target_token=next(iter(plan.target_state_tokens.values()), None),
        live_project_token=live_project,
        live_audible_token=live_audible,
        live_target_token=live_target or None,
        require_target=True,
        target_resolve_status=resolved.status.value,
    )

    plan = plan.model_copy(deep=True)
    plan.gate = gate.as_dict()
    action = plan.actions[0]
    action.target.resolve_status = resolved.status.value
    action.target.track_index_locator = resolved.track_index

    if gate.gate != "OPEN":
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = gate.reason
        return plan
    if track is None:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = resolved.status.value
        return plan

    params = action.params
    if not isinstance(params, DeviceTweakActionParams):
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "not_device_tweak_params"
        return plan

    device = next((d for d in track.devices if int(d.index) == int(params.device_index)), None)
    if device is None:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "DEVICE_NOT_FOUND"
        return plan

    param = next((pp for pp in device.parameters if pp.name.lower() == params.parameter_name.lower()), None)
    if param is None:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "PARAMETER_NOT_FOUND"
        return plan

    current = float(param.value)
    if abs(current - float(params.expected_before)) > params.readback_tolerance:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = f"EXPECTED_PARAM_MISMATCH current={current} expected={params.expected_before}"
        return plan

    if params.allowed_min is not None and params.intended_after < params.allowed_min:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "out_of_range_param"
        return plan
    if params.allowed_max is not None and params.intended_after > params.allowed_max:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "out_of_range_param"
        return plan

    if not action.rollback or not action.rollback.prepared:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "missing_rollback"
        return plan
    if not action.verification or not action.verification.execution:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "missing_verification_spec"
        return plan

    # authoritative rollback from live readback
    action.rollback = RollbackSpec(
        parameter=action.rollback.parameter,
        unit=action.rollback.unit,
        restore_value=current,
        prepared=True,
        source="authoritative_prewrite_readback",
    )
    action.params.expected_before = current
    action.verification.execution.expected_after = float(params.intended_after)

    plan.status = PlanStatus.READY_FOR_EXECUTION
    plan.rejection_reason = None
    return plan


def _resolve_plan_header(
    plan: MusicPlan,
    session: SessionState,
    expected_type: ActionType,
) -> tuple[MusicPlan, PlanAction | None, TrackState | None]:
    """Shared abstention/action-type/target/gate validation for new action types."""
    attach_tokens(session)
    if not plan.actions:
        plan = plan.model_copy(deep=True)
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "no_actions"
        return plan, None, None
    action = plan.actions[0]
    if action.action_type is not expected_type:
        plan = plan.model_copy(deep=True)
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = f"not_{expected_type.value.lower()}"
        return plan, None, None

    live_project = session.project_token or session.project_identity or ""
    live_audible = session.audible_token or ""

    target_ref = _as_ref(action.target.ref)
    resolved = resolve_track(session, target_ref)
    track: TrackState | None = None
    live_target = ""
    if resolved.status is ResolveStatus.RESOLVED and resolved.track_index is not None:
        try:
            track = require_resolved(session, target_ref)
            live_target = target_token(track)
        except Exception:
            track = None

    gate = evaluate_musicplan_gate(
        diagnosis_accepted=True,
        diagnosis_status="SUPPORTED",
        actionable=True,
        cause_status="CAUSE_SUPPORTED",
        require_cause_supported=False,
        evidence_project_token=plan.project_state_token,
        evidence_audible_token=plan.audible_state_token,
        evidence_target_token=next(iter(plan.target_state_tokens.values()), None),
        live_project_token=live_project,
        live_audible_token=live_audible,
        live_target_token=live_target or None,
        require_target=True,
        target_resolve_status=resolved.status.value,
    )

    plan = plan.model_copy(deep=True)
    plan.gate = gate.as_dict()
    action = plan.actions[0]
    action.target.resolve_status = resolved.status.value
    action.target.track_index_locator = resolved.track_index

    if gate.gate != "OPEN":
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = gate.reason
        return plan, None, None
    if track is None:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = resolved.status.value
        return plan, None, None
    return plan, action, track


def build_device_load_action(
    *,
    track: TrackState,
    project_identity: str,
    device_name: str,
    device_uri: str,
    reason: str,
    evidence_refs: list[str],
    device_index_hint: int = -1,
    session_incarnation_id: str = "",
) -> PlanAction:
    ref = ref_from_track(track, project_identity=project_identity)
    runtime = None
    if session_incarnation_id:
        runtime = runtime_from_track(track, session_incarnation_id=session_incarnation_id)
    params = DeviceLoadActionParams(
        device_name=device_name,
        device_uri=device_uri,
        device_index_hint=device_index_hint,
    )
    rollback = RollbackSpec(
        parameter="device",
        unit="",
        restore_value=-1.0,
        prepared=True,
    )
    verification = VerificationSpec(
        execution=ExecutionVerificationSpec(
            parameter=f"device[{device_name}]",
            expected_after=1.0,
            unit="present",
        ),
        musical=MusicalVerificationSpec(
            comparison="recapture_vs_baseline_later",
            deferred=True,
        ),
    )
    effect = ExpectedEffect(
        affected_target=f"{track.name}.devices",
        direction="add",
        description=f"load native device {device_name}",
        measurement_to_compare_after=f"device presence of {device_name}",
        limitations=["Device loaded but not yet tuned."],
    )
    return PlanAction(
        action_id=new_action_id(),
        action_type=ActionType.DEVICE_LOAD,
        target=ActionTarget(
            ref=ref.model_dump(mode="json"),
            runtime_id=None if runtime is None else runtime.model_dump(mode="json"),
            track_index_locator=track.index,
        ),
        params=params,
        reason=reason,
        evidence_refs=list(evidence_refs),
        expected_effect=effect,
        verification=verification,
        rollback=rollback,
        reversible=True,
        preconditions=[
            ActionPrecondition(code="TARGET_EXISTS", detail="track must resolve uniquely"),
            ActionPrecondition(code="TOKENS_CURRENT", detail="plan tokens must match live"),
            ActionPrecondition(code="ROLLBACK_PREPARED", detail="delete_device inverse prepared"),
            ActionPrecondition(code="VERIFICATION_SPEC_PRESENT", detail="execution + musical verification specs required"),
        ],
    )


def validate_device_load_plan(
    plan: MusicPlan,
    *,
    session: SessionState,
) -> MusicPlan:
    """Validate a DEVICE_LOAD plan (tokens, target, device uri, not duplicate). No writes."""
    plan, action, track = _resolve_plan_header(plan, session, ActionType.DEVICE_LOAD)
    if action is None:
        return plan

    params = action.params
    if not isinstance(params, DeviceLoadActionParams):
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "not_device_load_params"
        return plan
    if not params.device_uri:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "device_uri_required"
        return plan
    existing = [d.name for d in track.devices if d.name.lower() == params.device_name.lower()]
    if existing:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = f"DEVICE_ALREADY_PRESENT {params.device_name}"
        return plan
    if not action.rollback or not action.rollback.prepared:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "missing_rollback"
        return plan
    if not action.verification or not action.verification.execution:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "missing_verification_spec"
        return plan

    plan.status = PlanStatus.READY_FOR_EXECUTION
    plan.rejection_reason = None
    return plan


def build_sample_swap_action(
    *,
    track: TrackState,
    project_identity: str,
    clip_index: int,
    sample_uri: str,
    reason: str,
    evidence_refs: list[str],
    previous_sample_uri: str | None = None,
    session_incarnation_id: str = "",
) -> PlanAction:
    ref = ref_from_track(track, project_identity=project_identity)
    runtime = None
    if session_incarnation_id:
        runtime = runtime_from_track(track, session_incarnation_id=session_incarnation_id)
    params = SampleSwapActionParams(
        clip_index=clip_index,
        sample_uri=sample_uri,
        previous_sample_uri=previous_sample_uri,
    )
    rollback = RollbackSpec(
        parameter="clip_sample",
        unit="uri",
        restore_value=-1.0,
        prepared=True,
    )
    verification = VerificationSpec(
        execution=ExecutionVerificationSpec(
            parameter=f"clip[{clip_index}].sample",
            expected_after=1.0,
            unit="swapped",
        ),
        musical=MusicalVerificationSpec(
            comparison="recapture_vs_baseline_later",
            deferred=True,
        ),
    )
    effect = ExpectedEffect(
        affected_target=f"{track.name}.clip[{clip_index}].sample",
        direction="swap",
        description=f"swap sample on clip slot {clip_index} to {sample_uri}",
        measurement_to_compare_after=f"sample reference of clip {clip_index}",
        limitations=["Sample reference readback requires audio clip model."],
    )
    return PlanAction(
        action_id=new_action_id(),
        action_type=ActionType.SAMPLE_SWAP,
        target=ActionTarget(
            ref=ref.model_dump(mode="json"),
            runtime_id=None if runtime is None else runtime.model_dump(mode="json"),
            track_index_locator=track.index,
        ),
        params=params,
        reason=reason,
        evidence_refs=list(evidence_refs),
        expected_effect=effect,
        verification=verification,
        rollback=rollback,
        reversible=True,
        preconditions=[
            ActionPrecondition(code="TARGET_EXISTS", detail="track must resolve uniquely"),
            ActionPrecondition(code="CLIP_EXISTS", detail="clip_index must resolve on track"),
            ActionPrecondition(code="TOKENS_CURRENT", detail="plan tokens must match live"),
            ActionPrecondition(code="ROLLBACK_PREPARED", detail="previous sample captured for restore"),
            ActionPrecondition(code="VERIFICATION_SPEC_PRESENT", detail="execution + musical verification specs required"),
        ],
    )


def validate_sample_swap_plan(
    plan: MusicPlan,
    *,
    session: SessionState,
) -> MusicPlan:
    """Validate a SAMPLE_SWAP plan (tokens, target, clip slot, sample uri). No writes."""
    plan, action, track = _resolve_plan_header(plan, session, ActionType.SAMPLE_SWAP)
    if action is None:
        return plan

    params = action.params
    if not isinstance(params, SampleSwapActionParams):
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "not_sample_swap_params"
        return plan
    if not params.sample_uri:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "sample_uri_required"
        return plan
    clip = next((c for c in track.clips if c.slot_index == params.clip_index), None)
    if clip is None:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = f"CLIP_NOT_FOUND slot={params.clip_index} slots={[c.slot_index for c in track.clips]}"
        return plan
    if not action.rollback or not action.rollback.prepared:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "missing_rollback"
        return plan
    if not action.verification or not action.verification.execution:
        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = "missing_verification_spec"
        return plan

    plan.status = PlanStatus.READY_FOR_EXECUTION
    plan.rejection_reason = None
    return plan


def compile_execution_envelope(
    plan: MusicPlan,
    *,
    session: SessionState,
) -> CompiledExecutionEnvelope:
    if plan.status is not PlanStatus.READY_FOR_EXECUTION:
        raise ValueError(f"plan not READY_FOR_EXECUTION: {plan.status}")
    if len(plan.actions) != 1:
        raise ValueError("V1 supports exactly one action")
    action = plan.actions[0]
    track = require_resolved(session, _as_ref(action.target.ref))
    resolved = resolve_track(session, _as_ref(action.target.ref))
    return CompiledExecutionEnvelope(
        envelope_id=new_envelope_id(),
        plan_id=plan.plan_id,
        action_id=action.action_id,
        action_type=action.action_type,
        transaction_intent=f"{action.action_type.value}:{track.name}",
        resolved_track_index=int(track.index),
        runtime_id=None
        if resolved.runtime_id is None
        else resolved.runtime_id.model_dump(mode="json"),
        expected_before=float(action.params.expected_before),
        requested_after=float(action.params.intended_after),
        rollback_value=float(action.rollback.restore_value),
        verification=action.verification,
        project_state_token=plan.project_state_token,
        audible_state_token=plan.audible_state_token,
        target_state_token=target_token(track),
        musical_writes_if_executed=1,
        dry_run_only=True,
    )


def dry_run_musicplan(
    plan: MusicPlan,
    *,
    session: SessionState,
    persist_dir: Path | None = None,
) -> DryRunResult:
    """Refresh-validate-compile. ZERO musical mutations."""
    validated = validate_musicplan(plan, session=session)
    attach_tokens(session)
    live_tokens = {
        "PROJECT_STATE_TOKEN": session.project_token or session.project_identity or "",
        "AUDIBLE_STATE_TOKEN": session.audible_token or "",
    }
    if validated.actions:
        target_ref = _as_ref(validated.actions[0].target.ref)
        resolved = resolve_track(session, target_ref)
        live_tokens["TARGET_STATE_TOKEN"] = ""
        current_volume = None
        target_resolution = resolved.model_dump()
        if resolved.status is ResolveStatus.RESOLVED and resolved.track_index is not None:
            track = require_resolved(session, target_ref)
            live_tokens["TARGET_STATE_TOKEN"] = target_token(track)
            current_volume = float(track.mixer.volume)
    else:
        target_resolution = {}
        current_volume = None

    compiled = None
    if validated.status is PlanStatus.READY_FOR_EXECUTION:
        compiled = compile_execution_envelope(validated, session=session)

    result = DryRunResult(
        status="DRY_RUN_COMPLETE",
        plan_id=validated.plan_id,
        plan_status=validated.status,
        gate=validated.gate,
        target_resolution=target_resolution,
        live_tokens=live_tokens,
        current_volume=current_volume,
        preconditions=(validated.actions[0].preconditions if validated.actions else []),
        rollback=(validated.actions[0].rollback if validated.actions else None),
        compiled=compiled,
        would_mutate=False,
        musical_writes=0,
        detail=(
            "Compiled execution envelope only. No Ableton musical mutation performed."
            if compiled
            else f"Plan not executable: {validated.rejection_reason or validated.status}"
        ),
    )
    root = persist_dir or PLANS_DIR
    root.mkdir(parents=True, exist_ok=True)
    artifact = root / f"{validated.plan_id}_dry_run.json"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "plan": validated.model_dump(mode="json"),
        "dry_run": result.model_dump(mode="json"),
        "MUSICAL WRITES": 0,
        "EXECUTED": False,
    }
    artifact.write_text(
        __import__("json").dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    # Immutable plan revision snapshot
    plan_path = root / f"{validated.plan_id}.json"
    if not plan_path.exists():
        plan_path.write_text(
            __import__("json").dumps(
                validated.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str
            ),
            encoding="utf-8",
        )
    result.artifact = str(artifact)
    return result
