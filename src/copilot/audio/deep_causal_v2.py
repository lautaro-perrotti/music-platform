"""Deterministic causal-evidence evaluation for DEEP_CAUSAL_V2.

This module consumes existing evidence.  It performs no Live RPC, capture,
DSP, reasoning-provider call, or musical write.
"""

from __future__ import annotations

import math
from typing import Any

from copilot.schemas.deep_causal import (
    CausalCandidate,
    CausalContext,
    CausalEvent,
    CausalEvaluation,
    CausalEvidence,
    CausalNode,
    CausalNodeKind,
    CausalSourceObservation,
    CausalGrade,
    CausalPath,
    CausalPathKind,
    WindowMeasurement,
)
from copilot.schemas.evidence import EvidencePack
from copilot.schemas.music_analysis import MusicAnalysisPack
from copilot.schemas.session import SessionState


MAIN_NODE_ID = "main"


def _rms_db(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return 20.0 * math.log10(max(number, 1e-12))


def extract_observed_effects(
    pack: EvidencePack,
    analysis_pack: MusicAnalysisPack | None = None,
    *,
    event_rows: list[dict[str, Any]] | None = None,
) -> list[CausalEvent]:
    """Derive observed events from facts; never emit a causal conclusion."""
    events: dict[str, CausalEvent] = {}

    grouped: dict[str, dict[str, Any]] = {}
    for item in pack.items:
        if item.name.startswith("fullmix_energy_event_"):
            suffix = item.name.removeprefix("fullmix_energy_event_")
            event_id = item.evidence_id.rsplit(".", 1)[0]
            grouped.setdefault(event_id, {})[suffix] = item.value
            grouped[event_id].setdefault("refs", []).append(item.evidence_id)
    for event_id, row in grouped.items():
        start = row.get("start_s")
        end = row.get("end_s")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            events[event_id] = CausalEvent(
                event_id=event_id,
                effect_node_id=MAIN_NODE_ID,
                kind=str(row.get("kind") or "UNKNOWN"),
                start_s=float(start),
                end_s=float(end),
                evidence_refs=list(row.get("refs", [])),
                source="EvidencePack.fullmix",
            )

    if analysis_pack is not None:
        bpm = analysis_pack.tempo_bpm
        for transition_index, transition in enumerate(analysis_pack.transitions):
            start_s = transition.start_beat * 60.0 / bpm
            end_s = transition.end_beat * 60.0 / bpm
            event_id = f"music-analysis.transition.{transition_index}"
            events.setdefault(
                event_id,
                CausalEvent(
                    event_id=event_id,
                    effect_node_id=MAIN_NODE_ID,
                    kind=transition.kind,
                    start_s=start_s,
                    end_s=end_s,
                    delta_db=transition.energy_delta_db,
                    evidence_refs=[
                        f"MusicAnalysisPack:transition:{transition_index}",
                        *[f"MusicAnalysisPack:event:{location}" for location in transition.event_locations],
                    ],
                    source="MusicAnalysisPack.transitions",
                ),
            )
        windows = analysis_pack.windows
        for previous, current in zip(windows, windows[1:]):
            if previous.energy_db is None or current.energy_db is None:
                continue
            delta_db = current.energy_db - previous.energy_db
            if abs(delta_db) < 1.5:
                continue
            start_s = current.start_beat * 60.0 / bpm
            end_s = current.end_beat * 60.0 / bpm
            event_id = f"music-analysis.window-change.{current.index}"
            events.setdefault(
                event_id,
                CausalEvent(
                    event_id=event_id,
                    effect_node_id=MAIN_NODE_ID,
                    kind="ENERGY_CHANGE",
                    start_s=start_s,
                    end_s=end_s,
                    delta_db=delta_db,
                    evidence_refs=[*previous.evidence_refs, *current.evidence_refs],
                    source="MusicAnalysisPack.windows",
                ),
            )

    for row in event_rows or []:
        event_id = str(row.get("event_id") or "")
        try:
            start_s = float(row["start_s"])
            end_s = float(row["end_s"])
        except (KeyError, TypeError, ValueError):
            continue
        if not event_id or end_s <= start_s:
            continue
        refs = [str(source.get("evidence_id")) for source in row.get("sources", []) if source.get("evidence_id")]
        source_observations = [
            CausalSourceObservation(
                locator_name=str(source.get("locator_name") or ""),
                before_rms=float(source["before_rms"]) if isinstance(source.get("before_rms"), (int, float)) else None,
                during_rms=float(source["event_rms"]) if isinstance(source.get("event_rms"), (int, float)) else None,
                after_rms=float(source["after_rms"]) if isinstance(source.get("after_rms"), (int, float)) else None,
                signal_class=str(source.get("signal_class") or "UNKNOWN"),
                arrangement_active=source.get("arrangement_active"),
                evidence_refs=[str(source["evidence_id"])] if source.get("evidence_id") else [],
            )
            for source in row.get("sources", [])
        ]
        limitations: list[str] = []
        coverage = row.get("coverage") or {}
        if coverage.get("incomplete"):
            limitations.append("SOURCE_AUDIO_COVERAGE_INCOMPLETE")
        events.setdefault(
            event_id,
            CausalEvent(
                event_id=event_id,
                effect_node_id=MAIN_NODE_ID,
                kind=str(row.get("kind") or "UNKNOWN"),
                start_s=start_s,
                end_s=end_s,
                evidence_refs=refs,
                source_observations=source_observations,
                source="active_source_region_analysis.event_rows",
                limitations=limitations,
            ),
        )
    return list(events.values())


def build_signal_graph(
    session: SessionState,
    *,
    sidechain_sources: set[str] | None = None,
) -> tuple[list[CausalNode], list[CausalPath]]:
    """Build an authoritative graph from one read-only SessionState snapshot."""
    nodes: dict[str, CausalNode] = {MAIN_NODE_ID: CausalNode(node_id=MAIN_NODE_ID, name="Main", kind=CausalNodeKind.MAIN)}
    tracks = sorted(session.tracks, key=lambda track: track.index)
    group_stack: list[str] = []
    parent_group: dict[str, str | None] = {}
    for track in tracks:
        if track.foldable:
            parent_group[track.stable_id] = group_stack[-1] if track.grouped and group_stack else None
            group_stack.append(track.stable_id)
        elif track.grouped:
            parent_group[track.stable_id] = group_stack[-1] if group_stack else None
        else:
            parent_group[track.stable_id] = None
            group_stack.clear()
        kind = CausalNodeKind.GROUP if track.foldable else CausalNodeKind.SOURCE
        nodes.setdefault(track.stable_id, CausalNode(node_id=track.stable_id, name=track.name, kind=kind))
        if sidechain_sources and (track.stable_id in sidechain_sources or track.name in sidechain_sources):
            control_id = f"control:{track.stable_id}"
            nodes[control_id] = CausalNode(node_id=control_id, name=track.name, kind=CausalNodeKind.CONTROL)

    return_tracks = {track.name: track for track in tracks if track.role == "return"}
    for track in return_tracks.values():
        nodes.setdefault(track.stable_id, CausalNode(node_id=track.stable_id, name=track.name, kind=CausalNodeKind.RETURN))

    paths: list[CausalPath] = []
    for track in tracks:
        if track.foldable or track.role in {"return", "master"}:
            continue
        output = (track.routing.output_type or "").strip().lower()
        if output in {"no output", "sends only"}:
            continue
        node_ids = [track.stable_id]
        for device in track.devices:
            device_id = f"device:{track.stable_id}:{device.stable_id}"
            nodes.setdefault(device_id, CausalNode(node_id=device_id, name=device.name, kind=CausalNodeKind.DEVICE))
            node_ids.append(device_id)
        post_id = f"post-mixer:{track.stable_id}"
        nodes.setdefault(post_id, CausalNode(node_id=post_id, name=f"post-mixer:{track.name}", kind=CausalNodeKind.POST_MIXER))
        node_ids.append(post_id)
        group_id = parent_group.get(track.stable_id)
        seen_groups: set[str] = set()
        while group_id and group_id not in seen_groups:
            seen_groups.add(group_id)
            node_ids.append(group_id)
            group_id = parent_group.get(group_id)
        if "main" in output or "master" in output or not output:
            node_ids.append(MAIN_NODE_ID)
        else:
            continue
        direct_path = CausalPath(
            path_id=f"audio:{track.stable_id}:main",
            kind=CausalPathKind.AUDIO,
            node_ids=node_ids,
            evidence_refs=[f"SessionState:track:{track.stable_id}"],
        )
        paths.append(direct_path)
        for send in track.sends:
            return_track = return_tracks.get(send.name)
            if return_track is None:
                continue
            return_path_id = f"audio:{track.stable_id}:return:{return_track.stable_id}"
            direct_path.parallel_path_ids.append(return_path_id)
            paths.append(
                CausalPath(
                    path_id=return_path_id,
                    kind=CausalPathKind.AUDIO,
                    node_ids=[*node_ids[:-1], return_track.stable_id, MAIN_NODE_ID],
                    evidence_refs=[
                        f"SessionState:send:{track.stable_id}:{return_track.stable_id}",
                        f"SessionState:return:{return_track.stable_id}",
                    ],
                )
            )
        if sidechain_sources and (track.stable_id in sidechain_sources or track.name in sidechain_sources):
            control_id = f"control:{track.stable_id}"
            paths.append(
                CausalPath(
                    path_id=f"control:{track.stable_id}",
                    kind=CausalPathKind.CONTROL,
                    node_ids=[track.stable_id, control_id],
                    evidence_refs=[f"SessionState:control:{track.stable_id}"],
                )
            )
    return list(nodes.values()), paths


def generate_causal_candidates(
    events: list[CausalEvent],
    *,
    nodes: list[CausalNode],
    paths: list[CausalPath],
    project_identity: str = "",
    project_token: str = "",
    audible_token: str = "",
) -> list[CausalCandidate]:
    """Enumerate plausible upstream paths; this is not a causal conclusion."""
    node_ids = {node.node_id for node in nodes}
    unique_names: dict[str, list[str]] = {}
    for node in nodes:
        if node.name:
            unique_names.setdefault(node.name, []).append(node.node_id)
    candidates: list[CausalCandidate] = []
    for event in events:
        for path in paths:
            if path.kind != CausalPathKind.AUDIO or not path.valid:
                continue
            if path.node_ids[-1] != event.effect_node_id or path.node_ids[0] not in node_ids:
                continue
            source_measurements: list[WindowMeasurement] = []
            source_node = next((node for node in nodes if node.node_id == path.node_ids[0]), None)
            if source_node is not None and len(unique_names.get(source_node.name, [])) == 1:
                observation = next(
                    (item for item in event.source_observations if item.locator_name == source_node.name),
                    None,
                )
                if observation is not None:
                    for phase, value in (
                        ("before", observation.before_rms),
                        ("during", observation.during_rms),
                        ("after", observation.after_rms),
                    ):
                        if value is not None:
                            source_measurements.append(
                                WindowMeasurement(
                                    node_id=source_node.node_id,
                                    phase=phase,  # type: ignore[arg-type]
                                    level_db=_rms_db(value),
                                    active=observation.signal_class not in {"SILENCE", "NEAR_SILENCE"},
                                    start_s=event.start_s,
                                    end_s=event.end_s,
                                    evidence_refs=list(observation.evidence_refs),
                                    project_identity=project_identity,
                                    project_token=project_token,
                                    audible_token=audible_token,
                                )
                            )
            candidates.append(
                CausalCandidate(
                    candidate_id=f"{event.event_id}:{path.path_id}",
                    event_id=event.event_id,
                    cause_node_id=path.node_ids[0],
                    effect_node_id=event.effect_node_id,
                    path_id=path.path_id,
                    effect_event_start_s=event.start_s,
                    cause_event_start_s=event.start_s if source_measurements else None,
                    measurements=source_measurements,
                    evidence_refs=sorted(set(event.evidence_refs + path.evidence_refs)),
                    counterevidence=list(event.limitations),
                )
            )
    return candidates


def _by_phase(measurements: list[WindowMeasurement], node_id: str) -> dict[str, WindowMeasurement]:
    return {item.phase: item for item in measurements if item.node_id == node_id}


def _delta(values: dict[str, WindowMeasurement]) -> float | None:
    before = values.get("before")
    during = values.get("during")
    if before is None or during is None or before.level_db is None or during.level_db is None:
        return None
    return during.level_db - before.level_db


def _changed(values: dict[str, WindowMeasurement], minimum: float) -> bool | None:
    delta = _delta(values)
    return None if delta is None else abs(delta) >= minimum


def _path_for(context: CausalContext, candidate: CausalCandidate) -> CausalPath | None:
    return next((path for path in context.paths if path.path_id == candidate.path_id), None)


def _identity_ok(context: CausalContext, candidate: CausalCandidate) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not context.project_identity:
        reasons.append("project identity unavailable")
    for measurement in candidate.measurements:
        if context.project_identity and measurement.project_identity != context.project_identity:
            reasons.append("measurement project identity does not match context")
        if context.project_token and measurement.project_token and measurement.project_token != context.project_token:
            reasons.append("measurement project token does not match context")
        if context.audible_token and measurement.audible_token and measurement.audible_token != context.audible_token:
            reasons.append("measurement audible token does not match context")
        if context.evidence_generation is not None and measurement.generation is not None and measurement.generation != context.evidence_generation:
            reasons.append("measurement generation is stale or mixed")
    return not reasons, reasons


def _evaluate_candidate(context: CausalContext, candidate: CausalCandidate) -> CausalEvidence:
    reasons: list[str] = []
    missing: list[str] = list(context.missing_evidence)
    path = _path_for(context, candidate)
    identity_ok, identity_reasons = _identity_ok(context, candidate)
    reasons.extend(identity_reasons)
    path_valid = bool(path and path.valid and candidate.cause_node_id in path.node_ids and candidate.effect_node_id in path.node_ids)
    if not path_valid:
        reasons.append("no valid measured path connects cause and effect")

    cause = _by_phase(candidate.measurements, candidate.cause_node_id)
    effect = _by_phase(candidate.measurements, candidate.effect_node_id)
    cause_changed = _changed(cause, candidate.minimum_change_db)
    effect_changed = _changed(effect, candidate.minimum_change_db)
    if cause_changed is None:
        missing.append("cause before/after level measurements")
    if effect_changed is None:
        missing.append("effect before/after level measurements")

    direction: bool | None = None
    cause_delta = _delta(cause)
    effect_delta = _delta(effect)
    if cause_delta is not None and effect_delta is not None:
        direction = cause_delta * effect_delta > 0
        if direction:
            reasons.append("cause and effect move in the same measured direction")
        else:
            reasons.append("cause and effect directions are incompatible")
    else:
        missing.append("cause/effect direction")

    precedence: bool | None = None
    if candidate.cause_event_start_s is not None and candidate.effect_event_start_s is not None:
        delay_ms = (candidate.effect_event_start_s - candidate.cause_event_start_s) * 1000.0
        precedence = 0 <= delay_ms <= candidate.max_propagation_ms
        reasons.append("event ordering is compatible" if precedence else "event ordering is not compatible")
    else:
        missing.append("cause and effect event locations")

    intermediate_ids = [node for node in (path.node_ids[1:-1] if path else []) if node not in {candidate.cause_node_id, candidate.effect_node_id}]
    intermediate_supported: bool | None = True
    for node_id in intermediate_ids:
        values = _by_phase(candidate.measurements, node_id)
        changed = _changed(values, candidate.minimum_change_db)
        if changed is True:
            continue
        if changed is False:
            intermediate_supported = False
        elif changed is None and intermediate_supported is not False:
            intermediate_supported = None
        if changed is None:
            missing.append(f"intermediate propagation at {node_id}")
    propagation = intermediate_supported
    if propagation is True:
        reasons.append("expected path propagation is supported")

    persistence: bool | None = None
    if cause.get("during") and effect.get("during"):
        persistence = cause["during"].active is not False and effect["during"].active is not False
        if not persistence:
            reasons.append("during-window activity does not persist")
    else:
        missing.append("during-window overlap/activity")

    counter = list(context.counterevidence) + list(candidate.counterevidence)
    if path and path.parallel_path_ids:
        counter.append("parallel path exists: " + ", ".join(path.parallel_path_ids))
    if counter:
        reasons.append("counterevidence remains")

    if not identity_ok:
        grade = CausalGrade.CAUSALITY_UNRESOLVED
    elif not path_valid:
        grade = CausalGrade.COINCIDENT if precedence is True else CausalGrade.CAUSALITY_UNRESOLVED
    elif precedence is False or direction is False or cause_changed is False or effect_changed is False:
        grade = CausalGrade.COINCIDENT
    elif precedence is None or direction is None or cause_changed is None or effect_changed is None:
        grade = CausalGrade.CAUSALITY_UNRESOLVED
    elif counter:
        grade = CausalGrade.CAUSALITY_UNRESOLVED
    elif propagation is True and persistence is not False:
        grade = CausalGrade.STRONG_CAUSAL_SUPPORT
    elif persistence is not False:
        grade = CausalGrade.WEAK_CAUSAL_SUPPORT
    else:
        grade = CausalGrade.COMPATIBLE_WITH_CAUSE

    return CausalEvidence(
        candidate_id=candidate.candidate_id,
        cause_node_id=candidate.cause_node_id,
        effect_node_id=candidate.effect_node_id,
        path_id=candidate.path_id,
        path_kind=None if path is None else path.kind,
        grade=grade,
        event_id=candidate.event_id,
        temporal_precedence=precedence,
        propagation_supported=propagation,
        path_valid=path_valid,
        direction_compatible=direction,
        persistence_supported=persistence,
        reasons=reasons,
        counterevidence=counter,
        missing_evidence=sorted(set(missing)),
        evidence_refs=sorted(set(candidate.evidence_refs + [ref for item in candidate.measurements for ref in item.evidence_refs] + (path.evidence_refs if path else []))),
        project_identity=context.project_identity,
        project_token=context.project_token,
        audible_token=context.audible_token,
        provenance=list(context.provenance),
    )


def evaluate_causal_context(context: CausalContext) -> CausalEvaluation:
    """Evaluate all candidates without changing any project or audio state."""
    if not context.project_identity:
        return CausalEvaluation(
            rejected=True,
            rejection_reason="PROJECT_IDENTITY_MISSING",
            context_provenance=list(context.provenance),
        )
    return CausalEvaluation(
        results=[_evaluate_candidate(context, candidate) for candidate in context.candidates],
        context_provenance=list(context.provenance),
    )


def context_from_evidence(
    pack: EvidencePack,
    *,
    analysis_pack: MusicAnalysisPack | None = None,
    nodes: list[Any] | None = None,
    paths: list[CausalPath] | None = None,
    events: list[CausalEvent] | None = None,
    candidates: list[CausalCandidate] | None = None,
    evidence_generation: int | None = None,
) -> CausalContext:
    """Create a causal context from existing immutable EvidencePack data.

    This is an adapter, not a second evidence producer.  Project identity is
    read from the pack's measured state-token item; absent identity is kept as
    an explicit fail-closed condition.
    """
    identities = {
        str(item.value)
        for item in pack.items
        if item.name == "project_identity" and isinstance(item.value, str) and item.value
    }
    identity = next(iter(identities)) if len(identities) == 1 else ""
    provenance = [f"EvidencePack:{pack.pack_id}"]
    missing: list[str] = []
    if len(identities) > 1:
        missing.append("contradictory project identities in EvidencePack")
    if analysis_pack is not None:
        provenance.append(f"MusicAnalysisPack:{analysis_pack.schema_version}")
    return CausalContext(
        project_identity=identity,
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        evidence_generation=evidence_generation,
        nodes=list(nodes or []),
        paths=list(paths or []),
        events=list(events or []),
        candidates=list(candidates or []),
        missing_evidence=missing,
        provenance=provenance,
    )


def build_deep_causal_context(
    pack: EvidencePack,
    *,
    analysis_pack: MusicAnalysisPack | None = None,
    session: SessionState,
    event_rows: list[dict[str, Any]] | None = None,
    sidechain_sources: set[str] | None = None,
) -> CausalContext:
    """Run fact-to-event-to-candidate generation on existing read-only facts."""
    nodes, paths = build_signal_graph(session, sidechain_sources=sidechain_sources)
    events = extract_observed_effects(pack, analysis_pack, event_rows=event_rows)
    pack_identity = next(
        (
            str(item.value)
            for item in pack.items
            if item.name == "project_identity" and isinstance(item.value, str) and item.value
        ),
        "",
    )
    candidates = generate_causal_candidates(
        events,
        nodes=nodes,
        paths=paths,
        project_identity=pack_identity,
        project_token=pack.project_token,
        audible_token=pack.audible_token,
    )
    context = context_from_evidence(
        pack,
        analysis_pack=analysis_pack,
        nodes=nodes,
        paths=paths,
        events=events,
        candidates=candidates,
    )
    if session.project_identity != context.project_identity:
        context.missing_evidence.append("SessionState project identity differs from EvidencePack")
    if session.project_token and session.project_token != context.project_token:
        context.missing_evidence.append("SessionState project token differs from EvidencePack")
    if session.audible_token and session.audible_token != context.audible_token:
        context.missing_evidence.append("SessionState audible token differs from EvidencePack")
    return context


def causal_report(context: CausalContext, evaluation: CausalEvaluation) -> dict[str, Any]:
    """Serialize the complete derivation for inspection and Astra grounding."""
    return {
        "schema_version": evaluation.schema_version,
        "events": [event.model_dump(mode="json") for event in context.events],
        "nodes": [node.model_dump(mode="json") for node in context.nodes],
        "paths": [path.model_dump(mode="json") for path in context.paths],
        "candidates": [candidate.model_dump(mode="json") for candidate in context.candidates],
        "evaluation": evaluation.model_dump(mode="json"),
        "NO_WRITE": True,
        "MUSICAL_WRITES": 0,
    }


def causal_summary(evaluation: CausalEvaluation) -> dict[str, Any]:
    """Stable, provider-independent summary suitable for an Astra context."""
    return {
        "schema_version": evaluation.schema_version,
        "results": [item.model_dump(mode="json") for item in evaluation.results],
        "rejected": evaluation.rejected,
        "rejection_reason": evaluation.rejection_reason,
        "NO_WRITE": True,
        "MUSICAL_WRITES": 0,
    }
