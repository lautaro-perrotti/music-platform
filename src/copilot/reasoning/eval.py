from __future__ import annotations

from dataclasses import dataclass, field

from copilot.reasoning.errors import ReasoningFailure
from copilot.reasoning.fixtures import (
    ALL_PACKS,
    EXPECTED_ACTION_FAMILY,
    EXPECTED_CATEGORY,
    EXPECTED_STATUS,
    SCRIPTED_VALID,
    output_hallucination_trap,
)
from copilot.reasoning.human_labels import seed_cases
from copilot.reasoning.pipeline import ReasoningResult, reason
from copilot.reasoning.provider import ReasoningProvider, ScriptedProvider, configured_http_provider
from copilot.reasoning.schema import PROMPT_VERSION, SCHEMA_VERSION
from copilot.schemas.diagnosis import CandidateActionType, DiagnosisStatus

STABILITY_REPEATS = 3
CORE_FIXTURES = (
    "CLEAR_NO_ACTION",
    "CLEAR_TEMPORAL",
    "CLEAR_SPECTRAL",
    "AMBIGUOUS",
    "CONTRADICTORY",
    "HALLUCINATION_TRAP",
)


@dataclass
class EvalCaseResult:
    fixture: str
    accepted: bool
    failure: str | None
    category: str | None
    status: str | None
    action_family: list[str]
    evidence_refs: list[str]
    musical_writes: int
    repeats: list[str] = field(default_factory=list)


def scripted_provider() -> ScriptedProvider:
    return ScriptedProvider({name: factory() for name, factory in SCRIPTED_VALID.items()})


def run_eval(
    provider: ReasoningProvider | None = None,
    *,
    repeats: int = STABILITY_REPEATS,
    include_real_model: bool = False,
) -> dict:
    used = provider or scripted_provider()
    cases: list[EvalCaseResult] = []
    for name in CORE_FIXTURES:
        pack = ALL_PACKS[name]()
        run_results: list[ReasoningResult] = []
        for _ in range(repeats):
            run_results.append(reason(pack, used))
        first = run_results[0]
        family = []
        if first.output is not None:
            family = [item.action_type.value for item in first.output.candidate_actions]
        cases.append(
            EvalCaseResult(
                fixture=name,
                accepted=first.accepted,
                failure=None if first.failure is None else first.failure.value,
                category=None if first.output is None else first.output.category.value,
                status=None if first.output is None else first.output.status.value,
                action_family=family,
                evidence_refs=[] if first.output is None else list(first.output.evidence_refs),
                musical_writes=sum(item.musical_writes for item in run_results),
                repeats=[
                    (item.output.category.value if item.output else (item.failure.value if item.failure else "NONE"))
                    for item in run_results
                ],
            )
        )
    metrics = _metrics(cases)
    adversarial = _adversarial(used)
    real_model = _real_model_smoke() if include_real_model else {"status": "NOT_RUN"}
    return {
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "provider": used.identity,
        "provider_version": used.version,
        "musical_writes": sum(item.musical_writes for item in cases),
        "cases": [item.__dict__ for item in cases],
        "metrics": metrics,
        "adversarial": adversarial,
        "human_label_schema": [item.model_dump() for item in seed_cases()],
        "real_model": real_model,
        "scalar_score": None,
        "note": "Do not reduce quality to one scalar score.",
    }


