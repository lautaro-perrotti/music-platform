from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
from uuid import uuid4

from pydantic import ValidationError

from copilot.perf.trace import CAT_CPU, CAT_MODEL, span
from copilot.reasoning.claims import classify_output
from copilot.reasoning.errors import ProviderError, ReasoningFailure
from copilot.reasoning.evidence_input import (
    limitation_codes_from_scoped,
    scoped_evidence,
    serialize_for_prompt,
)
from copilot.reasoning.grounding import validate_pack_facts, validate_reasoning
from copilot.reasoning.prompt import build_prompt
from copilot.reasoning.provider import ReasoningProvider
from copilot.reasoning.schema import (
    ANALYSIS_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ReasoningCandidate,
    ReasoningOutput,
)
from copilot.schemas.diagnosis import (
    CandidateAction,
    Confidence,
    DiagnosisStatus,
    EvidenceStatus,
    Finding,
    FindingType,
    Hypothesis,
    MusicDiagnosis,
)
from copilot.schemas.evidence import EvidencePack

SCHEMA_ATTEMPTS = 2
MODEL_TIMEOUT_S = 30.0


@dataclass
class ReasoningAudit:
    provider: str
    provider_version: str
    prompt_version: str
    schema_version: str
    analysis_version: str
    pack_id: str
    evidence_ids: list[str]
    project_token: str
    audible_token: str
    target_token: str | None
    output_hash: str | None = None
    raw_hash: str | None = None
    attempts: int = 0
    timings: dict[str, float] = field(default_factory=dict)
    request_decisions: list[dict[str, str]] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)
    input_stats: dict[str, object] = field(default_factory=dict)
    claim_classifications: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ReasoningResult:
    accepted: bool
    failure: ReasoningFailure | None
    diagnosis: MusicDiagnosis | None
    output: ReasoningOutput | None
    audit: ReasoningAudit
    musical_writes: int = 0

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "failure": None if self.failure is None else self.failure.value,
            "musical_writes": self.musical_writes,
            "diagnosis": None if self.diagnosis is None else self.diagnosis.model_dump(),
            "output": None if self.output is None else self.output.model_dump(),
            "audit": {
                "provider": self.audit.provider,
                "provider_version": self.audit.provider_version,
                "prompt_version": self.audit.prompt_version,
                "schema_version": self.audit.schema_version,
                "analysis_version": self.audit.analysis_version,
                "pack_id": self.audit.pack_id,
                "evidence_ids": self.audit.evidence_ids,
                "project_token": self.audit.project_token,
                "audible_token": self.audit.audible_token,
                "target_token": self.audit.target_token,
                "output_hash": self.audit.output_hash,
                "raw_hash": self.audit.raw_hash,
                "attempts": self.audit.attempts,
                "timings": self.audit.timings,
                "request_decisions": self.audit.request_decisions,
                "issues": self.audit.issues,
                "input_stats": dict(self.audit.input_stats),
                "claim_classifications": list(self.audit.claim_classifications),
            },
        }


