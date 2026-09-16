from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
from uuid import uuid4

from pydantic import ValidationError

from copilot.reasoning.errors import ProviderError, ReasoningFailure
from copilot.reasoning.grounding import cap_confidence, validate_pack_facts, validate_reasoning
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
            },
        }


def reason(
    pack: EvidencePack,
    provider: ReasoningProvider,
    *,
    timeout_s: float = MODEL_TIMEOUT_S,
) -> ReasoningResult:
    """LLM reasons. Core owns reality. Never writes to Ableton."""
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
    t_prompt = time.perf_counter()
    prompt = build_prompt(pack)
    prompt_s = time.perf_counter() - t_prompt
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
        timings={"prompt_build_s": prompt_s},
    )
    raw = ""
    parsed: ReasoningOutput | None = None
    parse_error: str | None = None
    t_model = time.perf_counter()
    for attempt in range(1, SCHEMA_ATTEMPTS + 1):
        audit.attempts = attempt
        try:
            raw = provider.reason(prompt, timeout_s=timeout_s)
        except ProviderError as exc:
            audit.timings["model_s"] = time.perf_counter() - t_model
            audit.timings["validation_s"] = 0.0
            audit.timings["total_s"] = time.perf_counter() - t0
            audit.issues = [{"kind": exc.kind.value, "detail": exc.message}]
            return ReasoningResult(
                accepted=False,
                failure=exc.kind,
                diagnosis=None,
                output=None,
                audit=audit,
            )
        audit.raw_hash = _sha256(raw)
        parsed, parse_error = _parse_output(raw)
        if parsed is not None:
            break
        if attempt >= SCHEMA_ATTEMPTS:
            audit.timings["model_s"] = time.perf_counter() - t_model
            audit.timings["validation_s"] = 0.0
            audit.timings["total_s"] = time.perf_counter() - t0
            audit.issues = [
                {
                    "kind": ReasoningFailure.MODEL_OUTPUT_INVALID.value,
                    "detail": parse_error or "invalid json/schema",
                }
            ]
            return ReasoningResult(
                accepted=False,
                failure=ReasoningFailure.MODEL_OUTPUT_INVALID,
                diagnosis=None,
                output=None,
                audit=audit,
            )
    model_s = time.perf_counter() - t_model
    t_val = time.perf_counter()
    assert parsed is not None
    report = validate_reasoning(parsed, pack)
    parsed.confidence = report.capped_confidence or parsed.confidence
    audit.output_hash = _sha256(parsed.model_dump_json())
    audit.request_decisions = report.request_decisions
    audit.issues = [{"kind": issue.kind.value, "detail": issue.detail} for issue in report.issues]
    audit.timings["model_s"] = model_s
    audit.timings["validation_s"] = time.perf_counter() - t_val
    audit.timings["total_s"] = time.perf_counter() - t0
    if not report.accepted:
        failure = report.primary_failure or ReasoningFailure.LLM_GROUNDING_VIOLATION
        return ReasoningResult(
            accepted=False,
            failure=failure,
            diagnosis=None,
            output=parsed,
            audit=audit,
        )
    diagnosis = to_music_diagnosis(parsed, pack, audit)
    return ReasoningResult(
        accepted=True,
        failure=None,
        diagnosis=diagnosis,
        output=parsed,
        audit=audit,
    )


def to_music_diagnosis(
    output: ReasoningOutput,
    pack: EvidencePack,
    audit: ReasoningAudit,
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
    targets = {
        entity.role: entity.name
        for entity in pack.entities
        if entity.role in {"kick", "bass"}
    }
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
        limitations=list(output.limitations) + [item.code for item in pack.limitations],
        structured_evidence={"pack_id": pack.pack_id, "evidence_ids": audit.evidence_ids},
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
        },
    )


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
