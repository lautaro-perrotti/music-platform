"""Runtime vs persistent object identity.

RuntimeObjectId is valid only for the current Core connection.
PersistentObjectRef is a reconciliable descriptor. Track index is a locator,
never an identity. Name is weak evidence only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from copilot.daw.state_errors import (
    IDENTITY_RECONCILIATION_FAILED,
    PROJECT_MISMATCH,
    TARGET_AMBIGUOUS,
    TARGET_NOT_FOUND,
    StateTrustError,
)
from copilot.daw.state_tokens import dumps_canonical, target_token, token_of
from copilot.schemas.session import SessionState, TrackState


class RuntimeObjectId(BaseModel):
    session_incarnation_id: str
    stable_id: str
    index: int
    object_type: Literal["track"] = "track"


class PersistentObjectRef(BaseModel):
    object_type: Literal["track"] = "track"
    project_identity: str
    role: str
    name: str = ""
    device_names: list[str] = Field(default_factory=list)
    device_classes: list[str] = Field(default_factory=list)
    clip_slots: list[int] = Field(default_factory=list)
    clip_names: list[str] = Field(default_factory=list)
    note_counts: list[int] = Field(default_factory=list)
    grouped: bool = False
    parent_name: str | None = None
    content_fingerprint: str = ""
    target_state_token: str = ""


class ResolveStatus(StrEnum):
    RESOLVED = "RESOLVED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    PROJECT_MISMATCH = "PROJECT_MISMATCH"


class ResolveResult(BaseModel):
    status: ResolveStatus
    track_index: int | None = None
    runtime_id: RuntimeObjectId | None = None
    matches: int = 0
    reason: str = ""


def content_fingerprint(track: TrackState) -> str:
    payload = {
        "role": track.role,
        "device_names": [device.name for device in track.devices],
        "device_classes": [device.class_name for device in track.devices],
        "clip_slots": [clip.slot_index for clip in track.clips],
        "clip_names": [clip.name for clip in track.clips],
        "note_counts": [len(clip.notes) for clip in track.clips],
        "grouped": bool(getattr(track, "grouped", False)),
    }
    return token_of(payload)


def ref_from_track(
    track: TrackState, *, project_identity: str, parent_name: str | None = None
) -> PersistentObjectRef:
    return PersistentObjectRef(
        object_type="track",
        project_identity=project_identity,
        role=track.role,
        name=track.name,
        device_names=[device.name for device in track.devices],
        device_classes=[device.class_name for device in track.devices],
        clip_slots=[clip.slot_index for clip in track.clips],
        clip_names=[clip.name for clip in track.clips],
        note_counts=[len(clip.notes) for clip in track.clips],
        grouped=bool(getattr(track, "grouped", False)),
        parent_name=parent_name,
        content_fingerprint=content_fingerprint(track),
        target_state_token=target_token(track),
    )


def runtime_from_track(track: TrackState, *, session_incarnation_id: str) -> RuntimeObjectId:
    return RuntimeObjectId(
        session_incarnation_id=session_incarnation_id,
        stable_id=track.stable_id,
        index=track.index,
    )


def _fingerprint_tuple(track: TrackState) -> tuple[Any, ...]:
    return (
        track.role,
        tuple(device.name for device in track.devices),
        tuple(device.class_name for device in track.devices),
        tuple(clip.slot_index for clip in track.clips),
        tuple(clip.name for clip in track.clips),
        tuple(len(clip.notes) for clip in track.clips),
        bool(getattr(track, "grouped", False)),
    )


def _ref_tuple(ref: PersistentObjectRef) -> tuple[Any, ...]:
    return (
        ref.role,
        tuple(ref.device_names),
        tuple(ref.device_classes),
        tuple(ref.clip_slots),
        tuple(ref.clip_names),
        tuple(ref.note_counts),
        bool(ref.grouped),
    )


def resolve_track(
    session: SessionState,
    ref: PersistentObjectRef,
    *,
    require_same_project: bool = True,
) -> ResolveResult:
    """Exactly one of RESOLVED / TARGET_NOT_FOUND / TARGET_AMBIGUOUS / PROJECT_MISMATCH.

    Never picks the closest candidate.
    """
    current_project = session.project_identity or ""
    if require_same_project and ref.project_identity and current_project:
        if ref.project_identity != current_project:
            return ResolveResult(
                status=ResolveStatus.PROJECT_MISMATCH,
                matches=0,
                reason="project identity differs",
            )
    candidates = list(session.tracks)
    exact = [
        track
        for track in candidates
        if content_fingerprint(track) == ref.content_fingerprint
        and track.role == ref.role
    ]
    if len(exact) == 1:
        track = exact[0]
        return ResolveResult(
            status=ResolveStatus.RESOLVED,
            track_index=track.index,
            runtime_id=runtime_from_track(
                track, session_incarnation_id=session.session_incarnation_id
            ),
            matches=1,
            reason="content_fingerprint",
        )
    if len(exact) > 1:
        named = [track for track in exact if track.name == ref.name and ref.name]
        if len(named) == 1:
            track = named[0]
            return ResolveResult(
                status=ResolveStatus.RESOLVED,
                track_index=track.index,
                runtime_id=runtime_from_track(
                    track, session_incarnation_id=session.session_incarnation_id
                ),
                matches=1,
                reason="content_fingerprint+name",
            )
        return ResolveResult(
            status=ResolveStatus.TARGET_AMBIGUOUS,
            matches=len(exact),
            reason="multiple identical fingerprints",
        )
    structural = [track for track in candidates if _fingerprint_tuple(track) == _ref_tuple(ref)]
    if len(structural) == 1:
        track = structural[0]
        return ResolveResult(
            status=ResolveStatus.RESOLVED,
            track_index=track.index,
            runtime_id=runtime_from_track(
                track, session_incarnation_id=session.session_incarnation_id
            ),
            matches=1,
            reason="structural_fingerprint",
        )
    if len(structural) > 1:
        return ResolveResult(
            status=ResolveStatus.TARGET_AMBIGUOUS,
            matches=len(structural),
            reason="multiple structural matches",
        )
    return ResolveResult(
        status=ResolveStatus.TARGET_NOT_FOUND,
        matches=0,
        reason="no structural match",
    )


def require_resolved(
    session: SessionState, ref: PersistentObjectRef
) -> TrackState:
    result = resolve_track(session, ref)
    if result.status is ResolveStatus.PROJECT_MISMATCH:
        raise StateTrustError(PROJECT_MISMATCH, result.reason, details=result.model_dump())
    if result.status is ResolveStatus.TARGET_NOT_FOUND:
        raise StateTrustError(TARGET_NOT_FOUND, result.reason, details=result.model_dump())
    if result.status is ResolveStatus.TARGET_AMBIGUOUS:
        raise StateTrustError(TARGET_AMBIGUOUS, result.reason, details=result.model_dump())
    if result.status is not ResolveStatus.RESOLVED or result.track_index is None:
        raise StateTrustError(
            IDENTITY_RECONCILIATION_FAILED,
            result.reason or "unresolved",
            details=result.model_dump(),
        )
    for track in session.tracks:
        if track.index == result.track_index:
            return track
    raise StateTrustError(
        IDENTITY_RECONCILIATION_FAILED,
        "resolved index missing from session",
        details=result.model_dump(),
    )


def dumps_ref(ref: PersistentObjectRef) -> bytes:
    return dumps_canonical(ref.model_dump())
