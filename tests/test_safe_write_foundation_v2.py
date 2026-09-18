"""SAFE_WRITE_FOUNDATION_V2 — mock Live only. No Groove Rider analyze."""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot.agent.journal import DurableJournal
from copilot.agent.recovery import RecoveryStatus, classify_journal
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.audio.m4l_control_contract_v1 import APPLY_VOCABULARY, control_contract
from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.mutation_protocol import KIND_TEMPORARY_CAPTURE_HOST
from copilot.daw.write import WriteInDoubt
from copilot.musicplan.execute import execute_controlled_write_loop
from copilot.runtime.safe_write import (
    SafeWriteExecutor,
    WriteCancellation,
    action_is_certified,
    build_safe_write_executor,
    certified_production_actions,
    isolated_rollback_permitted,
    plan_compound_rollback,
    volume_intent,
)
from copilot.schemas.musicplan import ActionType
from copilot.schemas.safe_write import (
    CERTIFIED_PRODUCTION_ACTION,
    KIND_PRODUCTION_MUSICAL,
    ApplyDecision,
    MutationExecution,
    MutationFailure,
    MutationPhase,
    MutationRollback,
    MutationTarget,
    RollbackReversibility,
)
from copilot.schemas.transaction import TransactionStatus


def _executor(tmp_path: Path, *, volume: float = 0.85, name: str = "Coffee Leaf"):
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track(name)
    daw.tracks[0]["mixer"]["volume"] = volume
    executor = build_safe_write_executor(
        daw, journal_path=tmp_path / "safe_write.jsonl", persist_dir=tmp_path
    )
    session = executor.tools.get_session_snapshot()
    track = session.track_by_name(name)
    assert track is not None
    return daw, executor, session, track


def test_generic_mutation_contract_and_keep(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path, volume=0.85)
    intent = volume_intent(
        session=session, track=track, requested_after=0.70, plan_id="sw_keep"
    )
    assert intent.kind == KIND_PRODUCTION_MUSICAL
    assert intent.kind != KIND_TEMPORARY_CAPTURE_HOST
    assert all(step.certified for step in intent.executions)
    result = executor.run(intent)
    assert result.failure is None
    assert result.ok is True
    assert result.decision is ApplyDecision.KEEP
    assert MutationPhase.PREPARED.value in result.lifecycle
    assert MutationPhase.READBACK.value in result.lifecycle
    assert MutationPhase.VERIFY.value in result.lifecycle
    assert MutationPhase.KEEP.value in result.lifecycle
    assert result.readbacks[0].matched is True
    assert result.musical_writes == 1
    assert result.open_transaction is False
    assert Path(result.prestate_path).is_file()
    live = daw.snapshot().track_by_name("Coffee Leaf")
    assert live is not None
    assert live.mixer.volume == pytest.approx(0.70)


def test_durable_prestate_exists_before_write(tmp_path: Path) -> None:
    _daw, executor, session, track = _executor(tmp_path)
    intent = volume_intent(
        session=session, track=track, requested_after=0.60, plan_id="sw_pre"
    )
    prepared = executor.run(intent, stop_at=MutationPhase.PREPARED)
    assert prepared.phase is MutationPhase.PREPARED
    assert prepared.musical_writes == 0
    assert Path(prepared.prestate_path).is_file()
    payload = Path(prepared.prestate_path).read_text(encoding="utf-8")
    assert "rollback_plan" in payload
    assert executor.tools.get_session_snapshot().tracks[0].mixer.volume == pytest.approx(
        0.85
    )
    records = executor.journal.read_all()
    assert any(row.get("status") == TransactionStatus.PREPARED.value for row in records)
    assert not any(row.get("status") == TransactionStatus.SENT.value for row in records)


