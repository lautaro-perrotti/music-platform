"""Compile producer MusicPlans into the single SafeWrite authority.

The compiler is intentionally conservative. It does not execute Ableton calls
and it never creates a second journal or transaction manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from copilot.daw.object_ref import require_resolved
from copilot.daw.identities import fingerprint_track
from copilot.musicplan import (
    _as_ref,
    validate_duplicate_clip_to_arrangement_plan,
    validate_create_track_plan,
    validate_device_load_plan,
    validate_device_tweak_plan,
    validate_sample_load_plan,
)
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
            ProductionActionKind.SAMPLE_LOAD,
            ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT,
            ProductionActionKind.DEVICE_TWEAK,
            ProductionActionKind.CREATE_PATTERN,
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
        # The first real creative vertical slice is deliberately the only
        # compound plan certified here: create a new Copilot MIDI track,
        # optionally load one native instrument, and put one editable pattern
        # on it. This is still one SafeWrite intent and one durable
        # transaction, not a general multi-action escape hatch.
        if (
            len(plan.actions) in {2, 3}
            and plan.actions[0].action_type is ProductionActionKind.CREATE_TRACK
            and plan.actions[-1].action_type is ProductionActionKind.CREATE_PATTERN
            and (
                len(plan.actions) == 2
                or plan.actions[1].action_type
                in {ProductionActionKind.LOAD_DEVICE, ProductionActionKind.DEVICE_LOAD}
            )
        ):
            return self._compile_midi_variation(plan, session=session)
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
        if action.action_type is ProductionActionKind.CREATE_PATTERN:
            return ProductionCompileResult(
                status="PLAN_REJECTED",
                reasons=("CREATE_PATTERN_REQUIRES_COPILOT_TRACK_COMPOUND_PLAN",),
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
        if action.action_type is ProductionActionKind.SAMPLE_LOAD:
            validated = validate_sample_load_plan(plan, session=session)
            if validated.status.value != "READY_FOR_EXECUTION":
                return ProductionCompileResult(status="PLAN_REJECTED", reasons=(validated.rejection_reason or "SAMPLE_LOAD_PLAN_REJECTED",))
            track = require_resolved(session, _as_ref(action.target.ref))
            params = action.params
            action_id = action.action_id
            audio_track = track.role == "audio"
            intent = MutationIntent(
                plan_id=plan.plan_id, kind=KIND_PRODUCER_EXECUTION_V1, user_intent=action.reason,
                project_identity=session.project_identity or "", expected_revision=session.revision,
                expected_session_hash=session.state_hash, expected_project_token=session.project_token or "",
                expected_audible_token=session.audible_token or "", expected_incarnation_id=session.session_incarnation_id or "",
                targets=[MutationTarget(action_id=action_id, ref=action.target.ref, stable_id=track.stable_id,
                    name_at_plan=track.name, fingerprint=TargetFingerprint(**fingerprint_track(track)),
                    locator=TargetLocator(track_index=track.index), session_incarnation_id=session.session_incarnation_id or "")],
                executions=[MutationExecution(action_id=action_id, action_type="LOAD_SAMPLE", operation="load_browser_item",
                    arguments={"clip_index": params.clip_index, "sample_uri": params.sample_uri},
                    expected_before=({"clip_index": params.clip_index} if audio_track else {"device_count": len(track.devices)}),
                    expected_after=({"sample_uri": params.sample_uri} if audio_track else {"device_count": len(track.devices) + 1, "sample_uri": params.sample_uri}),
                    certified=True, rollback=MutationRollback(inverse_operation="delete_clip",
                        reversibility=RollbackReversibility.INDEPENDENT, prepared=True))],
            )
            if not audio_track:
                intent.executions[0].rollback.inverse_operation = "delete_device"
            return ProductionCompileResult(status="COMPILED", intent=intent, certified_action_ids=(action_id,))
        if action.action_type is ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT:
            validated = validate_duplicate_clip_to_arrangement_plan(plan, session=session)
            if validated.status.value != "READY_FOR_EXECUTION":
                return ProductionCompileResult(status="PLAN_REJECTED", reasons=(validated.rejection_reason or "ARRANGEMENT_PLAN_REJECTED",))
            track = require_resolved(session, _as_ref(action.target.ref))
            params = action.params
            action_id = action.action_id
            intent = MutationIntent(
                plan_id=plan.plan_id, kind=KIND_PRODUCER_EXECUTION_V1, user_intent=action.reason,
                project_identity=session.project_identity or "", expected_revision=session.revision,
                expected_session_hash=session.state_hash, expected_project_token=session.project_token or "",
                expected_audible_token=session.audible_token or "", expected_incarnation_id=session.session_incarnation_id or "",
                targets=[MutationTarget(action_id=action_id, ref=action.target.ref, stable_id=track.stable_id,
                    name_at_plan=track.name, fingerprint=TargetFingerprint(**fingerprint_track(track)),
                    locator=TargetLocator(track_index=track.index, clip_index=params.clip_index), session_incarnation_id=session.session_incarnation_id or "")],
                executions=[MutationExecution(action_id=action_id, action_type="DUPLICATE_CLIP_TO_ARRANGEMENT",
                    operation="duplicate_clip_to_arrangement",
                    arguments={"clip_index": params.clip_index, "destination_time": params.destination_time, "length": params.length},
                    expected_before={}, expected_after={}, certified=True,
                    rollback=MutationRollback(inverse_operation="delete_arrangement_clips",
                        reversibility=RollbackReversibility.INDEPENDENT, prepared=True))],
            )
            return ProductionCompileResult(status="COMPILED", intent=intent, certified_action_ids=(action_id,))
        if action.action_type is ProductionActionKind.DEVICE_TWEAK:
            validated = validate_device_tweak_plan(plan, session=session)
            if validated.status.value != "READY_FOR_EXECUTION":
                return ProductionCompileResult(status="PLAN_REJECTED", reasons=(validated.rejection_reason or "SET_DEVICE_PARAMETER_PLAN_REJECTED",))
            track = require_resolved(session, _as_ref(action.target.ref))
            params = action.params
            action_id = action.action_id
            device = track.devices[int(params.device_index)]
            parameter = next(item for item in device.parameters if item.name.lower() == params.parameter_name.lower())
            intent = MutationIntent(
                plan_id=plan.plan_id, kind=KIND_PRODUCER_EXECUTION_V1, user_intent=action.reason,
                project_identity=session.project_identity or "", expected_revision=session.revision,
                expected_session_hash=session.state_hash, expected_project_token=session.project_token or "",
                expected_audible_token=session.audible_token or "", expected_incarnation_id=session.session_incarnation_id or "",
                targets=[MutationTarget(action_id=action_id, ref=action.target.ref, stable_id=track.stable_id,
                    name_at_plan=track.name, fingerprint=TargetFingerprint(**fingerprint_track(track)),
                    locator=TargetLocator(track_index=track.index, device_index=device.index, parameter_index=parameter.index),
                    session_incarnation_id=session.session_incarnation_id or "")],
                executions=[MutationExecution(action_id=action_id, action_type="SET_DEVICE_PARAMETER", operation="set_device_parameter",
                    arguments={"device_index": device.index, "parameter_index": parameter.index, "value": params.intended_after},
                    expected_before={"value": params.expected_before}, expected_after={"value": params.intended_after}, certified=True,
                    rollback=MutationRollback(inverse_operation="set_device_parameter",
                        inverse_params={"value": params.expected_before}, reversibility=RollbackReversibility.INDEPENDENT,
                        prepared=True))],
            )
            return ProductionCompileResult(status="COMPILED", intent=intent, certified_action_ids=(action_id,))
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

    def _compile_midi_variation(
        self, plan: MusicPlan, *, session: SessionState
    ) -> ProductionCompileResult:
        """Compile one bounded, audible MIDI variation compound plan."""
        create = plan.actions[0]
        device = plan.actions[1] if len(plan.actions) == 3 else None
        pattern = plan.actions[-1]
        create_validated = validate_create_track_plan(
            plan.model_copy(update={"actions": [create]}), session=session
        )
        if create_validated.status.value != "READY_FOR_EXECUTION":
            return ProductionCompileResult(
                status="PLAN_REJECTED",
                reasons=(create_validated.rejection_reason or "CREATE_TRACK_PLAN_REJECTED",),
            )
        params = pattern.params
        if getattr(params, "kind", None) != "create_pattern":
            return ProductionCompileResult(
                status="PLAN_REJECTED", reasons=("PATTERN_PARAMS_INVALID",)
            )
        if not getattr(params, "notes", None):
            return ProductionCompileResult(
                status="PLAN_REJECTED", reasons=("PATTERN_NOTES_REQUIRED",)
            )
        if float(params.length_beats) <= 0:
            return ProductionCompileResult(
                status="PLAN_REJECTED", reasons=("PATTERN_LENGTH_INVALID",)
            )
        if not pattern.rollback or not pattern.rollback.prepared:
            return ProductionCompileResult(
                status="PLAN_REJECTED", reasons=("PATTERN_ROLLBACK_REQUIRED",)
            )
        if device is not None:
            device_params = device.params
            if getattr(device_params, "kind", None) != "device_load":
                return ProductionCompileResult(
                    status="PLAN_REJECTED", reasons=("VARIATION_DEVICE_PARAMS_INVALID",)
                )
            if not str(getattr(device_params, "device_name", "")).strip():
                return ProductionCompileResult(
                    status="PLAN_REJECTED", reasons=("VARIATION_DEVICE_NAME_REQUIRED",)
                )
            if not device.rollback or not device.rollback.prepared:
                return ProductionCompileResult(
                    status="PLAN_REJECTED", reasons=("VARIATION_DEVICE_ROLLBACK_REQUIRED",)
                )

        track_name = create.params.track_name
        create_id = create.action_id
        pattern_id = pattern.action_id
        create_target = MutationTarget(
            action_id=create_id,
            ref=create.target.ref,
            name_at_plan=track_name,
            fingerprint=TargetFingerprint(),
            locator=None,
            session_incarnation_id=session.session_incarnation_id or "",
        )
        # The pattern target intentionally has no pre-existing persistent ref.
        # SafeWrite binds it to the newly-created track after CREATE_TRACK
        # readback, and refuses any pattern action that is not dependent on it.
        pattern_target = MutationTarget(
            action_id=pattern_id,
            ref={"object_type": "track", "project_identity": session.project_identity or "", "role": "midi", "name": track_name},
            name_at_plan=track_name,
            fingerprint=TargetFingerprint(),
            locator=None,
            session_incarnation_id=session.session_incarnation_id or "",
        )
        notes = [note.model_dump(mode="json") for note in params.notes]
        targets = [create_target]
        executions = [
            MutationExecution(
                action_id=create_id,
                action_type="CREATE_TRACK",
                operation="create_midi_track",
                arguments={"name": track_name, "index": create.params.index_hint},
                expected_before={"track_count": len(session.tracks)},
                expected_after={"track_count": len(session.tracks) + 1},
                certified=True,
                rollback=MutationRollback(
                    inverse_operation="delete_track",
                    reversibility=RollbackReversibility.INDEPENDENT,
                    prepared=True,
                ),
            )
        ]
        if device is not None:
            device_target = MutationTarget(
                action_id=device.action_id,
                ref=dict(pattern_target.ref),
                name_at_plan=track_name,
                fingerprint=TargetFingerprint(),
                locator=None,
                session_incarnation_id=session.session_incarnation_id or "",
            )
            device_params = device.params
            targets.append(device_target)
            executions.append(
                MutationExecution(
                    action_id=device.action_id,
                    action_type="LOAD_DEVICE",
                    operation="load_instrument_or_effect",
                    arguments={
                        "uri": device_params.device_uri or device_params.device_name,
                        "device_name": device_params.device_name,
                    },
                    expected_before={"device_count": 0},
                    expected_after={"device_count": 1},
                    certified=True,
                    rollback=MutationRollback(
                        inverse_operation="delete_device",
                        depends_on=[create_id],
                        reversibility=RollbackReversibility.DEPENDENT,
                        prepared=True,
                    ),
                )
            )
        executions.append(
            MutationExecution(
                action_id=pattern_id,
                action_type="CREATE_PATTERN",
                operation="create_pattern",
                arguments={
                    "clip_index": int(params.clip_index),
                    "length_beats": float(params.length_beats),
                    "notes": notes,
                },
                expected_before={"clip_exists": False, "clip_index": int(params.clip_index)},
                expected_after={"clip_index": int(params.clip_index), "note_count": len(notes)},
                certified=True,
                rollback=MutationRollback(
                    inverse_operation="delete_clip",
                    depends_on=[create_id],
                    reversibility=RollbackReversibility.DEPENDENT,
                    prepared=True,
                ),
            )
        )
        targets.append(pattern_target)
        intent = MutationIntent(
            plan_id=plan.plan_id,
            kind=KIND_PRODUCER_EXECUTION_V1,
            user_intent=create.reason,
            project_identity=session.project_identity or "",
            expected_revision=session.revision,
            expected_session_hash=session.state_hash,
            expected_project_token=session.project_token or "",
            expected_audible_token=session.audible_token or "",
            expected_incarnation_id=session.session_incarnation_id or "",
            targets=targets,
            executions=executions,
        )
        return ProductionCompileResult(
            status="COMPILED",
            intent=intent,
            certified_action_ids=(create_id, pattern_id),
        )