def reason(
    pack: EvidencePack,
    provider: ReasoningProvider,
    *,
    view: object | None = None,
    timeout_s: float = MODEL_TIMEOUT_S,
) -> ReasoningResult:
    """LLM reasons over a scoped EvidenceView. Core owns reality. Never writes to Ableton."""
    from copilot.human_eval.gate import AstraPrelockBlocked, assert_astra_allowed_for_pack

    t0 = time.perf_counter()
    try:
        assert_astra_allowed_for_pack(pack)
    except AstraPrelockBlocked as exc:
        audit = ReasoningAudit(
            provider=provider.identity,
            provider_version=provider.version,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            analysis_version=pack.analysis_version or ANALYSIS_VERSION,
            pack_id=pack.pack_id,
            evidence_ids=[item.evidence_id for item in pack.items],
            project_token=pack.project_token,
            audible_token=pack.audible_token,
            target_token=pack.target_token,
        )
        audit.issues.append({"kind": "HUMAN_LABELS_NOT_LOCKED", "message": exc.message})
        return ReasoningResult(
            accepted=False,
            failure=ReasoningFailure.INSUFFICIENT_EVIDENCE,
            diagnosis=None,
            output=None,
            audit=audit,
            musical_writes=0,
        )
    fact_problems = validate_pack_facts(pack)
    if fact_problems:
        raise ValueError(f"evidence pack is not factual: {fact_problems}")
    t_view = time.perf_counter()
    with span("evidence_view_construction", category=CAT_CPU):
        scoped = scoped_evidence(pack, view)
    view_s = time.perf_counter() - t_view
    t_ser = time.perf_counter()
    with span("evidence_view_serialization", category=CAT_CPU):
        serialized = serialize_for_prompt(scoped)
    serialize_s = time.perf_counter() - t_ser
    t_prompt = time.perf_counter()
    with span("prompt_build", category=CAT_CPU):
        prompt = build_prompt(pack, view)
    prompt_s = time.perf_counter() - t_prompt
    prompt_bytes = len(prompt.encode("utf-8"))
    audit = ReasoningAudit(
        provider=provider.identity,
        provider_version=provider.version,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        analysis_version=pack.analysis_version or ANALYSIS_VERSION,
        pack_id=pack.pack_id,
        evidence_ids=[item.evidence_id for item in pack.items],
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        target_token=pack.target_token,
        timings={
            "view_build_s": view_s,
            "serialize_s": serialize_s,
            "prompt_build_s": prompt_s,
        },
        input_stats={
            "prompt_bytes": prompt_bytes,
            "approx_input_tokens": prompt_bytes // 4,
            "view_node_count": len(scoped.get("nodes") or {}),
            "serialized_node_count": len(serialized.get("nodes") or []),
            "serialization": serialized.get("serialization") or {},
            "support": list(scoped.get("support") or []),
            "counterevidence": list(scoped.get("counterevidence") or []),
        },
    )
    raw = ""
    parsed: ReasoningOutput | None = None
    parse_error: str | None = None
    t_model = time.perf_counter()
    for attempt in range(1, SCHEMA_ATTEMPTS + 1):
        audit.attempts = attempt
        t_req = time.perf_counter()
        try:
            with span(
                "provider_request",
                category=CAT_MODEL,
                attempt=attempt,
                external_call="http",
                prompt_bytes=prompt_bytes,
            ):
                pass
            with span(
                "provider_wait",
                category=CAT_MODEL,
                attempt=attempt,
                external_call="http",
                prompt_bytes=prompt_bytes,
            ):
                raw = provider.reason(prompt, timeout_s=timeout_s)
        except ProviderError as exc:
            audit.timings["provider_request_s"] = 0.0
            audit.timings["provider_wait_s"] = time.perf_counter() - t_req
            audit.timings["model_s"] = time.perf_counter() - t_model
            audit.timings["parse_s"] = 0.0
            audit.timings["validation_s"] = 0.0
            audit.timings["postprocess_s"] = 0.0
            audit.timings["total_s"] = time.perf_counter() - t0
            audit.issues = [{"kind": exc.kind.value, "detail": exc.message}]
            return ReasoningResult(
                accepted=False,
                failure=exc.kind,
                diagnosis=None,
                output=None,
                audit=audit,
                musical_writes=0,
            )
        audit.timings["provider_request_s"] = 0.0
        audit.timings["provider_wait_s"] = time.perf_counter() - t_req
        audit.raw_hash = _sha256(raw)
        t_parse = time.perf_counter()
        with span("response_parsing", category=CAT_CPU, attempt=attempt):
            parsed, parse_error = _parse_output(raw)
        audit.timings["parse_s"] = time.perf_counter() - t_parse
        if parsed is not None:
            break
        if attempt >= SCHEMA_ATTEMPTS:
            audit.timings["model_s"] = time.perf_counter() - t_model
            audit.timings["validation_s"] = 0.0
            audit.timings["postprocess_s"] = 0.0
            audit.timings["total_s"] = time.perf_counter() - t0
            detail = parse_error or "invalid json/schema"
            if raw and not raw.strip().endswith("}"):
                detail = f"truncated output: {detail}"
            audit.issues = [
                {
                    "kind": ReasoningFailure.MODEL_OUTPUT_INVALID.value,
                    "detail": detail,
                }
            ]
            return ReasoningResult(
                accepted=False,
                failure=ReasoningFailure.MODEL_OUTPUT_INVALID,
                diagnosis=None,
                output=None,
                audit=audit,
                musical_writes=0,
            )
    model_s = time.perf_counter() - t_model
    t_val = time.perf_counter()
    assert parsed is not None
    with span("grounding", category=CAT_CPU):
        report = validate_reasoning(parsed, pack, view)
    parsed.confidence = report.capped_confidence or parsed.confidence
    audit.claim_classifications = classify_output(parsed)
    t_post = time.perf_counter()
    with span("post_processing", category=CAT_CPU):
        audit.output_hash = _sha256(parsed.model_dump_json())
        audit.request_decisions = report.request_decisions
        audit.issues = [{"kind": issue.kind.value, "detail": issue.detail} for issue in report.issues]
        components = _confidence_components(scoped, parsed, pack)
    post_s = time.perf_counter() - t_post
    audit.timings["model_s"] = model_s
    audit.timings["validation_s"] = time.perf_counter() - t_val
    audit.timings["postprocess_s"] = post_s
    audit.timings["total_s"] = time.perf_counter() - t0
    audit.input_stats["confidence_components"] = components
    if not report.accepted:
        failure = report.primary_failure or ReasoningFailure.LLM_GROUNDING_VIOLATION
        return ReasoningResult(
            accepted=False,
            failure=failure,
            diagnosis=None,
            output=parsed,
            audit=audit,
            musical_writes=0,
        )
    diagnosis = to_music_diagnosis(parsed, pack, audit, scoped=scoped)
    return ReasoningResult(
        accepted=True,
        failure=None,
        diagnosis=diagnosis,
        output=parsed,
        audit=audit,
        musical_writes=0,
    )