def test_readback_mismatch_is_fail_closed(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path, volume=0.85)
    original = daw.set_mixer_volume
    calls = {"n": 0}

    def lie(index: int, volume: float):
        calls["n"] += 1
        result = original(index, volume)
        if calls["n"] == 1:
            daw.tracks[index]["mixer"]["volume"] = 0.11
            return {"track_index": index, "volume": 0.11}
        return result

    daw.set_mixer_volume = lie  # type: ignore[method-assign]
    intent = volume_intent(session=session, track=track, requested_after=0.70)
    result = executor.run(intent)
    assert result.failure is MutationFailure.READBACK_MISMATCH
    assert result.ok is False
    restored = daw.snapshot().tracks[0].mixer.volume
    assert restored == pytest.approx(0.85)


def test_verification_failed_rolls_back(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path, volume=0.85, name="Coffee Leaf")
    original = daw.set_mixer_volume

    def also_mute(index: int, volume: float):
        result = original(index, volume)
        daw.tracks[index]["mixer"]["mute"] = True
        return result

    daw.set_mixer_volume = also_mute  # type: ignore[method-assign]
    intent = volume_intent(session=session, track=track, requested_after=0.70)
    result = executor.run(intent)
    assert result.failure is MutationFailure.VERIFICATION_FAILED
    assert result.verification is not None
    assert result.verification.unexpected_mutations


def test_in_doubt_never_failed_never_retried(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path, volume=0.85)
    calls = {"n": 0}

    def timeout_unknown(index: int, volume: float):
        calls["n"] += 1
        raise WriteInDoubt("set_mixer_volume", "cmd_timeout")

    daw.set_mixer_volume = timeout_unknown  # type: ignore[method-assign]
    intent = volume_intent(session=session, track=track, requested_after=0.70)
    result = executor.run(intent)
    assert result.failure is MutationFailure.IN_DOUBT
    assert result.decision is ApplyDecision.IN_DOUBT
    assert result.journal_terminal_state == TransactionStatus.IN_DOUBT.value
    assert result.journal_terminal_state != TransactionStatus.FAILED.value
    assert calls["n"] == 1
    assert daw.snapshot().tracks[0].mixer.volume == pytest.approx(0.85)


def test_timeout_after_write_reconciles_when_applied(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path, volume=0.85)
    original = daw.set_mixer_volume

    def timeout_after(index: int, volume: float):
        result = original(index, volume)
        raise WriteInDoubt("set_mixer_volume", "cmd_timeout_after")

    daw.set_mixer_volume = timeout_after  # type: ignore[method-assign]
    intent = volume_intent(session=session, track=track, requested_after=0.66)
    result = executor.run(intent)
    assert result.failure is None
    assert result.ok is True
    assert result.decision is ApplyDecision.KEEP
    assert daw.snapshot().tracks[0].mixer.volume == pytest.approx(0.66)


def test_partial_failure_rolls_back_applied_step(tmp_path: Path) -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track("A")
    daw.create_midi_track("B")
    daw.tracks[0]["mixer"]["volume"] = 0.80
    daw.tracks[1]["mixer"]["volume"] = 0.80
    executor = build_safe_write_executor(
        daw, journal_path=tmp_path / "journal.jsonl", persist_dir=tmp_path
    )
    session = executor.tools.get_session_snapshot()
    first = volume_intent(session=session, track=session.tracks[0], requested_after=0.50)
    second = volume_intent(session=session, track=session.tracks[1], requested_after=0.40)
    second.executions[0] = second.executions[0].model_copy(update={"action_id": "b"})
    second.targets[0] = second.targets[0].model_copy(update={"action_id": "b"})
    first.executions[0] = first.executions[0].model_copy(update={"action_id": "a"})
    first.targets[0] = first.targets[0].model_copy(update={"action_id": "a"})
    intent = first.model_copy(
        update={
            "plan_id": "sw_partial",
            "targets": first.targets + second.targets,
            "executions": first.executions + second.executions,
        }
    )
    original = daw.set_mixer_volume

    def fail_second(index: int, volume: float):
        if index == 1 and abs(float(volume) - 0.40) < 1e-9:
            raise RuntimeError("injected second-step failure")
        return original(index, volume)

    daw.set_mixer_volume = fail_second  # type: ignore[method-assign]
    result = executor.run(intent)
    assert result.failure is MutationFailure.PARTIAL_FAILURE
    assert result.applied_action_ids == ["a"]
    live = daw.snapshot()
    assert live.tracks[0].mixer.volume == pytest.approx(0.80)
    assert live.tracks[1].mixer.volume == pytest.approx(0.80)


