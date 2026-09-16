from __future__ import annotations

from copilot.reasoning.errors import ProviderError, ReasoningFailure
from copilot.human_eval.store import EvalStore
from copilot.schemas.evidence import EvidencePack


class AstraPrelockBlocked(ProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(ReasoningFailure.INSUFFICIENT_EVIDENCE, message)


def assert_run_locked(run_id: str, *, store: EvalStore | None = None) -> None:
    store = store or EvalStore()
    run = store.load_run(run_id)
    if run is None:
        raise AstraPrelockBlocked(
            f"HUMAN_LABELS_NOT_LOCKED: eval run {run_id} missing"
        )
    if not run.comparison_requires_human_gt:
        return
    if not store.all_required_locked(run_id):
        raise AstraPrelockBlocked(
            f"HUMAN_LABELS_NOT_LOCKED: lock completed human labels for {run_id} before Astra"
        )


def assert_astra_allowed_for_pack(
    pack: EvidencePack, *, store: EvalStore | None = None
) -> None:
    store = store or EvalStore()
    run = store.find_run_by_project_token(pack.project_token)
    if run is None:
        return
    assert_run_locked(run.evaluation_run_id, store=store)
