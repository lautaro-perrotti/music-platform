"""SOURCE_CAPTURE_POOL_V1 — bounded host allocation for Post Mixer capture.

Current dynamic capture already routes a fixed host temporarily and restores.
This pool is the smallest reusable wrapper: Core asks for a PersistentObjectRef
+ region; the pool owns slot/host/journal/restore.

Does not add unbounded parallel capture. Does not rewrite frozen isolation V1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copilot.audio.arrangement_active_source_isolation import (
    HOST_NAME,
    HOST_SLOT,
    capture_source_post_mixer,
)
from copilot.audio.batch_capture import CAPTURE_BASS, CAPTURE_HOST
from copilot.audio.cross_project_bootstrap_v1 import INFRA_HOSTS, retain_tokens
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.object_ref import PersistentObjectRef, ResolveStatus, resolve_track
from copilot.daw.state_tokens import attach_tokens
from copilot.schemas.session import SessionState, TrackState

MILESTONE = "SOURCE_CAPTURE_POOL_V1"
STATUS = "IMPLEMENTED"
POOL_HOSTS = (
    {"name": CAPTURE_BASS, "slot": 2},
    {"name": CAPTURE_HOST, "slot": 1},
)
# Sequential: one host busy at a time. Second host is fallback if first missing.
MAX_PARALLEL = 1


def review_existing_capture() -> dict[str, Any]:
    return {
        "SOURCE_CAPTURE_POOL": "IMPLEMENTED",
        "already_satisfied_partially": True,
        "existing": (
            "arrangement_active_source_isolation.capture_source_post_mixer "
            "temporarily routes one host (Copilot Capture Bass / slot 2) and restores."
        ),
        "gap": (
            "Callers still passed host_index and a display name. "
            "Pool now resolves PersistentObjectRef and selects an idle host."
        ),
        "max_parallel": MAX_PARALLEL,
        "hosts": POOL_HOSTS,
        "rewrote_frozen_v1": False,
    }


def _session_host_index(session: SessionState, name: str) -> int | None:
    track = session.track_by_name(name)
    return None if track is None else track.index


def select_idle_host(session: SessionState) -> dict[str, Any]:
    for spec in POOL_HOSTS:
        index = _session_host_index(session, spec["name"])
        if index is not None:
            return {"name": spec["name"], "slot": spec["slot"], "index": index}
    return {"name": None, "slot": None, "index": None, "error": "no_capture_host"}


def capture_source_post_mixer_ref(
    daw: AbletonTcpAdapter,
    *,
    session: SessionState,
    preflight: dict[str, Any],
    target_ref: PersistentObjectRef | dict[str, Any],
    start_qn: float,
    end_qn: float,
    region_id: str,
    tempo: float,
    dest_root: Path,
) -> dict[str, Any]:
    """Core API: capture_source_post_mixer(target_ref, region)."""
    retain_tokens(session)
    ref = (
        target_ref
        if isinstance(target_ref, PersistentObjectRef)
        else PersistentObjectRef.model_validate(target_ref)
    )
    resolved = resolve_track(session, ref)
    if resolved.status is not ResolveStatus.RESOLVED or resolved.track_index is None:
        return {
            "ok": False,
            "signal_status": "CAPTURE_FAILED",
            "error": f"resolve_{resolved.status.value}",
            "ref": ref.model_dump(mode="json"),
            "milestone": MILESTONE,
        }
    track: TrackState | None = None
    for item in session.tracks:
        if item.index == resolved.track_index:
            track = item
            break
    if track is None:
        return {
            "ok": False,
            "signal_status": "CAPTURE_FAILED",
            "error": "resolved_index_missing",
            "ref": ref.model_dump(mode="json"),
            "milestone": MILESTONE,
        }
    host = select_idle_host(session)
    if host.get("index") is None:
        return {
            "ok": False,
            "signal_status": "CAPTURE_FAILED",
            "error": "no_capture_host",
            "ref": ref.model_dump(mode="json"),
            "milestone": MILESTONE,
        }
    saved_path = session.project_path
    saved_name = session.project_name
    identity_before = {
        "project_identity": session.project_identity,
        "project_token": session.project_token,
        "audible_token": session.audible_token,
        "project_path": saved_path,
        "track_count": len(session.tracks),
    }
    result: dict[str, Any] = {
        "ok": False,
        "signal_status": "CAPTURE_FAILED",
        "error": "capture_did_not_return",
        "ref": ref.model_dump(mode="json"),
    }
    try:
        result = capture_source_post_mixer(
            daw,
            session=session,
            preflight=preflight,
            track=track,
            start_qn=start_qn,
            end_qn=end_qn,
            region_id=region_id,
            tempo=tempo,
            host_index=int(host["index"]),
            dest_root=dest_root,
        )
    finally:
        # Frozen isolation calls attach_tokens(session) without path and would
        # otherwise switch identity from live-set-path to structural fingerprint.
        attach_tokens(session, path=saved_path, name=saved_name)
    result["pool"] = {
        "milestone": MILESTONE,
        "host": host["name"],
        "slot": host["slot"],
        "reused_existing_capture": True,
        "frozen_host_default": HOST_NAME == CAPTURE_BASS and HOST_SLOT == 2,
        "infra_hosts": list(INFRA_HOSTS),
    }
    result["ref"] = ref.model_dump(mode="json")
    result["identity_audit"] = {
        "before": identity_before,
        "after_restore": {
            "project_identity": session.project_identity,
            "project_token": session.project_token,
            "audible_token": session.audible_token,
            "project_path": session.project_path,
            "track_count": len(session.tracks),
        },
        "path_restored": session.project_path == saved_path,
        "identity_restored": session.project_identity == identity_before["project_identity"],
    }
    return result


def capture_sources_post_mixer_batch_refs(
    daw: AbletonTcpAdapter,
    *,
    session: SessionState,
    preflight: dict[str, Any],
    target_refs: list[PersistentObjectRef | dict[str, Any]],
    start_qn: float,
    end_qn: float,
    region_id: str,
    tempo: float,
    dest_root: Path,
    ready_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Batched sibling of capture_source_post_mixer_ref.

    Resolves every PersistentObjectRef, then records the group in ONE playback
    pass. Identity is restored afterwards exactly as in the single-source path:
    frozen isolation calls attach_tokens(session) without a path and would
    otherwise switch identity from the live-set path to a structural
    fingerprint.
    """
    from copilot.audio.source_capture_batch_v1 import (
        MILESTONE as BATCH_MILESTONE,
        capture_sources_post_mixer_batch,
    )

    retain_tokens(session)
    refs = [
        item
        if isinstance(item, PersistentObjectRef)
        else PersistentObjectRef.model_validate(item)
        for item in target_refs
    ]
    tracks: list[Any] = []
    failures: list[dict[str, Any]] = []
    for ref in refs:
        resolved = resolve_track(session, ref)
        if resolved.status is not ResolveStatus.RESOLVED or resolved.track_index is None:
            failures.append(
                {
                    "ok": False,
                    "signal_status": "CAPTURE_FAILED",
                    "error": f"resolve_{resolved.status.value}",
                    "ref": ref.model_dump(mode="json"),
                    "milestone": BATCH_MILESTONE,
                }
            )
            continue
        track = next(
            (item for item in session.tracks if item.index == resolved.track_index), None
        )
        if track is None:
            failures.append(
                {
                    "ok": False,
                    "signal_status": "CAPTURE_FAILED",
                    "error": "resolved_index_missing",
                    "ref": ref.model_dump(mode="json"),
                    "milestone": BATCH_MILESTONE,
                }
            )
            continue
        tracks.append(track)
    # A batch is only meaningful if every member resolved: a partially prepared
    # group would leave one host routed with nothing recording it.
    if failures:
        return [
            {
                "ok": False,
                "signal_status": "CAPTURE_FAILED",
                "error": "BATCH_TARGET_UNRESOLVED",
                "ref": ref.model_dump(mode="json"),
                "milestone": BATCH_MILESTONE,
                "batch_failed_closed": True,
                "resolve_failures": failures,
            }
            for ref in refs
        ]

    saved_path = session.project_path
    saved_name = session.project_name
    identity_before = {
        "project_identity": session.project_identity,
        "project_token": session.project_token,
        "audible_token": session.audible_token,
        "project_path": saved_path,
        "track_count": len(session.tracks),
    }
    try:
        results = capture_sources_post_mixer_batch(
            daw,
            session=session,
            preflight=preflight,
            tracks=tracks,
            start_qn=start_qn,
            end_qn=end_qn,
            region_id=region_id,
            tempo=tempo,
            dest_root=dest_root,
            ready_names=ready_names,
        )
    finally:
        attach_tokens(session, path=saved_path, name=saved_name)

    audit = {
        "before": identity_before,
        "after_restore": {
            "project_identity": session.project_identity,
            "project_token": session.project_token,
            "audible_token": session.audible_token,
            "project_path": session.project_path,
            "track_count": len(session.tracks),
        },
        "path_restored": session.project_path == saved_path,
        "identity_restored": session.project_identity
        == identity_before["project_identity"],
    }
    for result, ref in zip(results, refs):
        result["ref"] = ref.model_dump(mode="json")
        result["identity_audit"] = audit
        result["pool"] = {
            "milestone": BATCH_MILESTONE,
            "batched": True,
            "infra_hosts": list(INFRA_HOSTS),
        }
    return results
