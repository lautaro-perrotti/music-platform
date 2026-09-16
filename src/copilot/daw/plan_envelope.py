"""Generic plan envelope. No musical autonomy. No write under mismatch/stale/ambiguity."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from copilot.daw.object_ref import (
    PersistentObjectRef,
    ResolveStatus,
    require_resolved,
    resolve_track,
)
from copilot.daw.state_errors import (
    PROJECT_MISMATCH,
    STATE_TOKEN_MISMATCH,
    STATE_UNAVAILABLE,
    STALE_PLAN,
    TARGET_AMBIGUOUS,
    TARGET_NOT_FOUND,
    StateTrustError,
)
from copilot.daw.state_tokens import StateScope, token_for_scope
from copilot.schemas.session import SessionState, TrackState


class PlanEnvelope(BaseModel):
    project_token: str
    observed_scope: StateScope
    observed_state_token: str
    target_ref: PersistentObjectRef | None = None
    target_state_token: str | None = None
    evidence_asset_ids: list[str] = Field(default_factory=list)
    created_at: str | None = None
    intended_operation: str = ""
    audible_source_names: list[str] | None = None


class AuthorizeResult(BaseModel):
    decision: str
    observed_project_token: str
    current_project_token: str
    observed_state_token: str
    current_state_token: str
    target_resolution: dict[str, Any] = Field(default_factory=dict)
    current_target_state_token: str | None = None
    code: str | None = None


def _current_token(
    session: SessionState, envelope: PlanEnvelope, track: TrackState | None
) -> str:
    names = (
        frozenset(envelope.audible_source_names)
        if envelope.audible_source_names is not None
        else None
    )
    return token_for_scope(
        session,
        envelope.observed_scope,
        track=track,
        track_names=names,
    )


def authorize_execution(
    session: SessionState, envelope: PlanEnvelope
) -> AuthorizeResult:
    """Read current relevant state and compare. Fail closed. No best-guess write."""
    if not session.connected:
        raise StateTrustError(STATE_UNAVAILABLE, "session is not connected")
    current_project = session.project_identity or ""
    observed_project = envelope.project_token
    track: TrackState | None = None
    resolution: dict[str, Any] = {}
    if envelope.target_ref is not None:
        resolved = resolve_track(session, envelope.target_ref)
        resolution = resolved.model_dump()
        if resolved.status is ResolveStatus.PROJECT_MISMATCH:
            raise StateTrustError(
                PROJECT_MISMATCH,
                "plan project does not match current Live Set",
                details={"journal": _journal(envelope, session, resolution, PROJECT_MISMATCH)},
            )
        if resolved.status is ResolveStatus.TARGET_NOT_FOUND:
            raise StateTrustError(
                TARGET_NOT_FOUND,
                resolved.reason,
                details={"journal": _journal(envelope, session, resolution, TARGET_NOT_FOUND)},
            )
        if resolved.status is ResolveStatus.TARGET_AMBIGUOUS:
            raise StateTrustError(
                TARGET_AMBIGUOUS,
                resolved.reason,
                details={"journal": _journal(envelope, session, resolution, TARGET_AMBIGUOUS)},
            )
        track = require_resolved(session, envelope.target_ref)
    elif observed_project and current_project and observed_project != current_project:
        raise StateTrustError(
            PROJECT_MISMATCH,
            "plan project does not match current Live Set",
            details={"journal": _journal(envelope, session, resolution, PROJECT_MISMATCH)},
        )
    try:
        current_state = _current_token(session, envelope, track)
    except ValueError as exc:
        raise StateTrustError(STATE_UNAVAILABLE, str(exc)) from exc
    current_target_token = None
    if track is not None:
        from copilot.daw.state_tokens import target_token

        current_target_token = target_token(track)
    if envelope.observed_state_token != current_state:
        raise StateTrustError(
            STALE_PLAN,
            f"{envelope.observed_scope} token changed since observation",
            details={
                "journal": _journal(
                    envelope,
                    session,
                    resolution,
                    STALE_PLAN,
                    current_state=current_state,
                    current_target_token=current_target_token,
                )
            },
        )
    if (
        envelope.target_state_token
        and current_target_token
        and envelope.target_state_token != current_target_token
    ):
        raise StateTrustError(
            STATE_TOKEN_MISMATCH,
            "target state token changed since observation",
            details={
                "journal": _journal(
                    envelope,
                    session,
                    resolution,
                    STATE_TOKEN_MISMATCH,
                    current_state=current_state,
                    current_target_token=current_target_token,
                )
            },
        )
    return AuthorizeResult(
        decision="ALLOW",
        observed_project_token=observed_project,
        current_project_token=current_project,
        observed_state_token=envelope.observed_state_token,
        current_state_token=current_state,
        target_resolution=resolution,
        current_target_state_token=current_target_token,
    )


def observe_plan(
    session: SessionState,
    *,
    scope: StateScope,
    track: TrackState | None = None,
    intended_operation: str = "",
    evidence_asset_ids: list[str] | None = None,
    audible_source_names: list[str] | None = None,
) -> PlanEnvelope:
    names = (
        frozenset(audible_source_names) if audible_source_names is not None else None
    )
    from copilot.daw.object_ref import ref_from_track

    ref = None
    target_tok = None
    if track is not None:
        ref = ref_from_track(track, project_identity=session.project_identity)
        from copilot.daw.state_tokens import target_token

        target_tok = target_token(track)
    return PlanEnvelope(
        project_token=session.project_identity,
        observed_scope=scope,
        observed_state_token=token_for_scope(
            session, scope, track=track, track_names=names
        ),
        target_ref=ref,
        target_state_token=target_tok,
        evidence_asset_ids=list(evidence_asset_ids or []),
        intended_operation=intended_operation,
        audible_source_names=audible_source_names,
    )


def _journal(
    envelope: PlanEnvelope,
    session: SessionState,
    resolution: dict[str, Any],
    code: str,
    *,
    current_state: str | None = None,
    current_target_token: str | None = None,
) -> dict[str, Any]:
    return {
        "observed_project_token": envelope.project_token,
        "observed_state_token": envelope.observed_state_token,
        "current_project_token": session.project_identity,
        "current_pre_write_token": current_state,
        "target_resolution": resolution,
        "code": code,
        "intended_operation": envelope.intended_operation,
        "current_target_state_token": current_target_token,
    }