def _metrics(cases: list[EvalCaseResult]) -> dict:
    n = len(cases) or 1
    grounding = sum(
        1
        for item in cases
        if item.failure
        in {
            ReasoningFailure.LLM_GROUNDING_VIOLATION.value,
            ReasoningFailure.UNKNOWN_EVIDENCE_REF.value,
            ReasoningFailure.UNKNOWN_ENTITY_REF.value,
        }
    )
    unsupported_number = sum(
        1
        for item in cases
        if item.failure == ReasoningFailure.LLM_GROUNDING_VIOLATION.value
    )
    unsupported_entity = sum(
        1
        for item in cases
        if item.failure in {
            ReasoningFailure.UNKNOWN_ENTITY_REF.value,
            ReasoningFailure.AMBIGUOUS_ENTITY_REFERENCE.value,
        }
    )
    correct_abstention = 0
    false_positive = 0
    false_negative = 0
    no_action = 0
    request_useful = 0
    stable = 0
    for item in cases:
        expected_status = EXPECTED_STATUS[item.fixture]
        expected_cat = EXPECTED_CATEGORY[item.fixture]
        expected_family = {member.value for member in EXPECTED_ACTION_FAMILY[item.fixture]}
        if item.status == DiagnosisStatus.NO_ACTION_REQUIRED.value:
            no_action += 1
        if item.fixture in {"AMBIGUOUS", "HALLUCINATION_TRAP"} and item.status == expected_status.value:
            correct_abstention += 1
        if expected_status is DiagnosisStatus.NO_ACTION_REQUIRED and item.status not in {
            DiagnosisStatus.NO_ACTION_REQUIRED.value,
            DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
        }:
            false_positive += 1
        if expected_status in {DiagnosisStatus.SUPPORTED, DiagnosisStatus.WEAKLY_SUPPORTED} and item.status in {
            DiagnosisStatus.NO_ACTION_REQUIRED.value,
        }:
            false_negative += 1
        if item.fixture == "AMBIGUOUS" and item.accepted:
            request_useful += 1
        if item.accepted and item.category == expected_cat.value and item.status == expected_status.value:
            if expected_family & set(item.action_family) or not expected_family:
                if len(set(item.repeats)) == 1:
                    stable += 1
        del expected_family
    return {
        "grounding_violation_rate": grounding / n,
        "unsupported_number_rate": unsupported_number / n,
        "unsupported_entity_rate": unsupported_entity / n,
        "correct_abstention": correct_abstention,
        "false_positive_diagnosis": false_positive,
        "false_negative_diagnosis": false_negative,
        "NO_ACTION_REQUIRED_usage": no_action,
        "diagnosis_stability": stable / n,
        "evidence_request_usefulness": request_useful,
        "accepted_core_fixtures": sum(1 for item in cases if item.accepted),
        "n": n,
    }


def _adversarial(provider: ReasoningProvider) -> dict:
    trap_pack = ALL_PACKS["HALLUCINATION_TRAP"]()
    trap_provider = ScriptedProvider({"HALLUCINATION_TRAP": output_hallucination_trap()})
    trapped = reason(trap_pack, trap_provider)
    honest = reason(trap_pack, provider)
    return {
        "injected_hallucination_rejected": trapped.accepted is False
        and trapped.failure
        in {
            ReasoningFailure.LLM_GROUNDING_VIOLATION,
            ReasoningFailure.UNKNOWN_EVIDENCE_REF,
            ReasoningFailure.UNKNOWN_ENTITY_REF,
            ReasoningFailure.UNSUPPORTED_PRECISION,
        },
        "injected_failure": None if trapped.failure is None else trapped.failure.value,
        "honest_on_trap_accepted": honest.accepted,
        "honest_status": None if honest.output is None else honest.output.status.value,
        "issues": trapped.audit.issues,
    }


def _real_model_smoke() -> dict:
    provider = configured_http_provider()
    if provider is None:
        return {
            "status": "NOT_RUN",
            "reason": "No COPILOT_REASONING_API_KEY or OPENAI_API_KEY",
        }
    pack = ALL_PACKS["HALLUCINATION_TRAP"]()
    try:
        result = reason(pack, provider, timeout_s=30.0)
    except Exception as exc:  # noqa: BLE001 — smoke must fail closed
        return {"status": "FAILED", "error": str(exc), "provider": provider.identity}
    invented = False
    text = ""
    if result.output is not None:
        text = result.output.model_dump_json().lower()
        invented = "serum" in text or "63 hz" in text or "63hz" in text
    return {
        "status": "RAN",
        "accepted": result.accepted,
        "failure": None if result.failure is None else result.failure.value,
        "invented_unsupported_facts": invented,
        "provider": provider.identity,
        "provider_version": provider.version,
        "output_hash": result.audit.output_hash,
        "musical_writes": result.musical_writes,
    }


def action_family(types: list[CandidateActionType | str]) -> set[str]:
    out: set[str] = set()
    for item in types:
        out.add(item.value if isinstance(item, CandidateActionType) else item)
    return out


def semantic_signature(result: ReasoningResult) -> tuple:
    if result.output is None:
        return (result.failure.value if result.failure else None,)
    family = tuple(sorted(item.action_type.value for item in result.output.candidate_actions))
    return (
        result.output.category.value,
        result.output.status.value,
        tuple(sorted(result.output.evidence_refs)),
        family,
    )