def test_compound_rollback_foundation_dependency_order() -> None:
    independent = [
        MutationExecution(
            action_id="a",
            action_type=CERTIFIED_PRODUCTION_ACTION,
            operation="set_mixer_volume",
            rollback=MutationRollback(
                inverse_operation="set_mixer_volume",
                reversibility=RollbackReversibility.INDEPENDENT,
            ),
        ),
        MutationExecution(
            action_id="b",
            action_type=CERTIFIED_PRODUCTION_ACTION,
            operation="set_mixer_volume",
            rollback=MutationRollback(
                inverse_operation="set_mixer_volume",
                reversibility=RollbackReversibility.DEPENDENT,
                depends_on=["a"],
            ),
        ),
    ]
    plan = plan_compound_rollback(independent)
    assert plan["undo_action_ids"][0] == "b"
    assert plan["undo_action_ids"][-1] == "a"
    assert plan["assumes_independent_reversibility"] is True

    grouped = [
        MutationExecution(
            action_id="create",
            action_type="FUTURE_UNCERTIFIED",
            operation="future",
            rollback=MutationRollback(
                inverse_operation="future_inverse",
                reversibility=RollbackReversibility.NOT_INDEPENDENTLY_REVERSIBLE,
            ),
        ),
        MutationExecution(
            action_id="shape",
            action_type="FUTURE_UNCERTIFIED",
            operation="future",
            rollback=MutationRollback(
                inverse_operation="future_inverse",
                reversibility=RollbackReversibility.DEPENDENT,
                depends_on=["create"],
            ),
        ),
    ]
    grouped_plan = plan_compound_rollback(grouped)
    assert grouped_plan["undo_action_ids"] == ["shape", "create"]
    assert isolated_rollback_permitted(grouped_plan, "create") is False
    assert isolated_rollback_permitted(grouped_plan, "shape") is True


def test_cancel_before_write(tmp_path: Path) -> None:
    _daw, executor, session, track = _executor(tmp_path)
    intent = volume_intent(session=session, track=track, requested_after=0.70)
    cancellation = WriteCancellation()
    cancellation.cancel("user_abort")
    result = executor.run(intent, cancellation=cancellation)
    assert result.failure is MutationFailure.CANCELLED
    assert result.musical_writes == 0
    assert result.decision is ApplyDecision.ABSTAIN
    assert executor.tools.get_session_snapshot().tracks[0].mixer.volume == pytest.approx(
        0.85
    )


def test_cancel_after_dispatch_rolls_back(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path)
    intent = volume_intent(session=session, track=track, requested_after=0.70)
    cancellation = WriteCancellation()
    original = daw.set_mixer_volume

    def cancel_during(index: int, volume: float):
        result = original(index, volume)
        cancellation.cancel("after_dispatch")
        return result

    daw.set_mixer_volume = cancel_during  # type: ignore[method-assign]
    result = executor.run(intent, cancellation=cancellation)
    assert result.failure is MutationFailure.CANCELLED
    assert result.decision is ApplyDecision.ROLLBACK
    assert daw.snapshot().tracks[0].mixer.volume == pytest.approx(0.85)


def test_disconnect_after_dispatch_is_in_doubt(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path)
    original = daw.set_mixer_volume

    def disconnect(index: int, volume: float):
        daw.disconnect()
        return original(index, volume)

    daw.set_mixer_volume = disconnect  # type: ignore[method-assign]
    intent = volume_intent(session=session, track=track, requested_after=0.70)
    result = executor.run(intent)
    assert result.failure is MutationFailure.IN_DOUBT
    assert result.journal_terminal_state == TransactionStatus.IN_DOUBT.value


