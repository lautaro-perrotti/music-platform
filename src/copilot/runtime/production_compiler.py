"""Compile producer MusicPlans into the single SafeWrite authority.

The compiler is intentionally conservative. It does not execute Ableton calls
and it never creates a second journal or transaction manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from copilot.daw.object_ref import require_resolved
from copilot.musicplan import _as_ref
from copilot.runtime.safe_write import volume_intent
from copilot.schemas.musicplan import MusicPlan, ProductionActionKind
from copilot.schemas.safe_write import MutationIntent
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
        default_factory=lambda: frozenset({ProductionActionKind.SET_TRACK_VOLUME})
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