def to_music_diagnosis(
    output: ReasoningOutput,
    pack: EvidencePack,
    audit: ReasoningAudit,
    *,
    scoped: dict | None = None,
) -> MusicDiagnosis:
    hypotheses = [
        Hypothesis(
            statement=item.claim,
            claim=item.claim,
            confidence=item.confidence,
            status=EvidenceStatus.INFERRED,
            evidence_refs=item.evidence_refs,
            reasoning_summary=item.reasoning_summary,
            alternatives_considered=item.alternatives_considered,
            contradicting_evidence_refs=item.contradicting_evidence_refs,
            entity_refs=item.entity_refs,
            missing_evidence=list(item.missing_evidence),
            limitations=list(item.limitations),
            support_status=item.status or output.status,
        )
        for item in output.hypotheses
    ]
    primary = hypotheses[0] if hypotheses else None
    finding_status = (
        EvidenceStatus.UNRESOLVED
        if output.status
        in {DiagnosisStatus.INSUFFICIENT_EVIDENCE, DiagnosisStatus.DIAGNOSIS_UNSTABLE}
        else EvidenceStatus.INFERRED
    )
    findings = [
        Finding(
            type=output.category,
            status=finding_status,
            summary=output.summary,
            evidence_refs=output.evidence_refs,
        )
    ]
    if output.status is DiagnosisStatus.NO_ACTION_REQUIRED and output.category is not FindingType.NO_ACTION_REQUIRED:
        findings.append(
            Finding(
                type=FindingType.NO_ACTION_REQUIRED,
                status=EvidenceStatus.INFERRED,
                summary="No change recommended despite measurable phenomenon.",
                evidence_refs=output.evidence_refs,
            )
        )
    actions = [_to_candidate(item, output.confidence) for item in output.candidate_actions]
    strategies = [item.model_dump() for item in output.candidate_strategies]
    if not strategies:
        strategies = [
            {
                "strategy": item.reason,
                "reason": item.expected_effect,
                "evidence_refs": item.evidence_refs,
                "entity_refs": item.entity_refs,
            }
            for item in output.candidate_actions
            if item.action_type.value != "NO_CHANGE" or output.status is DiagnosisStatus.NO_ACTION_REQUIRED
        ]
    targets = {
        entity.role: entity.name
        for entity in pack.entities
        if entity.role in {"kick", "bass"}
    }
    components = dict(audit.input_stats.get("confidence_components") or {})
    if not components:
        components = _confidence_components(scoped or scoped_evidence(pack), output, pack)
    pack_limit_codes = [item.code for item in pack.limitations]
    view_limit_codes = limitation_codes_from_scoped(pack, scoped)
    limitations = list(dict.fromkeys(list(output.limitations) + pack_limit_codes + view_limit_codes))
    question = output.question or output.category.value
    scope = output.scope or pack.region
    project_identity = pack.project_token
    if scoped:
        project_identity = str(scoped.get("project_identity") or project_identity)
    return MusicDiagnosis(
        diagnosis_id=f"reason_{uuid4().hex[:12]}",
        region=pack.region,
        targets=targets or {"kick": "Kick", "bass": "Bass"},
        findings=findings,
        hypotheses=hypotheses,
        primary_hypothesis=primary,
        secondary_hypotheses=hypotheses[1:],
        confidence=output.confidence,
        candidate_actions=actions,
        no_change_is_valid=True,
        limitations=limitations,
        structured_evidence={
            "pack_id": pack.pack_id,
            "evidence_ids": audit.evidence_ids,
            "support": list((scoped or {}).get("support") or []),
            "counterevidence": list((scoped or {}).get("counterevidence") or []),
        },
        user_facing=_user_facing(output),
        phase="REASONING",
        timings=dict(audit.timings),
        status=output.status,
        contradicting_evidence_refs=output.contradicting_evidence_refs,
        requested_evidence=output.requested_evidence,
        acceptance="ACCEPTED",
        reasoning_audit={
            "provider": audit.provider,
            "provider_version": audit.provider_version,
            "prompt_version": audit.prompt_version,
            "schema_version": audit.schema_version,
            "output_hash": audit.output_hash,
            "request_decisions": audit.request_decisions,
            "input_stats": dict(audit.input_stats),
            "claim_classifications": list(audit.claim_classifications),
        },
        question=question,
        scope=scope,
        project_identity=project_identity,
        candidate_strategies=strategies,
        confidence_components={**components, "combined": None},
    )


