"""Generic arrangement-active source inventory.

Does not depend on Drums / Rose Bass / Sub Sub Bass / Coffee Leaf / Kick 808 Deep.
Frozen V1 isolation remains untouched. This module is identity/routing/eligibility only.
"""

from __future__ import annotations

from typing import Any

from copilot.audio.arrangement_activity import clips_overlap_region
from copilot.audio.cross_project_bootstrap_v1 import (
    is_exact_infra_name,
    is_lookalike_user_track,
    retain_tokens,
)
from copilot.daw.object_ref import ref_from_track, resolve_track
from copilot.schemas.session import SessionState, TrackState

MILESTONE = "GENERIC_SOURCE_ISOLATION_V1"
SOURCE_BUDGET = 4


def _material_for_track(
    track: TrackState,
    clips: list[dict[str, Any]],
    start_qn: float,
    end_qn: float,
) -> tuple[str, int]:
    own = [clip for clip in clips if str(clip.get("track") or "") == track.name]
    if track.foldable or track.role == "unknown":
        group_hits = [
            clip
            for clip in clips
            if str(clip.get("group") or "") == track.name
            or str(clip.get("track") or "") == track.name
        ]
        hits = clips_overlap_region(group_hits, start_qn, end_qn)
    else:
        parent = None
        # grouped children already tagged via clip.group when loading .als
        grouped = [
            clip
            for clip in clips
            if str(clip.get("track") or "") == track.name
            or (
                str(clip.get("group") or "") == track.name
                and bool(getattr(track, "foldable", False))
            )
        ]
        hits = clips_overlap_region(grouped or own, start_qn, end_qn)
        del parent
    if clips and not own and not track.foldable:
        # ALS available but this track has no overlapping arrangement clip.
        return ("NO_MATERIAL" if not hits else "HAS_MATERIAL", len(hits))
    if not clips:
        return ("UNKNOWN", 0)
    return ("HAS_MATERIAL" if hits else "NO_MATERIAL", len(hits))


def inventory_generic_sources(
    *,
    session: SessionState,
    clips: list[dict[str, Any]],
    start_qn: float,
    end_qn: float,
) -> dict[str, Any]:
    retain_tokens(session)
    project_identity = session.project_identity or session.project_token or ""
    rows: list[dict[str, Any]] = []
    for track in session.tracks:
        if track.role in {"return", "master"}:
            continue
        if is_exact_infra_name(track.name) or is_lookalike_user_track(track.name):
            continue
        material, n_hits = _material_for_track(track, clips, start_qn, end_qn)
        ref = ref_from_track(
            track,
            project_identity=project_identity,
            parent_name=None,
        )
        resolved = resolve_track(session, ref)
        eligible_role = track.role in {"midi", "audio"}
        muted = bool(track.mixer.mute)
        capture_ok = eligible_role and material == "HAS_MATERIAL" and not muted
        if material == "UNKNOWN":
            reason = "arrangement_clips_unavailable"
        elif material != "HAS_MATERIAL":
            reason = "no_material"
        elif muted:
            reason = "muted_source"
        elif not eligible_role:
            reason = "unsupported_role"
        else:
            reason = "has_material_and_routable"
        rows.append(
            {
                "ref": ref.model_dump(mode="json"),
                "display_name": track.name,
                "role": track.role,
                "grouped": bool(track.grouped),
                "foldable": bool(track.foldable),
                "arrangement_material": material,
                "overlapping_clips": n_hits,
                "mute": muted,
                "solo": bool(track.mixer.solo),
                "routing": {
                    "input_type": track.routing.input_type,
                    "input_channel": track.routing.input_channel,
                    "output_type": track.routing.output_type,
                    "output_channel": track.routing.output_channel,
                    "monitoring": track.routing.monitoring,
                },
                "post_mixer_source": True,
                "capture_eligibility": {
                    "eligible": capture_ok,
                    "reason": reason,
                },
                "resolve_status": resolved.status.value,
                "track_index_locator_only": track.index,
            }
        )
    active = [row for row in rows if row["arrangement_material"] == "HAS_MATERIAL"]
    eligible = [
        row for row in rows if (row.get("capture_eligibility") or {}).get("eligible")
    ]
    return {
        "ok": True,
        "milestone": MILESTONE,
        "region": {"start_qn": start_qn, "end_qn": end_qn},
        "method": "generic clips ∩ PersistentObjectRef — no development-song names",
        "sources": rows,
        "active_count": len(active),
        "eligible_count": len(eligible),
        "bounded_targets": eligible[:SOURCE_BUDGET],
        "source_budget": SOURCE_BUDGET,
        "limitation": (
            "HAS_MATERIAL is arrangement clip overlap only. "
            "Audible contribution requires Post Mixer capture. "
            "Silence after capture remains valid evidence."
        ),
    }


def select_activity_region(
    clips: list[dict[str, Any]],
    *,
    window_qn: float = 32.0,
) -> dict[str, Any] | None:
    """First window with any overlapping arrangement clip. Not a musical judgement."""
    if not clips:
        return None
    starts = [float(clip["start_qn"]) for clip in clips]
    start = min(starts)
    # snap down to bar
    start = float(int(start / 4.0) * 4)
    return {
        "id": f"AUTO_{int(start)}_{int(start + window_qn)}",
        "start_qn": start,
        "end_qn": start + window_qn,
        "why": "first_arrangement_activity_window",
    }
