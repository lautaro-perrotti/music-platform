"""A bounded, typed revision proposal; no model-originated DAW commands."""

from __future__ import annotations

import json
import math
from typing import Any

from copilot.musicplan import build_set_track_volume_action
from copilot.schemas.musicplan import VolumeOperation


def volume_revision_action(
    raw: str, *, session: Any, evidence_refs: list[str],
):
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {
        "track_name", "target_volume", "reason"
    }:
        raise ValueError("REVISION_TYPED_VOLUME_OR_ABSTAIN_REQUIRED")
    name = payload["track_name"]
    track = session.track_by_name(name) if isinstance(name, str) else None
    if track is None or track.role not in {"audio", "midi"}:
        raise ValueError("REVISION_TRACK_NOT_RESOLVED")
    value = payload["target_volume"]
    if (
        isinstance(value, bool) or not isinstance(value, (float, int))
        or not math.isfinite(value) or not 0 <= value <= 1
        or abs(value - track.mixer.volume) > .08
    ):
        raise ValueError("REVISION_VOLUME_OUT_OF_BOUNDS")
    reason = payload["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("REVISION_EVIDENCE_REASON_REQUIRED")
    return build_set_track_volume_action(
        track=track, project_identity=session.project_identity,
        operation=VolumeOperation.SET,
        expected_before=track.mixer.volume, target_value=value,
        reason=reason, evidence_refs=evidence_refs,
        session_incarnation_id=session.session_incarnation_id,
    )
