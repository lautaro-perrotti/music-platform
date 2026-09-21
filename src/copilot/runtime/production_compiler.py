"""Compile producer MusicPlans into the single SafeWrite authority.

The compiler is intentionally conservative. It does not execute Ableton calls
and it never creates a second journal or transaction manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from copilot.daw.object_ref import require_resolved
from copilot.daw.identities import fingerprint_track
from copilot.musicplan import _as_ref, validate_create_track_plan, validate_device_load_plan
from copilot.runtime.safe_write import volume_intent
from copilot.schemas.musicplan import MusicPlan, ProductionActionKind
from copilot.schemas.safe_write import (
    KIND_PRODUCER_EXECUTION_V1,
    MutationExecution,
    MutationIntent,
    MutationRollback,
    MutationTarget,
    RollbackReversibility,
)
from copilot.schemas.transaction import TargetFingerprint, TargetLocator
from copilot.schemas.session import SessionState


@dataclass(frozen=True)
class ProductionCompileResult:
    status: str
    intent: MutationIntent | None = None
    certified_action_ids: tuple[str, ...] = ()
    uncertified_action_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass
class ProductionCompiler:
    """MusicPlan -> SafeWrite intent compiler; never a writer."""

    certified_kinds: frozenset[ProductionActionKind] = field(
        default_factory=lambda: frozenset({
            ProductionActionKind.SET_TRACK_VOLUME,
            ProductionActionKind.CREATE_TRACK,
            ProductionActionKind.LOAD_DEVICE,
            ProductionActionKind.DEVICE_LOAD,
        })
    )

    def compile(self, plan: MusicPlan, *, session: SessionState) -> ProductionCompileResult:
        if not plan.actions:
            return ProductionCompileResult(status="PLAN_REJECTED", reasons=("NO_ACTIONS",))
        unsupported = tuple(
            action.action_id
            for action in plan.actions
            if action.action_type not in self.certified_kinds
        )
        if unsupported:
            return ProductionCompileResult(
                status="UNCERTIFIED_ACTION",
                uncertified_action_ids=unsupported,
                reasons=("PLAN_CONTAINS_UNCERTIFIED_ACTIONS",),
            )
        if len(plan.actions) != 1:
            return ProductionCompileResult(
                status="PLAN_REJECTED",
                reasons=("ONLY_SINGLE_CERTIFIED_ACTION_SUPPORTED",),
            )

        action = plan.actions[0]
        if action.action_type is ProductionActionKind.CREATE_TRACK:
            validated = validate_create_track_plan(plan, session=session)
            if validated.status.value != "READY_FOR_EXECUTION":
                return ProductionCompileResult(
                    status="PLAN_REJECTED",
                    reasons=(validated.rejection_reason or "CREATE_TRACK_PLAN_REJECTED",),
                )
            params = action.params
            operation = "create_audio_track" if params.track_kind == "audio" else "create_midi_track"
            target_id = action.action_id
            intent = MutationIntent(
                plan_id=plan.plan_id,
                kind=KIND_PRODUCER_EXECUTION_V1,
                user_intent=action.reason,
                project_identity=session.project_identity or "",
                expected_revision=session.revision,
                expected_session_hash=session.state_hash,
                expected_project_token=session.project_token or "",
                expected_audible_token=session.audible_token or "",
                expected_incarnation_id=session.session_incarnation_id or "",
                targets=[
                    MutationTarget(
                        action_id=target_id,
                        ref=action.target.ref,
                        name_at_plan=params.track_name,
                        fingerprint=TargetFingerprint(),
                        locator=None,
                        session_incarnation_id=session.session_incarnation_id or "",
                    )
                ],
                executions=[
                    MutationExecution(
                        action_id=target_id,
                        action_type="CREATE_TRACK",
                        operation=operation,
                        arguments={"name": params.track_name, "index": params.index_hint},
                        expected_before={"track_count": len(session.tracks)},
                        expected_after={"track_count": len(session.tracks) + 1},
                        certified=True,
                        rollback=MutationRollback(
                            inverse_operation="delete_track",
                            reversibility=RollbackReversibility.INDEPENDENT,
                            prepared=True,
                        ),
                    )
                ],
            )
            return ProductionCompileResult(
                status="COMPILED",
                intent=intent,
                certified_action_ids=(target_id,),
            )
        if action.action_type in {ProductionActionKind.LOAD_DEVICE, ProductionActionKind.DEVICE_LOAD}:
            validated = validate_device_load_plan(plan, session=session)
            if validated.status.value != "READY_FOR_EXECUTION":
                return ProductionCompileResult(
                    status="PLAN_REJECTED",
                    reasons=(validated.rejection_reason or "LOAD_DEVICE_PLAN_REJECTED",),
                )
            track = require_resolved(session, _as_ref(action.target.ref))
            params = action.params
            uri = params.device_uri or params.device_name
            action_id = action.action_id
            intent = MutationIntent(
                plan_id=plan.plan_id,
                kind=KIND_PRODUCER_EXECUTION_V1,
                user_intent=action.reason,
                project_identity=session.project_identity or "",
                expected_revision=session.revision,
                expected_session_hash=session.state_hash,
                expected_project_token=session.project_token or "",
                expected_audible_token=session.audible_token or "",
                expected_incarnation_id=session.session_incarnation_id or "",
                targets=[MutationTarget(
                    action_id=action_id,
                    ref=action.target.ref,
                    stable_id=track.stable_id,
                    name_at_plan=track.name,
                    fingerprint=TargetFingerprint(**fingerprint_track(track)),
                    locator=TargetLocator(track_index=track.index),
                    session_incarnation_id=session.session_incarnation_id or "",
                )],
                executions=[MutationExecution(
                    action_id=action_id,
                    action_type="LOAD_DEVICE",
                    operation="load_instrument_or_effect",
                    arguments={"uri": uri, "device_name": params.device_name},
                    expected_before={"device_count": len(track.devices)},
                    expected_after={"device_count": len(track.devices) + 1},
                    certified=True,
                    rollback=MutationRollback(
                        inverse_operation="delete_device",
                        reversibility=RollbackReversibility.INDEPENDENT,
                        prepared=True,
                    ),
                )],
            )
            return ProductionCompileResult(
                status="COMPILED", intent=intent, certified_action_ids=(action_id,)
            )
        track = require_resolved(session, _as_ref(action.target.ref))
        intent = volume_intent(
            session=session,
            track=track,
            requested_after=float(action.params.intended_after),
            plan_id=plan.plan_id,
            user_intent=action.reason,
        )
        return ProductionCompileResult(
            status="COMPILED",
            intent=intent,
            certified_action_ids=(action.action_id,),
        )
