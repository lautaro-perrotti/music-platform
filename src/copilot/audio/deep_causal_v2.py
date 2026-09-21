"""Deterministic causal-evidence evaluation for DEEP_CAUSAL_V2.

This module consumes existing evidence.  It performs no Live RPC, capture,
DSP, reasoning-provider call, or musical write.
"""

from __future__ import annotations

from typing import Any

from copilot.schemas.deep_causal import (
    CausalCandidate,
    CausalContext,
    CausalEvaluation,
    CausalEvidence,
    CausalGrade,
    CausalPath,
    WindowMeasurement,
)
from copilot.schemas.evidence import EvidencePack
from copilot.schemas.music_analysis import MusicAnalysisPack


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
        candidates=list(candidates or []),
        missing_evidence=missing,
        provenance=provenance,
    )


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