def test_process_interruption_and_crash_recovery(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path)
    intent = volume_intent(
        session=session, track=track, requested_after=0.70, plan_id="sw_crash"
    )
    prepared = executor.run(intent, stop_at=MutationPhase.PREPARED)
    assert prepared.phase is MutationPhase.PREPARED
    journal_path = tmp_path / "safe_write.jsonl"
    crashed = SafeWriteExecutor(
        AgentTools(daw, TransactionManager(daw, journal=DurableJournal(journal_path))),
        journal=DurableJournal(journal_path),
        persist_dir=tmp_path,
    )
    reports = crashed.recover()
    assert reports
    assert reports[-1]["recovery"] == RecoveryStatus.RECOVERY_REQUIRED.value
    assert reports[-1]["last_status"] == TransactionStatus.PREPARED.value
    assert daw.snapshot().tracks[0].mixer.volume == pytest.approx(0.85)
    assert crashed.transactions._open is None


def test_crash_recovery_sent_is_in_doubt(tmp_path: Path) -> None:
    journal = DurableJournal(tmp_path / "sent.jsonl")
    journal.append(
        {
            "transaction_id": "txn_crash",
            "kind": "write",
            "status": TransactionStatus.SENT.value,
        }
    )
    reports = classify_journal(journal.read_all())
    assert reports[0]["recovery"] == RecoveryStatus.IN_DOUBT.value
    assert reports[0]["last_status"] != TransactionStatus.FAILED.value


def test_rollback_interruption(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path)
    original = daw.set_mixer_volume
    calls = {"n": 0}

    def interrupt_rollback(index: int, volume: float):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("rollback interrupted")
        return original(index, volume)

    daw.set_mixer_volume = interrupt_rollback  # type: ignore[method-assign]
    intent = volume_intent(
        session=session,
        track=track,
        requested_after=0.70,
        decision_after_verify=ApplyDecision.ROLLBACK,
    )
    result = executor.run(intent)
    assert result.failure is MutationFailure.ROLLBACK_FAILED
    assert result.ok is False


def test_supersession(tmp_path: Path) -> None:
    _daw, executor, session, track = _executor(tmp_path)
    first = volume_intent(
        session=session, track=track, requested_after=0.70, plan_id="sw_old"
    )
    prepared = executor.run(first, stop_at=MutationPhase.PREPARED)
    assert prepared.phase is MutationPhase.PREPARED
    second_session = executor.tools.get_session_snapshot()
    second_track = second_session.track_by_name("Coffee Leaf")
    assert second_track is not None
    second = volume_intent(
        session=second_session,
        track=second_track,
        requested_after=0.55,
        plan_id="sw_new",
    )
    result = executor.run(second)
    assert any(
        txn.status is TransactionStatus.SUPERSEDED for txn in executor.transactions.history
    )
    assert result.ok is True
    assert result.plan_id == "sw_new"
    assert result.decision is ApplyDecision.KEEP


def test_fail_closed_identity_and_preconditions(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path, volume=0.85)
    missing = volume_intent(session=session, track=track, requested_after=0.70)
    missing.targets[0].ref["name"] = "Ghost"
    missing.targets[0].ref["role"] = "audio"
    missing.targets[0].ref["device_names"] = ["Nope"]
    missing.targets[0].ref["device_classes"] = ["Nope"]
    missing.targets[0].ref["content_fingerprint"] = "missing"
    missing.targets[0].name_at_plan = "Ghost"
    gone = executor.run(missing)
    assert gone.failure is MutationFailure.TARGET_NOT_FOUND
    assert gone.musical_writes == 0

    mismatch = volume_intent(session=session, track=track, requested_after=0.70)
    mismatch.project_identity = "other-project"
    assert executor.run(mismatch).failure is MutationFailure.PROJECT_MISMATCH

    stale_session = executor.tools.get_session_snapshot()
    stale = volume_intent(
        session=stale_session, track=stale_session.tracks[0], requested_after=0.70
    )
    daw.set_track_mute(0, True)
    assert executor.run(stale).failure is MutationFailure.STALE_PLAN

    pre_session = executor.tools.get_session_snapshot()
    pre = volume_intent(
        session=pre_session, track=pre_session.tracks[0], requested_after=0.70
    )
    pre.executions[0].expected_before["volume"] = 0.12
    assert executor.run(pre).failure is MutationFailure.PRECONDITION_FAILED

    daw.create_midi_track("Coffee Leaf")
    daw.tracks[1]["mixer"]["volume"] = float(daw.tracks[0]["mixer"]["volume"])
    twin_session = executor.tools.get_session_snapshot()
    twin = volume_intent(
        session=twin_session, track=twin_session.tracks[0], requested_after=0.70
    )
    ambiguous = executor.run(twin)
    assert ambiguous.failure is MutationFailure.TARGET_AMBIGUOUS