def semantic_fingerprint(output: ReasoningOutput) -> tuple:
    """Status, hypothesis support set, critical refs, next-evidence kinds. Not prose."""
    hypo_refs = tuple(
        sorted({ref for item in output.hypotheses for ref in item.evidence_refs})
    )
    hypo_counter = tuple(
        sorted({ref for item in output.hypotheses for ref in item.contradicting_evidence_refs})
    )
    next_kinds = tuple(sorted(item.request_kind.value for item in output.requested_evidence))
    return (
        output.status.value,
        output.category.value,
        hypo_refs,
        hypo_counter,
        tuple(sorted(output.contradicting_evidence_refs)),
        next_kinds,
    )


def _confidence_components(scoped: dict, output: ReasoningOutput, pack: EvidencePack) -> dict:
    qualities = []
    for node in (scoped.get("nodes") or {}).values():
        if isinstance(node, dict) and node.get("quality"):
            qualities.append(str(node["quality"]))
    measurement = None
    if any(item in {"LIMITED", "WARNING", "UNKNOWN"} for item in qualities):
        measurement = "LIMITED"
    elif qualities:
        measurement = "OK"
    fusion_vals = [
        str(row.get("status"))
        for row in (scoped.get("fusions") or [])
        if isinstance(row, dict)
    ]
    agreement = None
    if "CONTRADICT" in fusion_vals:
        agreement = "CONTRADICT"
    elif "PARTIALLY_AGREE" in fusion_vals:
        agreement = "PARTIALLY_AGREE"
    elif "NOT_COMPARABLE" in fusion_vals:
        agreement = "NOT_COMPARABLE"
    elif "AGREE" in fusion_vals:
        agreement = "AGREE"
    missing = [item for hypo in output.hypotheses for item in hypo.missing_evidence]
    for request in output.requested_evidence:
        missing.append(request.request_kind.value)
    return {
        "measurement_quality": measurement,
        "provider_confidence": None,
        "source_reliability": "evidence_view",
        "cross_source_agreement": agreement,
        "reasoning_confidence": output.confidence.value,
        "contradictions": list(output.contradicting_evidence_refs),
        "missing_evidence": list(dict.fromkeys(missing)),
        "limitations": limitation_codes_from_scoped(pack, scoped),
        "combined": None,
    }


def _to_candidate(item: ReasoningCandidate, confidence: Confidence) -> CandidateAction:
    return CandidateAction(
        action_type=item.action_type.value,
        target=item.target or "mix",
        rationale=item.reason,
        expected_effect=item.expected_effect,
        risk=item.risk,
        confidence=confidence,
        evidence_refs=item.evidence_refs,
        entity_refs=item.entity_refs,
    )


def _user_facing(output: ReasoningOutput) -> str:
    lines = [output.summary, f"Estado: {output.status.value}.", f"Confidence: {output.confidence.value}."]
    if output.requested_evidence:
        kinds = ", ".join(item.request_kind.value for item in output.requested_evidence)
        lines.append(f"Evidencia adicional pedida: {kinds}.")
    lines.append("No hice cambios.")
    return "\n\n".join(lines)


def _parse_output(raw: str) -> tuple[ReasoningOutput | None, str | None]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, str(exc)
    try:
        return ReasoningOutput.model_validate(payload), None
    except ValidationError as exc:
        return None, str(exc)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
