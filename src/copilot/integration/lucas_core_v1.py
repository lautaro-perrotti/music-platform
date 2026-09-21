"""Core-side adapter for the stable Lucas producer surface.

Lucas decides musical intent. This module only packages Core evidence, validates
the returned MusicPlan, and exposes explicit certified/unsupported action
results. It never receives a DAW object and never performs a write.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copilot.schemas.advanced_perception import AdvancedPerceptionResult
from copilot.schemas.lucas_integration import (
    ContextFact,
    LucasProducerInput,
    ProjectContext,
    ProjectTrackContext,
    ReferenceContext,
    ReferenceSectionContext,
    SampleSetBoundary,
    StyleContext,
    UserIntent,
)
from copilot.schemas.music_analysis import MusicAnalysisPack
from copilot.schemas.musicplan import MusicPlan, PlanAction, ProductionActionKind, SCHEMA_VERSION
from copilot.schemas.session import SessionState
from copilot.daw.state_tokens import target_token
from copilot.sample_library.schemas import LibraryIndex, SampleSetContext

CERTIFIED_ACTIONS = frozenset({
    ProductionActionKind.CREATE_TRACK,
    ProductionActionKind.SAMPLE_LOAD,
    ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT,
    ProductionActionKind.LOAD_DEVICE,
    ProductionActionKind.DEVICE_LOAD,
    ProductionActionKind.DEVICE_TWEAK,
    ProductionActionKind.SET_TRACK_VOLUME,
})


@dataclass(frozen=True)
class PlannerRun:
    plan: MusicPlan
    planner_metadata: dict[str, Any]
    input_context: LucasProducerInput


@dataclass(frozen=True)
class ActionDisposition:
    action_id: str
    action_type: str
    status: str
    reason: str


@dataclass(frozen=True)
class BoundedPlan:
    plan: MusicPlan
    accepted: tuple[ActionDisposition, ...]
    deferred: tuple[ActionDisposition, ...]


def build_reference_context(
    pack: MusicAnalysisPack,
    perception: AdvancedPerceptionResult | None = None,
) -> ReferenceContext:
    """Convert immutable Core evidence into the bounded producer handoff."""
    sections = [
        ReferenceSectionContext(
            name=section.name,
            start_beat=section.start_beat,
            end_beat=section.end_beat,
            function=section.function,
            confidence=section.confidence,
            evidence_refs=list(section.evidence),
        )
        for section in pack.sections
    ]
    facts: list[ContextFact] = []
    for window in pack.windows:
        facts.extend([
            ContextFact(
                domain="energy",
                name=f"window[{window.index}].energy_db",
                value=window.energy_db,
                evidence_refs=list(window.evidence_refs),
                provenance=dict(window.provenance),
                limitations=list(window.limitations),
            ),
            ContextFact(
                domain="lowend",
                name=f"window[{window.index}].kick_bass_relationship",
                value={
                    "kick_energy": window.kick_energy,
                    "bass_energy": window.bass_energy,
                    "overlap_ratio": window.kick_bass_overlap_ratio,
                    "overlap_duration_s": window.kick_bass_overlap_duration_s,
                },
                evidence_refs=list(window.evidence_refs),
                provenance=dict(window.provenance),
                limitations=list(window.limitations),
            ),
            ContextFact(
                domain="groove",
                name=f"window[{window.index}].groove",
                value={
                    "onset_density_per_s": window.groove.onset_density_per_s,
                    "swing_ratio": window.groove.swing_ratio,
                    "offbeat_ratio": window.groove.offbeat_ratio,
                    "repetition_strength": window.groove.repetition_strength,
                },
                evidence_refs=list(window.evidence_refs),
                provenance=dict(window.provenance),
                limitations=list(window.limitations),
            ),
        ])
    if perception is not None:
        for provider in perception.providers:
            facts.append(ContextFact(
                domain="advanced_perception",
                name=f"provider.{provider.name}",
                value={
                    "available": provider.available,
                    "semantic": provider.semantic,
                    "version": provider.version,
                    "metadata": provider.metadata,
                },
                evidence_refs=["advanced_perception.provider_availability"],
                limitations=[provider.reason] if provider.reason else [],
            ))
    refs = list(dict.fromkeys([
        *pack.evidence_refs,
        *(ref for section in pack.sections for ref in section.evidence),
    ]))
    return ReferenceContext(
        reference_state_token=pack.tokens.reference_state_token,
        identity=str(pack.provenance.get("audio_sha256") or pack.tokens.reference_state_token),
        tempo_bpm=pack.tempo_bpm,
        sections=sections,
        facts=facts,
        evidence_refs=refs,
        provenance={
            "pack_schema": pack.schema_version,
            "audio_sha256": pack.provenance.get("audio_sha256"),
            "analyzer_ids": dict(pack.analyzer_ids),
        },
        limitations=list(dict.fromkeys(pack.limitations)),
    )


def build_sample_set_boundary(
    context: SampleSetContext,
    *,
    provenance: dict[str, Any] | None = None,
) -> SampleSetBoundary:
    ids = sorted({
        str(candidate["id"])
        for candidate in context.candidates
        if candidate.get("id")
    })
    return SampleSetBoundary(
        context=context,
        stable_sample_ids=ids,
        provenance=dict(provenance or {}),
    )


def build_project_context(session: SessionState) -> ProjectContext:
    return ProjectContext(
        project_identity=session.project_identity,
        project_token=session.project_token or session.project_identity,
        audible_token=session.audible_token,
        session_incarnation_id=session.session_incarnation_id,
        project_name=session.project_name,
        tracks=[
            ProjectTrackContext(
                stable_id=track.stable_id,
                name=track.name,
                role=track.role,
                index_locator=track.index,
                device_count=len(track.devices),
                clip_count=len(track.clips),
                volume=track.mixer.volume,
            )
            for track in session.tracks
        ],
        facts=[
            ContextFact(
                domain="project",
                name="transport.tempo",
                value=session.transport.tempo,
                provenance={"source": "SessionState"},
            ),
        ],
    )


def build_lucas_input(
    *,
    user_intent: UserIntent,
    reference: ReferenceContext,
    samples: SampleSetContext,
    project: ProjectContext,
    style: StyleContext | None = None,
) -> LucasProducerInput:
    return LucasProducerInput(
        user_intent=user_intent,
        reference=reference,
        samples=build_sample_set_boundary(samples),
        style=style or StyleContext(),
        project=project,
    )


def _grounded_intent(input_context: LucasProducerInput) -> str:
    sections = ", ".join(
        f"{section.name}:{section.start_beat:g}-{section.end_beat:g}"
        for section in input_context.reference.sections
    ) or "UNKNOWN"
    return (
        f"{input_context.user_intent.description}. "
        f"Reference tempo={input_context.reference.tempo_bpm:g} BPM; "
        f"observed sections={sections}; "
        f"reference_token={input_context.reference.reference_state_token}; "
        f"project_identity={input_context.project.project_identity}; "
        f"sample_candidates={len(input_context.samples.stable_sample_ids)}. "
        "Use only these grounded facts; creative choices remain producer intent."
    )


def run_lucas_planner(
    *,
    input_context: LucasProducerInput,
    index: LibraryIndex,
    session: SessionState,
    provider: Any = None,
    planner: Callable[..., tuple[MusicPlan, dict[str, Any]]] | None = None,
    plan_id: str = "lucas_core_integration_v1",
) -> PlannerRun:
    """Invoke the stable Lucas planner without exposing Core write authority."""
    if session.project_identity != input_context.project.project_identity:
        raise ValueError("PROJECT_MISMATCH: planner input is not for this project")
    planner_fn = planner
    if planner_fn is None:
        from copilot.musicplan.astra_plan import build_plan_from_prompt

        planner_fn = build_plan_from_prompt
    plan, metadata = planner_fn(
        index=index,
        session=session,
        intent=_grounded_intent(input_context),
        provider=provider,
        plan_id=plan_id,
    )
    validated = MusicPlan.model_validate(plan.model_dump(mode="json"))
    if validated.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported MusicPlan schema: {validated.schema_version}")
    expected_project_token = input_context.project.project_token
    if validated.project_state_token != expected_project_token:
        raise ValueError("STALE_PLAN: MusicPlan project token does not match ProjectContext")
    refs = list(dict.fromkeys([
        *validated.evidence_refs,
        *input_context.reference.evidence_refs,
    ]))
    validated = validated.model_copy(update={
        "evidence_refs": refs,
        "notes": [
            *validated.notes,
            f"Core reference handoff: {input_context.reference.reference_state_token}",
            "Core remains measurement and write authority; Lucas plan is intent only.",
        ],
    })
    return PlannerRun(plan=validated, planner_metadata=dict(metadata), input_context=input_context)


def run_lucas_critique(
    *,
    plan: MusicPlan,
    session: SessionState,
    provider: Any = None,
    timeout_s: float = 120.0,
):
    """Invoke Lucas's advisory critique surface through a Core read-only boundary.

    The critique may use Astra internally, but it never receives a DAW adapter and
    never authorizes a write. Keeping this call in the Core adapter makes the
    planner and critique call chains explicit instead of calling the Lucas module
    ad hoc from a validation script.
    """
    from copilot.musicplan.critique import critique_track

    return critique_track(
        plan=plan,
        session=session,
        provider=provider,
        timeout_s=timeout_s,
    )


def bound_plan_to_certified_actions(
    plan: MusicPlan,
    *,
    action_ids: Iterable[str],
) -> BoundedPlan:
    """Select an explicit bounded subset; every other action stays traceable."""
    requested = set(action_ids)
    known = {action.action_id for action in plan.actions}
    unknown = requested - known
    if unknown:
        raise ValueError(f"unknown action IDs in bounded selection: {sorted(unknown)}")
    accepted: list[ActionDisposition] = []
    deferred: list[ActionDisposition] = []
    selected: list[PlanAction] = []
    for action in plan.actions:
        if action.action_id in requested and action.action_type in CERTIFIED_ACTIONS:
            selected.append(action)
            accepted.append(ActionDisposition(
                action_id=action.action_id,
                action_type=action.action_type.value,
                status="ACCEPTED",
                reason="explicitly selected certified action",
            ))
        else:
            reason = (
                "not in bounded execution selection"
                if action.action_id not in requested
                else "action is outside certified producer vocabulary"
            )
            deferred.append(ActionDisposition(
                action_id=action.action_id,
                action_type=action.action_type.value,
                status="DEFERRED_UNSUPPORTED" if action.action_type not in CERTIFIED_ACTIONS else "DEFERRED",
                reason=reason,
            ))
    # Plan IDs are also used by SafeWrite for durable pre-state filenames.
    # Keep the derived ID portable across hosts; ':' is invalid in Windows
    # filenames and would fail before the first DAW mutation.
    bounded = plan.model_copy(update={
        "plan_id": f"{plan.plan_id}_bounded",
        "actions": selected,
    })
    return BoundedPlan(plan=bounded, accepted=tuple(accepted), deferred=tuple(deferred))


def rebind_sample_load_action(
    action: PlanAction,
    *,
    track,
    session: SessionState,
) -> PlanAction:
    """Bind Lucas's stable sample intent to Core's authoritative post-create track."""
    if action.action_type is not ProductionActionKind.SAMPLE_LOAD:
        raise ValueError("only SAMPLE_LOAD actions can be rebound")
    from copilot.musicplan import build_sample_load_action

    params = action.params
    rebound = build_sample_load_action(
        track=track,
        project_identity=session.project_identity,
        clip_index=int(params.clip_index),
        sample_uri=str(params.sample_uri),
        reason=action.reason,
        evidence_refs=list(action.evidence_refs),
        session_incarnation_id=session.session_incarnation_id,
    )
    return rebound.model_copy(update={"action_id": action.action_id})


def rebind_sample_load_plan(
    plan: MusicPlan,
    action: PlanAction,
    *,
    track,
    session: SessionState,
) -> MusicPlan:
    """Rebind a Lucas sample intent after Core authoritatively creates a track.

    A virtual Lucas track has no valid target-state token.  The token must be
    minted from the post-create authoritative readback before the sample load
    can pass the normal MusicPlan gate.
    """
    rebound = rebind_sample_load_action(action, track=track, session=session)
    return plan.model_copy(update={
        "actions": [rebound],
        "project_state_token": session.project_token or session.project_identity or "",
        "audible_state_token": session.audible_token or "",
        "target_state_tokens": {track.name: target_token(track)},
    })


def normalize_sample_uri_for_working_copy(sample_uri: str) -> str:
    """Translate Lucas library-relative paths to the typed Live browser URI."""
    value = str(sample_uri).replace("\\", "/")
    if value.startswith("query:CurrentProject#Samples:"):
        prefix, relative = value.split("#Samples:", 1)
        return f"{prefix}#Samples:{relative.strip('/').replace('/', ':')}"
    if value.startswith("query:") or value.startswith("browser://"):
        return value
    if value.startswith("Samples/"):
        value = value[len("Samples/"):]
    return f"query:CurrentProject#Samples:{value.lstrip('/').replace('/', ':')}"
