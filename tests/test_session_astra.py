from __future__ import annotations

from pathlib import Path

from copilot.human_eval.gate import AstraPrelockBlocked, assert_run_locked
from copilot.reasoning.session_astra import load_frozen_packs
from copilot.schemas.evidence import EvidencePack


def test_frozen_session_packs_validate() -> None:
    packs = load_frozen_packs(Path("logs"), "session_run1")
    assert [pack.pack_id for pack in packs] == ["REGION_A", "REGION_B", "REGION_C"]
    for pack in packs:
        assert isinstance(pack, EvidencePack)
        assert pack.project_token
        assert pack.items


def test_real_session_human_labels_are_locked() -> None:
    assert_run_locked("session_run1")


def test_unlocked_run_blocks_astra(tmp_path) -> None:
    from copilot.human_eval.schema import EvalRun
    from copilot.human_eval.store import EvalStore, now_iso

    store = EvalStore(tmp_path)
    stamp = now_iso()
    store.save_run(
        EvalRun(
            evaluation_run_id="session_run1",
            project_token="token-unlocked",
            comparison_requires_human_gt=True,
            created_at=stamp,
            updated_at=stamp,
        )
    )
    try:
        assert_run_locked("session_run1", store=store)
        raised = False
    except AstraPrelockBlocked:
        raised = True
    assert raised
