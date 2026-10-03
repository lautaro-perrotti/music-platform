"""Compile one original symbolic candidate into the existing MusicPlan vocabulary.

This creates intent only. ProductionCompiler and SafeWrite remain the sole
execution authority, and concrete browser assets must be resolved separately.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from copilot.musicplan import (
    build_create_track_action, build_device_load_action,
    build_duplicate_clip_to_arrangement_action, build_pattern_action,
)
from copilot.producer.prompt_groove_v1 import SymbolicCandidate, validate_candidate
from copilot.producer.track_spec import TrackSpec
from copilot.schemas.musicplan import DiagnosisBinding, MusicPlan, PlanIntentClass, PlanStatus
from copilot.schemas.session import SessionState, TrackState


def build_prompt_groove_musicplan(
    *, spec: TrackSpec, candidate: SymbolicCandidate, session: SessionState,
    drum_sample_uris: dict[str, str], stock_device_uris: dict[str, str],
) -> MusicPlan:
    """Produce a single 16-bar plan; never resolve assets by guessed names."""
    validate_candidate(candidate, spec)
    if (
        not session.connected or not session.project_identity
        or not session.project_path or not session.project_path.lower().endswith(".als")
        or not session.project_token or not session.audible_token
    ):
        raise ValueError("AUTHORITATIVE_PROJECT_SESSION_REQUIRED")
    if (
        session.transport.tempo != spec.bpm
        or (session.transport.signature_numerator, session.transport.signature_denominator)
        != (spec.meter_numerator, spec.meter_denominator)
    ):
        raise ValueError("SESSION_TIMING_MISMATCH")
    if set(drum_sample_uris) != {"KICK", "HAT"} or any(not uri for uri in drum_sample_uris.values()):
        raise ValueError("EXACT_DRUM_SAMPLE_ASSETS_REQUIRED")
    if set(stock_device_uris) != {"BASS", "HARMONY", "HOOK"} or any(not uri for uri in stock_device_uris.values()):
        raise ValueError("EXACT_STOCK_DEVICE_ASSETS_REQUIRED")
    names = {role: f"GEN_{role}" for role in candidate.notes_by_role}
    if any(track.name in names.values() for track in session.tracks):
        raise ValueError("GENERATED_TRACK_ALREADY_EXISTS")
    payload = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    spec_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    refs = [f"accepted-track-spec-sha256:{spec_digest}", f"symbolic:{candidate.candidate_id}"]
    actions = []
    for role in ("KICK", "HAT", "BASS", "HARMONY", "HOOK"):
        notes = candidate.notes_by_role[role]
        if not notes:
            raise ValueError(f"EMPTY_GENERATED_ROLE:{role}")
        name = names[role]
        virtual = TrackState(stable_id="", index=-1, name=name, role="midi")
        actions.append(build_create_track_action(
            project_identity=session.project_identity, track_name=name, track_kind="midi",
            reason="Original prompt-derived symbolic lane", evidence_refs=refs,
        ))
        if role not in drum_sample_uris:
            actions.append(build_device_load_action(
                track=virtual, project_identity=session.project_identity,
                device_name="Operator", device_uri=stock_device_uris[role],
                reason="Resolved stock instrument for editable symbolic role", evidence_refs=refs,
            ))
        actions.append(build_pattern_action(
            track=virtual, project_identity=session.project_identity, clip_index=0,
            length_beats=64.0, notes=notes, reason="Original sixteen-bar phrase",
            evidence_refs=refs,
        ))
        actions.append(build_duplicate_clip_to_arrangement_action(
            track=virtual, project_identity=session.project_identity, clip_index=0,
            destination_time=0.0, length=None, reason="Place original sixteen-bar phrase",
            evidence_refs=refs,
        ))
    return MusicPlan(
        plan_id=f"prompt_groove_{spec_digest[:12]}_{candidate.candidate_id[-1].lower()}",
        status=PlanStatus.READY_FOR_EXECUTION,
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        diagnosis=DiagnosisBinding(
            diagnosis_id=f"producer-intent:{spec_digest}",
            diagnosis_status="ACCEPTED_STRUCTURED_MUSICAL_INTENT",
            diagnosis_accepted=True,
        ),
        project_state_token=session.project_token, audible_state_token=session.audible_token,
        evidence_refs=refs, actions=actions,
        notes=["Original symbolic candidate; no reference audio or reconstructed MIDI used.",
               "Drum sample loads must follow as separate certified SafeWrite actions after track readback.",
               "Structural selection is not a musical quality judgment."],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
