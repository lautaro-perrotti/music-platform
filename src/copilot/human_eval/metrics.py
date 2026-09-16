from __future__ import annotations

from collections import Counter

from copilot.human_eval.schema import CaseStatus
from copilot.human_eval.store import EvalStore


def compute_metrics(run_id: str, *, store: EvalStore | None = None) -> dict:
    """Internal metrics. Not served to the labeling UI."""
    store = store or EvalStore()
    cases = store.load_cases(run_id)
    labeled = [
        case
        for case in cases
        if case.status in {CaseStatus.COMPLETED, CaseStatus.LOCKED} and case.response
    ]
    times = []
    replays = 0
    feels = Counter()
    change = Counter()
    confidence = Counter()
    for case in labeled:
        resp = case.response
        if resp is None:
            continue
        if resp.listen_ms:
            times.append(resp.listen_ms)
        replays += int(resp.replays or 0)
        if resp.overall_feel:
            feels[resp.overall_feel.value] += 1
        if resp.would_change:
            change[resp.would_change.value] += 1
        if resp.confidence:
            confidence[resp.confidence.value] += 1
    avg_ms = (sum(times) / len(times)) if times else 0.0
    return {
        "cases_completed": len(labeled),
        "cases_skipped": sum(1 for case in cases if case.status == CaseStatus.SKIPPED),
        "avg_time_per_case_ms": avg_ms,
        "replays_per_case": (replays / len(labeled)) if labeled else 0.0,
        "confidence_distribution": dict(confidence),
        "problem_no_problem_distribution": dict(change),
        "overall_feel_distribution": dict(feels),
    }