def test_uncertified_action_persists_rollback_and_does_not_write(tmp_path: Path) -> None:
    _daw, executor, session, track = _executor(tmp_path)
    intent = volume_intent(session=session, track=track, requested_after=0.70)
    extra = MutationExecution(
        action_id="eq_future",
        action_type="FUTURE_EQ",
        operation="future_eq",
        certified=False,
        rollback=MutationRollback(
            inverse_operation="future_eq_inverse",
            reversibility=RollbackReversibility.NOT_INDEPENDENTLY_REVERSIBLE,
            depends_on=[intent.executions[0].action_id],
        ),
    )
    extra_target = MutationTarget(
        action_id="eq_future",
        ref=intent.targets[0].ref,
        stable_id=intent.targets[0].stable_id,
        name_at_plan=intent.targets[0].name_at_plan,
        fingerprint=intent.targets[0].fingerprint,
        locator=intent.targets[0].locator,
        session_incarnation_id=intent.targets[0].session_incarnation_id,
    )
    intent = intent.model_copy(
        update={
            "executions": intent.executions + [extra],
            "targets": intent.targets + [extra_target],
        }
    )
    result = executor.run(intent)
    assert result.failure is MutationFailure.PRECONDITION_FAILED
    assert result.musical_writes == 0
    assert result.rollback_plan["undo_action_ids"][0] == "eq_future"
    assert Path(result.prestate_path).is_file()
    assert executor.tools.get_session_snapshot().tracks[0].mixer.volume == pytest.approx(
        0.85
    )


def test_set_track_volume_compatibility(tmp_path: Path) -> None:
    daw, executor, session, track = _executor(tmp_path, volume=0.75)
    intent = volume_intent(
        session=session,
        track=track,
        requested_after=0.73,
        decision_after_verify=ApplyDecision.ROLLBACK,
    )
    result = executor.run(intent)
    assert result.ok is True
    assert result.decision is ApplyDecision.ROLLBACK
    assert result.rollback_verification is not None
    assert result.rollback_verification.ok is True
    live = daw.snapshot().track_by_name("Coffee Leaf")
    assert live is not None
    assert live.mixer.volume == pytest.approx(0.75)

    loop = execute_controlled_write_loop(
        executor.tools,
        target_track="Coffee Leaf",
        delta=-0.02,
        expected_before=0.75,
        source_plan_id="compat",
        source_dry_run_envelope_id="env_compat",
        persist_dir=tmp_path,
    )
    assert loop["CONTROLLED_WRITE_LOOP_V1"] == "VERIFIED"
    assert loop["after_readback"] == pytest.approx(0.73)
    assert loop["rollback_readback"] == pytest.approx(0.75)


def test_no_new_musical_actions() -> None:
    assert APPLY_VOCABULARY == ("SET_TRACK_VOLUME",)
    assert control_contract()["APPLY_VOCABULARY"] == ["SET_TRACK_VOLUME"]
    assert list(ActionType) == [ActionType.SET_TRACK_VOLUME]
    assert certified_production_actions() == frozenset({CERTIFIED_PRODUCTION_ACTION})
    assert action_is_certified("SET_TRACK_VOLUME") is True
    assert action_is_certified("SET_EQ") is False
    assert action_is_certified("COMPRESS") is False
    assert action_is_certified("EDIT_MIDI") is False
    assert action_is_certified("ARRANGE") is False
    source = (
        Path(__file__).resolve().parents[1] / "src" / "copilot" / "runtime" / "safe_write.py"
    ).read_text(encoding="utf-8")
    assert "execute_mutation_batch" not in source
    assert "MutationBatch(" not in source
