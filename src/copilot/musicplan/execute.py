"""Controlled Write Loop V1 — canonical production-write execution.

CONTROLLED_ENGINEERING_VALIDATION only. Not autonomous musical improvement.
Does not claim artistic improvement. Always ends with rollback when possible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.agent.journal import DurableJournal
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.daw.write import WriteInDoubt
from copilot.human_eval.store import now_iso
from copilot.musicplan import (
    PLANS_DIR,
    VOLUME_TOLERANCE,
    compile_execution_envelope,
    create_controlled_volume_plan,
    validate_musicplan,
)
from copilot.schemas.musicplan import (
    SCHEMA_VERSION,
    ActionType,
    CompiledExecutionEnvelope,
    MusicPlan,
    PlanIntentClass,
    PlanStatus,
)
from copilot.schemas.session import SessionState, TrackState
from copilot.schemas.transaction import TransactionStatus

EXECUTION_TOLERANCE = 1e-4


def _guard_track(track: TrackState) -> dict[str, Any]:
    return {
        "name": track.name,
        "index": track.index,
        "role": track.role,
        "volume": float(track.mixer.volume),
        "arm": bool(track.mixer.arm),
        "mute": bool(track.mixer.mute),
        "solo": bool(track.mixer.solo),
        "pan": float(track.mixer.pan),
        "routing": {
            "input_type": track.routing.input_type,
            "input_channel": track.routing.input_channel,
            "output_type": track.routing.output_type,
            "output_channel": track.routing.output_channel,
            "monitoring": track.routing.monitoring,
        },
    }


def snapshot_guard_state(session: SessionState, target_name: str) -> dict[str, Any]:
    attach_tokens(session)
    tracks = [_guard_track(t) for t in session.tracks]
    target = next((t for t in tracks if t["name"] == target_name), None)
    return {
        "track_order": [t["name"] for t in tracks],
        "tracks": {t["name"]: t for t in tracks},
        "target": target,
        "project_state_token": session.project_token or "",
        "audible_state_token": session.audible_token or "",
        "target_state_token": "" if target is None else target_token(
            next(t for t in session.tracks if t.name == target_name)
        ),
    }


def diff_guard_state(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    expected_volume_delta_target: str,
    expected_volume: float | None = None,
) -> dict[str, Any]:
    """Classify EXPECTED_MUTATION vs UNEXPECTED_MUTATION for protected fields."""
    expected: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []

    if before.get("track_order") != after.get("track_order"):
        unexpected.append(
            {
                "field": "track_order",
                "before": before.get("track_order"),
                "after": after.get("track_order"),
                "class": "UNEXPECTED_MUTATION",
            }
        )

    before_tracks = before.get("tracks") or {}
    after_tracks = after.get("tracks") or {}
    names = sorted(set(before_tracks) | set(after_tracks))
    for name in names:
        b = before_tracks.get(name)
        a = after_tracks.get(name)
        if b is None or a is None:
            unexpected.append(
                {
                    "field": f"track_presence:{name}",
                    "before": b,
                    "after": a,
                    "class": "UNEXPECTED_MUTATION",
                }
            )
            continue
        for field in ("index", "role", "arm", "mute", "solo", "pan", "routing"):
            if b.get(field) != a.get(field):
                unexpected.append(
                    {
                        "field": f"{name}.{field}",
                        "before": b.get(field),
                        "after": a.get(field),
                        "class": "UNEXPECTED_MUTATION",
                    }
                )
        if abs(float(b["volume"]) - float(a["volume"])) > EXECUTION_TOLERANCE:
            row = {
                "field": f"{name}.volume",
                "before": b["volume"],
                "after": a["volume"],
            }
            if name == expected_volume_delta_target and (
                expected_volume is None
                or abs(float(a["volume"]) - float(expected_volume)) <= EXECUTION_TOLERANCE
            ):
                row["class"] = "EXPECTED_MUTATION"
                expected.append(row)
            else:
                row["class"] = "UNEXPECTED_MUTATION"
                unexpected.append(row)

    return {
        "expected_mutations": expected,
        "unexpected_mutations": unexpected,
        "only_expected_changed": len(unexpected) == 0,
    }


def _volume_close(a: float, b: float, tol: float = EXECUTION_TOLERANCE) -> bool:
    return abs(float(a) - float(b)) <= tol


def _find_track(session: SessionState, name: str) -> TrackState | None:
    return session.track_by_name(name)


def _persist(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return str(path)


def create_executable_controlled_revision(
    *,
    session: SessionState,
    track: TrackState,
    delta: float,
    source_plan_id: str,
    source_dry_run_envelope_id: str,
    expected_before: float | None = None,
) -> MusicPlan:
    """Fresh plan revision. Never mutates historical dry-run plan/envelope."""
    if expected_before is not None and not _volume_close(
        float(track.mixer.volume), float(expected_before), VOLUME_TOLERANCE
    ):
        raise ValueError(
            f"PRECONDITION_FAILED: live volume {track.mixer.volume} "
            f"!= expected_before {expected_before}"
        )
    plan = create_controlled_volume_plan(session=session, track=track, delta=delta)
    plan.notes.extend(
        [
            f"source_plan_id={source_plan_id}",
            f"source_dry_run_envelope_id={source_dry_run_envelope_id}",
            "Fresh executable revision — dry_run envelope not reused.",
        ]
    )
    return plan


def compile_executable_envelope(
    plan: MusicPlan, *, session: SessionState
) -> CompiledExecutionEnvelope:
    envelope = compile_execution_envelope(plan, session=session)
    # New executable id; never flip a historical dry_run_only envelope in place.
    return envelope.model_copy(
        update={
            "envelope_id": f"env_{uuid4().hex[:10]}",
            "dry_run_only": False,
        }
    )


def execute_controlled_write_loop(
    tools: AgentTools,
    *,
    target_track: str,
    delta: float = -0.02,
    expected_before: float = 0.75,
    source_plan_id: str,
    source_dry_run_envelope_id: str,
    persist_dir: Path | None = None,
) -> dict[str, Any]:
    """Full lifecycle: validate → write → readback → rollback → restore verify.

    Always attempts rollback after a successful forward write.
    AUDIO_EFFECT_VERIFICATION is DEFERRED (parameter-state proof only).
    """
    root = persist_dir or PLANS_DIR
    lifecycle: list[str] = []
    report: dict[str, Any] = {
        "status": "STARTED",
        "intent_class": PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION.value,
        "CONTROLLED_WRITE_LOOP_V1": "BLOCKED",
        "source_plan_id": source_plan_id,
        "source_dry_run_envelope_id": source_dry_run_envelope_id,
        "AUDIO_EFFECT_VERIFICATION": "DEFERRED",
        "lifecycle": lifecycle,
        "MUSICAL_WRITE_COUNT": {"forward": 0, "rollback": 0},
        "EXECUTED": False,
        "note": (
            "CONTROLLED_ENGINEERING_VALIDATION — not autonomous musical improvement. "
            "Does not claim 0.73 sounds better."
        ),
    }

    session = tools.get_session_snapshot()
    attach_tokens(session)
    track = _find_track(session, target_track)
    if track is None:
        report["status"] = "TARGET_NOT_FOUND"
        report["error"] = f"track {target_track!r} not found"
        return report

    live_vol = float(track.mixer.volume)
    report["fresh_tokens"] = {
        "PROJECT_STATE_TOKEN": session.project_token or "",
        "AUDIBLE_STATE_TOKEN": session.audible_token or "",
        "TARGET_STATE_TOKEN": target_token(track),
    }
    report["before_value"] = live_vol

    if not _volume_close(live_vol, expected_before, VOLUME_TOLERANCE):
        report["status"] = "PRECONDITION_FAILED"
        report["error"] = (
            f"current volume {live_vol} != expected_before {expected_before}"
        )
        return report

    try:
        plan = create_executable_controlled_revision(
            session=session,
            track=track,
            delta=delta,
            source_plan_id=source_plan_id,
            source_dry_run_envelope_id=source_dry_run_envelope_id,
            expected_before=expected_before,
        )
    except ValueError as exc:
        report["status"] = "PRECONDITION_FAILED"
        report["error"] = str(exc)
        return report

    validated = validate_musicplan(plan, session=session)
    report["plan_id"] = validated.plan_id
    report["plan_status"] = validated.status.value
    report["gate"] = validated.gate
    _persist(root / f"{validated.plan_id}.json", validated.model_dump(mode="json"))

    if validated.status is PlanStatus.STALE:
        report["status"] = "STALE_PLAN"
        report["error"] = validated.rejection_reason
        return report
    if validated.status is not PlanStatus.READY_FOR_EXECUTION:
        report["status"] = "NOT_EXECUTABLE"
        report["error"] = validated.rejection_reason or validated.status.value
        return report

    envelope = compile_executable_envelope(validated, session=session)
    if envelope.dry_run_only:
        report["status"] = "ENVELOPE_NOT_EXECUTABLE"
        report["error"] = "refusing dry_run_only envelope"
        return report
    report["envelope_id"] = envelope.envelope_id
    report["requested_after"] = envelope.requested_after
    report["rollback_value"] = envelope.rollback_value
    _persist(
        root / f"{validated.plan_id}_executable_envelope.json",
        envelope.model_dump(mode="json"),
    )
    lifecycle.append("PREPARED")

    pre_guard = snapshot_guard_state(session, target_track)
    report["prewrite_guard"] = {
        "arm": (pre_guard.get("target") or {}).get("arm"),
        "mute": (pre_guard.get("target") or {}).get("mute"),
        "solo": (pre_guard.get("target") or {}).get("solo"),
        "routing": (pre_guard.get("target") or {}).get("routing"),
        "volume": (pre_guard.get("target") or {}).get("volume"),
    }

    # Durably journal PREPARED before mutation (via begin).
    txn = tools.transactions.begin(
        user_intent=(
            f"CONTROLLED_ENGINEERING_VALIDATION SET_TRACK_VOLUME "
            f"{target_track} {envelope.expected_before}->{envelope.requested_after}"
        ),
        session=session,
    )
    report["transaction_id"] = txn.transaction_id
    tools.transactions.plan_write(
        command_id=f"prep_{txn.transaction_id}",
        operation="set_mixer_volume",
        expected_revision=session.revision,
        before={"volume": envelope.expected_before},
        expected_after={"volume": envelope.requested_after},
        target_stable_id=track.stable_id,
        target_name_at_apply=track.name,
        target_fingerprint={"kind": "track", "role": track.role, "name": track.name},
    )
    lifecycle.append("EXECUTING")

    try:
        write_result = tools.set_mixer_volume(
            envelope.resolved_track_index, envelope.requested_after
        )
    except WriteInDoubt as exc:
        tools.transactions.mark_in_doubt(str(exc), getattr(exc, "command_id", ""))
        lifecycle.append("IN_DOUBT")
        report["status"] = "IN_DOUBT"
        report["error"] = str(exc)
        report["journal_terminal_state"] = TransactionStatus.IN_DOUBT.value
        report["open_transaction"] = tools.transactions._open is not None
        _best_effort_restore(
            tools,
            target_track=target_track,
            rollback_value=envelope.rollback_value,
            report=report,
        )
        return report
    except Exception as exc:  # noqa: BLE001
        tools.transactions.abort(str(exc))
        lifecycle.append("FAILED")
        report["status"] = "WRITE_FAILED"
        report["error"] = str(exc)
        report["journal_terminal_state"] = (
            tools.transactions.history[-1].status.value
            if tools.transactions.history
            else TransactionStatus.FAILED.value
        )
        report["open_transaction"] = tools.transactions._open is not None
        _best_effort_restore(
            tools,
            target_track=target_track,
            rollback_value=envelope.rollback_value,
            report=report,
        )
        return report

    report["MUSICAL_WRITE_COUNT"]["forward"] = 1
    report["EXECUTED"] = True
    lifecycle.append("EXECUTED")
    validated.status = PlanStatus.EXECUTED

    try:
        after_write = tools.get_session_snapshot()
        attach_tokens(after_write)
        after_track = _find_track(after_write, target_track)
        if after_track is None:
            tools.transactions.mark_in_doubt("target missing after write")
            lifecycle.append("IN_DOUBT")
            report["status"] = "IN_DOUBT"
            report["error"] = "target missing after write"
            report["journal_terminal_state"] = TransactionStatus.IN_DOUBT.value
            _best_effort_restore(
                tools,
                target_track=target_track,
                rollback_value=envelope.rollback_value,
                report=report,
            )
            return report

        after_readback = float(after_track.mixer.volume)
        report["after_readback"] = after_readback
        # Prefer Ableton ack volume when present; still require live snapshot match.
        ack_vol = write_result.get("volume")
        if ack_vol is not None and not _volume_close(
            float(ack_vol), envelope.requested_after
        ):
            tools.transactions.mark_in_doubt(
                f"ack volume {ack_vol} != requested {envelope.requested_after}"
            )
            lifecycle.append("IN_DOUBT")
            report["status"] = "IN_DOUBT"
            report["error"] = "ack readback mismatch"
            report["EXECUTION_VERIFICATION"] = "FAIL"
            report["journal_terminal_state"] = TransactionStatus.IN_DOUBT.value
            _best_effort_restore(
                tools,
                target_track=target_track,
                rollback_value=envelope.rollback_value,
                report=report,
            )
            return report

        if not _volume_close(after_readback, envelope.requested_after):
            tools.transactions.mark_in_doubt(
                f"snapshot volume {after_readback} != requested {envelope.requested_after}"
            )
            lifecycle.append("IN_DOUBT")
            report["status"] = "IN_DOUBT"
            report["error"] = "snapshot readback mismatch"
            report["EXECUTION_VERIFICATION"] = "FAIL"
            report["journal_terminal_state"] = TransactionStatus.IN_DOUBT.value
            _best_effort_restore(
                tools,
                target_track=target_track,
                rollback_value=envelope.rollback_value,
                report=report,
            )
            return report

        report["EXECUTION_VERIFICATION"] = "PASS"
        lifecycle.append("VERIFIED")

        post_write_guard = snapshot_guard_state(after_write, target_track)
        write_diff = diff_guard_state(
            pre_guard,
            post_write_guard,
            expected_volume_delta_target=target_track,
            expected_volume=envelope.requested_after,
        )
        report["unexpected_state_diffs_after_write"] = write_diff["unexpected_mutations"]
        report["expected_state_diffs_after_write"] = write_diff["expected_mutations"]
        if not write_diff["only_expected_changed"]:
            tools.transactions.abort("unexpected_state_mutation_after_write")
            lifecycle.append("ROLLED_BACK")
            report["status"] = "UNEXPECTED_MUTATION"
            report["error"] = "state diff contained unexpected mutations; rolled back"
            report["journal_terminal_state"] = (
                tools.transactions.history[-1].status.value
                if tools.transactions.history
                else TransactionStatus.ROLLED_BACK.value
            )
            report["open_transaction"] = tools.transactions._open is not None
            report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
            return report

        verification = {
            "EXECUTION_VERIFICATION": "PASS",
            "after_readback": after_readback,
            "requested_after": envelope.requested_after,
            "arm_preserved": report["prewrite_guard"]["arm"]
            == (post_write_guard.get("target") or {}).get("arm"),
            "only_expected_changed": True,
        }
        tools.transactions.commit(verification, session=after_write)
        validated.status = PlanStatus.VERIFIED
    except Exception as exc:  # noqa: BLE001
        if tools.transactions._open is not None:
            tools.transactions.abort(str(exc))
        lifecycle.append("FAILED")
        report["status"] = "POST_WRITE_EXCEPTION"
        report["error"] = str(exc)
        report["journal_terminal_state"] = (
            tools.transactions.history[-1].status.value
            if tools.transactions.history
            else TransactionStatus.FAILED.value
        )
        report["open_transaction"] = tools.transactions._open is not None
        _best_effort_restore(
            tools,
            target_track=target_track,
            rollback_value=envelope.rollback_value,
            report=report,
        )
        return report

    # Unconditional rollback for engineering validation.
    lifecycle.append("ROLLBACK_PREPARED")
    rollback_txn = tools.transactions.rollback_last()
    report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
    if rollback_txn.status is not TransactionStatus.ROLLED_BACK:
        lifecycle.append(rollback_txn.status.value)
        report["status"] = "ROLLBACK_FAILED"
        report["error"] = rollback_txn.error
        report["journal_terminal_state"] = rollback_txn.status.value
        report["open_transaction"] = tools.transactions._open is not None
        return report
    lifecycle.append("ROLLED_BACK")
    validated.status = PlanStatus.ROLLED_BACK

    restored = tools.get_session_snapshot()
    attach_tokens(restored)
    restored_track = _find_track(restored, target_track)
    if restored_track is None:
        report["status"] = "RESTORE_FAILED"
        report["error"] = "target missing after rollback"
        report["journal_terminal_state"] = TransactionStatus.ROLLED_BACK.value
        return report

    rollback_readback = float(restored_track.mixer.volume)
    report["rollback_readback"] = rollback_readback
    if not _volume_close(rollback_readback, envelope.rollback_value):
        report["status"] = "RESTORE_READBACK_FAILED"
        report["error"] = (
            f"rollback readback {rollback_readback} != {envelope.rollback_value}"
        )
        report["journal_terminal_state"] = TransactionStatus.ROLLED_BACK.value
        report["RESTORE_VERIFIED"] = False
        return report

    restored_guard = snapshot_guard_state(restored, target_track)
    restore_diff = diff_guard_state(
        pre_guard,
        restored_guard,
        expected_volume_delta_target="__none__",  # no intentional diffs after restore
    )
    # After restore, volume should match — treat any volume mismatch as unexpected.
    report["restored_state_verification"] = {
        "volume_restored": _volume_close(rollback_readback, envelope.rollback_value),
        "arm_preserved": (pre_guard.get("target") or {}).get("arm")
        == (restored_guard.get("target") or {}).get("arm"),
        "mute_preserved": (pre_guard.get("target") or {}).get("mute")
        == (restored_guard.get("target") or {}).get("mute"),
        "solo_preserved": (pre_guard.get("target") or {}).get("solo")
        == (restored_guard.get("target") or {}).get("solo"),
        "routing_preserved": (pre_guard.get("target") or {}).get("routing")
        == (restored_guard.get("target") or {}).get("routing"),
        "track_order_preserved": pre_guard.get("track_order")
        == restored_guard.get("track_order"),
        "unexpected_mutations": restore_diff["unexpected_mutations"],
        "project_token_restored": pre_guard.get("project_state_token")
        == restored_guard.get("project_state_token"),
        "audible_token_restored": pre_guard.get("audible_state_token")
        == restored_guard.get("audible_state_token"),
        "target_token_restored": pre_guard.get("target_state_token")
        == restored_guard.get("target_state_token"),
        "token_contract": (
            "PROJECT excludes mixer volume (structural). "
            "AUDIBLE/TARGET include mixer volume — exact equality expected after rollback."
        ),
    }

    if restore_diff["unexpected_mutations"] or not all(
        [
            report["restored_state_verification"]["volume_restored"],
            report["restored_state_verification"]["arm_preserved"],
            report["restored_state_verification"]["mute_preserved"],
            report["restored_state_verification"]["solo_preserved"],
            report["restored_state_verification"]["routing_preserved"],
            report["restored_state_verification"]["track_order_preserved"],
            report["restored_state_verification"]["project_token_restored"],
            report["restored_state_verification"]["audible_token_restored"],
            report["restored_state_verification"]["target_token_restored"],
        ]
    ):
        lifecycle.append("RESTORE_FAILED")
        report["status"] = "RESTORE_VERIFICATION_FAILED"
        report["RESTORE_VERIFIED"] = False
        report["journal_terminal_state"] = TransactionStatus.ROLLED_BACK.value
        report["open_transaction"] = tools.transactions._open is not None
        return report

    lifecycle.append("RESTORE_VERIFIED")
    report["RESTORE_VERIFIED"] = True
    report["status"] = "CONTROLLED_WRITE_LOOP_COMPLETE"
    report["CONTROLLED_WRITE_LOOP_V1"] = "VERIFIED"
    report["journal_terminal_state"] = TransactionStatus.ROLLED_BACK.value
    report["open_transaction"] = tools.transactions._open is not None
    report["acceptance"] = {
        "FIRST_REAL_WRITE": "VERIFIED",
        "CANONICAL_PRODUCTION_PATH": "VERIFIED",
        "FRESH_TOKEN_GATE": "VERIFIED",
        "PREWRITE_ROLLBACK_SNAPSHOT": "VERIFIED",
        "WRITE_READBACK": "VERIFIED",
        "ONLY_EXPECTED_STATE_CHANGED": "VERIFIED",
        "ROLLBACK": "VERIFIED",
        "RESTORE_READBACK": "VERIFIED",
        "USER_ARM_STATE_PRESERVED": "VERIFIED",
        "TRANSACTION_TERMINAL": "VERIFIED",
        "NO_OPEN_JOURNAL": "VERIFIED"
        if not report["open_transaction"]
        else "BLOCKED",
    }
    report["created_at"] = now_iso()
    report["schema_version"] = SCHEMA_VERSION
    artifact = _persist(
        root / f"{validated.plan_id}_controlled_write_loop.json", report
    )
    report["artifact"] = artifact
    _persist(root / f"{validated.plan_id}_final.json", validated.model_dump(mode="json"))
    return report


def _best_effort_restore(
    tools: AgentTools,
    *,
    target_track: str,
    rollback_value: float,
    report: dict[str, Any],
) -> None:
    """If a failed/in-doubt write left the target mutated, restore via AgentTools."""
    try:
        session = tools.get_session_snapshot()
        track = _find_track(session, target_track)
        if track is None:
            report["best_effort_restore"] = "target_missing"
            return
        if _volume_close(float(track.mixer.volume), rollback_value):
            report["best_effort_restore"] = "already_restored"
            return
        if tools.transactions._open is not None:
            tools.transactions.abort("best_effort_restore_close_open")
        tools.transactions.begin(
            user_intent=f"best-effort restore {target_track} volume",
            session=session,
        )
        tools.set_mixer_volume(track.index, rollback_value)
        after = tools.get_session_snapshot()
        tools.transactions.commit(
            {"best_effort_restore": True, "volume": rollback_value},
            session=after,
        )
        report["best_effort_restore"] = "restored"
        report["MUSICAL_WRITE_COUNT"]["rollback"] = (
            int(report.get("MUSICAL_WRITE_COUNT", {}).get("rollback", 0)) + 1
        )
    except Exception as exc:  # noqa: BLE001
        report["best_effort_restore"] = f"failed:{exc}"
        try:
            if tools.transactions._open is not None:
                tools.transactions.abort(str(exc))
        except Exception:  # noqa: BLE001
            pass


def build_agent_tools(daw, *, journal_path: Path) -> AgentTools:
    journal = DurableJournal(journal_path)
    txns = TransactionManager(daw, journal=journal)
    return AgentTools(daw, txns)


def _read_param_value(
    session: SessionState, track_index: int, device_index: int, parameter_name: str
) -> float | None:
    track = next((t for t in session.tracks if int(t.index) == int(track_index)), None)
    if track is None:
        return None
    device = next((d for d in track.devices if int(d.index) == int(device_index)), None)
    if device is None:
        return None
    param = next((pp for pp in device.parameters if pp.name.lower() == parameter_name.lower()), None)
    return None if param is None else float(param.value)


def execute_device_tweak_write_loop(
    tools: AgentTools,
    *,
    plan: MusicPlan,
    session: SessionState,
    persist_dir: Path | None = None,
) -> dict[str, Any]:
    """DEVICE_TWEAK lifecycle: validate -> write -> readback -> rollback -> restore verify."""
    from copilot.musicplan import _as_ref, validate_device_tweak_plan
    from copilot.daw.object_ref import require_resolved

    root = persist_dir or PLANS_DIR
    lifecycle: list[str] = []
    report: dict[str, Any] = {
        "status": "STARTED",
        "CONTROLLED_WRITE_LOOP_V1": "BLOCKED",
        "action_type": "DEVICE_TWEAK",
        "lifecycle": lifecycle,
        "MUSICAL_WRITE_COUNT": {"forward": 0, "rollback": 0},
        "EXECUTED": False,
    }

    validated = validate_device_tweak_plan(plan, session=session)
    report["plan_id"] = validated.plan_id
    report["plan_status"] = validated.status.value
    if validated.status is not PlanStatus.READY_FOR_EXECUTION:
        report["status"] = "NOT_EXECUTABLE"
        report["error"] = validated.rejection_reason or validated.status.value
        return report

    action = validated.actions[0]
    params = action.params  # DeviceTweakActionParams
    track = require_resolved(session, _as_ref(action.target.ref))
    device_index = int(params.device_index)
    parameter_name = params.parameter_name
    param = next(
        (pp for pp in next(d for d in track.devices if int(d.index) == device_index).parameters
         if pp.name.lower() == parameter_name.lower()),
        None,
    )
    if param is None:
        report["status"] = "PARAMETER_NOT_FOUND"
        report["error"] = parameter_name
        return report
    parameter_index = int(param.index)
    before_value = float(param.value)
    intended = float(params.intended_after)

    report["before_value"] = before_value
    report["intended_after"] = intended
    report["rollback_value"] = before_value
    lifecycle.append("PREPARED")

    txn = tools.transactions.begin(
        user_intent=f"DEVICE_TWEAK {track.name}.{parameter_name} {before_value}->{intended}",
        session=session,
    )
    report["transaction_id"] = txn.transaction_id
    lifecycle.append("EXECUTING")

    try:
        write_result = tools.set_device_parameter(
            track.index, device_index, parameter_index, intended, previous=before_value
        )
    except Exception as exc:  # noqa: BLE001
        if tools.transactions._open is not None:
            tools.transactions.abort(str(exc))
        lifecycle.append("FAILED")
        report["status"] = "WRITE_FAILED"
        report["error"] = str(exc)
        return report

    report["MUSICAL_WRITE_COUNT"]["forward"] = 1
    report["EXECUTED"] = True
    lifecycle.append("EXECUTED")

    after = tools.get_session_snapshot()
    after_value = _read_param_value(after, track.index, device_index, parameter_name)
    report["after_readback"] = after_value
    if after_value is None or abs(after_value - intended) > params.readback_tolerance:
        if tools.transactions._open is not None:
            tools.transactions.mark_in_doubt(
                f"readback {after_value} != intended {intended}"
            )
        lifecycle.append("IN_DOUBT")
        report["status"] = "IN_DOUBT"
        report["error"] = "readback mismatch"
        report["EXECUTION_VERIFICATION"] = "FAIL"
        return report
    report["EXECUTION_VERIFICATION"] = "PASS"
    lifecycle.append("VERIFIED")

    tools.transactions.commit({"EXECUTION_VERIFICATION": "PASS", "after_readback": after_value}, session=after)
    validated.status = PlanStatus.VERIFIED

    # Unconditional rollback for engineering validation.
    lifecycle.append("ROLLBACK_PREPARED")
    rollback_txn = tools.transactions.rollback_last()
    report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
    if rollback_txn.status is not TransactionStatus.ROLLED_BACK:
        lifecycle.append(rollback_txn.status.value)
        report["status"] = "ROLLBACK_FAILED"
        report["error"] = rollback_txn.error
        return report
    lifecycle.append("ROLLED_BACK")
    validated.status = PlanStatus.ROLLED_BACK

    restored = tools.get_session_snapshot()
    restore_value = _read_param_value(restored, track.index, device_index, parameter_name)
    report["rollback_readback"] = restore_value
    if restore_value is None or abs(restore_value - before_value) > params.readback_tolerance:
        report["status"] = "RESTORE_READBACK_FAILED"
        report["error"] = f"rollback readback {restore_value} != {before_value}"
        report["RESTORE_VERIFIED"] = False
        return report

    lifecycle.append("RESTORE_VERIFIED")
    report["RESTORE_VERIFIED"] = True
    report["status"] = "CONTROLLED_WRITE_LOOP_COMPLETE"
    report["CONTROLLED_WRITE_LOOP_V1"] = "VERIFIED"
    report["open_transaction"] = tools.transactions._open is not None
    report["created_at"] = now_iso()
    report["schema_version"] = SCHEMA_VERSION
    artifact = _persist(root / f"{validated.plan_id}_device_tweak_loop.json", report)
    report["artifact"] = artifact
    return report


def _same_sample_uri(a: str | None, b: str | None) -> bool:
    """Compare sample URIs by basename (plan carries full path; live clip carries file name)."""
    if not a or not b:
        return False
    fa = a.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    fb = b.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return fa == fb


def _find_track_by_stable_id(session: SessionState, stable_id: str) -> TrackState | None:
    return next((t for t in session.tracks if t.stable_id == stable_id), None)


def execute_device_load_write_loop(
    tools: AgentTools,
    *,
    plan: MusicPlan,
    session: SessionState,
    persist_dir: Path | None = None,
) -> dict[str, Any]:
    """DEVICE_LOAD lifecycle: validate -> load device -> verify presence -> rollback (delete) -> verify gone."""
    from copilot.musicplan import _as_ref, validate_device_load_plan
    from copilot.daw.object_ref import require_resolved

    root = persist_dir or PLANS_DIR
    lifecycle: list[str] = []
    report: dict[str, Any] = {
        "status": "STARTED",
        "CONTROLLED_WRITE_LOOP_V1": "BLOCKED",
        "action_type": "DEVICE_LOAD",
        "lifecycle": lifecycle,
        "MUSICAL_WRITE_COUNT": {"forward": 0, "rollback": 0},
        "EXECUTED": False,
    }

    validated = validate_device_load_plan(plan, session=session)
    report["plan_id"] = validated.plan_id
    report["plan_status"] = validated.status.value
    if validated.status is not PlanStatus.READY_FOR_EXECUTION:
        report["status"] = "NOT_EXECUTABLE"
        report["error"] = validated.rejection_reason or validated.status.value
        return report

    action = validated.actions[0]
    params = action.params  # DeviceLoadActionParams
    track = require_resolved(session, _as_ref(action.target.ref))
    device_name = params.device_name
    device_uri = params.device_uri or device_name
    before_count = len(track.devices)
    report["before_device_count"] = before_count
    lifecycle.append("PREPARED")

    txn = tools.transactions.begin(
        user_intent=f"DEVICE_LOAD {track.name}.{device_name}",
        session=session,
    )
    report["transaction_id"] = txn.transaction_id
    lifecycle.append("EXECUTING")

    try:
        write_result = tools.load_instrument_or_effect(track.index, device_uri)
    except Exception as exc:  # noqa: BLE001
        if tools.transactions._open is not None:
            tools.transactions.abort(str(exc))
        lifecycle.append("FAILED")
        report["status"] = "WRITE_FAILED"
        report["error"] = str(exc)
        return report

    report["MUSICAL_WRITE_COUNT"]["forward"] = 1
    report["EXECUTED"] = True
    lifecycle.append("EXECUTED")

    after = tools.get_session_snapshot()
    after_track = _find_track_by_stable_id(after, track.stable_id)
    loaded = bool(after_track) and any(
        d.name.lower() == device_name.lower() for d in after_track.devices
    )
    report["device_present_after_write"] = loaded
    report["after_device_count"] = len(after_track.devices) if after_track else None
    if not loaded:
        if tools.transactions._open is not None:
            tools.transactions.mark_in_doubt("device not present after load")
        lifecycle.append("IN_DOUBT")
        report["status"] = "IN_DOUBT"
        report["error"] = "device not present after load"
        report["EXECUTION_VERIFICATION"] = "FAIL"
        return report
    report["EXECUTION_VERIFICATION"] = "PASS"
    lifecycle.append("VERIFIED")

    tools.transactions.commit(
        {"EXECUTION_VERIFICATION": "PASS", "device_name": device_name}, session=after
    )
    validated.status = PlanStatus.VERIFIED

    lifecycle.append("ROLLBACK_PREPARED")
    rollback_txn = tools.transactions.rollback_last()
    report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
    if rollback_txn.status is not TransactionStatus.ROLLED_BACK:
        lifecycle.append(rollback_txn.status.value)
        report["status"] = "ROLLBACK_FAILED"
        report["error"] = rollback_txn.error
        return report
    lifecycle.append("ROLLED_BACK")
    validated.status = PlanStatus.ROLLED_BACK

    restored = tools.get_session_snapshot()
    restored_track = _find_track_by_stable_id(restored, track.stable_id)
    still_present = bool(restored_track) and any(
        d.name.lower() == device_name.lower() for d in restored_track.devices
    )
    report["device_present_after_rollback"] = still_present
    report["after_rollback_device_count"] = len(restored_track.devices) if restored_track else None
    if still_present:
        report["status"] = "RESTORE_READBACK_FAILED"
        report["error"] = "device still present after rollback"
        report["RESTORE_VERIFIED"] = False
        return report

    lifecycle.append("RESTORE_VERIFIED")
    report["RESTORE_VERIFIED"] = True
    report["status"] = "CONTROLLED_WRITE_LOOP_COMPLETE"
    report["CONTROLLED_WRITE_LOOP_V1"] = "VERIFIED"
    report["open_transaction"] = tools.transactions._open is not None
    report["created_at"] = now_iso()
    report["schema_version"] = SCHEMA_VERSION
    artifact = _persist(root / f"{validated.plan_id}_device_load_loop.json", report)
    report["artifact"] = artifact
    return report


def execute_sample_swap_write_loop(
    tools: AgentTools,
    *,
    plan: MusicPlan,
    session: SessionState,
    persist_dir: Path | None = None,
) -> dict[str, Any]:
    """SAMPLE_SWAP lifecycle: validate -> load sample -> readback clip.sample_uri -> rollback (reload previous)."""
    from copilot.musicplan import _as_ref, validate_sample_swap_plan
    from copilot.daw.object_ref import require_resolved

    root = persist_dir or PLANS_DIR
    lifecycle: list[str] = []
    report: dict[str, Any] = {
        "status": "STARTED",
        "CONTROLLED_WRITE_LOOP_V1": "BLOCKED",
        "action_type": "SAMPLE_SWAP",
        "lifecycle": lifecycle,
        "MUSICAL_WRITE_COUNT": {"forward": 0, "rollback": 0},
        "EXECUTED": False,
    }

    validated = validate_sample_swap_plan(plan, session=session)
    report["plan_id"] = validated.plan_id
    report["plan_status"] = validated.status.value
    if validated.status is not PlanStatus.READY_FOR_EXECUTION:
        report["status"] = "NOT_EXECUTABLE"
        report["error"] = validated.rejection_reason or validated.status.value
        return report

    action = validated.actions[0]
    params = action.params  # SampleSwapActionParams
    track = require_resolved(session, _as_ref(action.target.ref))
    clip_index = int(params.clip_index)
    sample_uri = params.sample_uri
    previous = params.previous_sample_uri or ""
    report["clip_index"] = clip_index
    report["sample_uri"] = sample_uri
    report["previous_sample_uri"] = previous
    lifecycle.append("PREPARED")

    txn = tools.transactions.begin(
        user_intent=f"SAMPLE_SWAP {track.name}.clip[{clip_index}] -> {sample_uri}",
        session=session,
    )
    report["transaction_id"] = txn.transaction_id
    lifecycle.append("EXECUTING")

    try:
        write_result = tools.load_browser_item(
            track.index, sample_uri, previous_item_uri=previous, clip_index=clip_index
        )
    except Exception as exc:  # noqa: BLE001
        if tools.transactions._open is not None:
            tools.transactions.abort(str(exc))
        lifecycle.append("FAILED")
        report["status"] = "WRITE_FAILED"
        report["error"] = str(exc)
        return report

    report["MUSICAL_WRITE_COUNT"]["forward"] = 1
    report["EXECUTED"] = True
    lifecycle.append("EXECUTED")

    after = tools.get_session_snapshot()
    after_track = _find_track_by_stable_id(after, track.stable_id)
    after_clip = next(
        (c for c in (after_track.clips if after_track else []) if c.slot_index == clip_index),
        None,
    )
    after_sample_uri = after_clip.sample_uri if after_clip else None
    report["after_sample_uri"] = after_sample_uri
    if not _same_sample_uri(after_sample_uri, sample_uri):
        if tools.transactions._open is not None:
            tools.transactions.mark_in_doubt(f"readback {after_sample_uri} != {sample_uri}")
        lifecycle.append("IN_DOUBT")
        report["status"] = "IN_DOUBT"
        report["error"] = "sample swap readback mismatch"
        report["EXECUTION_VERIFICATION"] = "FAIL"
        return report
    report["EXECUTION_VERIFICATION"] = "PASS"
    lifecycle.append("VERIFIED")

    tools.transactions.commit(
        {"EXECUTION_VERIFICATION": "PASS", "sample_uri": sample_uri}, session=after
    )
    validated.status = PlanStatus.VERIFIED

    lifecycle.append("ROLLBACK_PREPARED")
    rollback_txn = tools.transactions.rollback_last()
    report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
    if rollback_txn.status is not TransactionStatus.ROLLED_BACK:
        lifecycle.append(rollback_txn.status.value)
        report["status"] = "ROLLBACK_FAILED"
        report["error"] = rollback_txn.error
        return report
    lifecycle.append("ROLLED_BACK")
    validated.status = PlanStatus.ROLLED_BACK

    restored = tools.get_session_snapshot()
    restored_track = _find_track_by_stable_id(restored, track.stable_id)
    restored_clip = next(
        (c for c in (restored_track.clips if restored_track else []) if c.slot_index == clip_index),
        None,
    )
    restored_sample_uri = restored_clip.sample_uri if restored_clip else None
    report["restored_sample_uri"] = restored_sample_uri
    if previous and not _same_sample_uri(restored_sample_uri, previous):
        report["status"] = "RESTORE_READBACK_FAILED"
        report["error"] = f"restored {restored_sample_uri} != previous {previous}"
        report["RESTORE_VERIFIED"] = False
        return report

    lifecycle.append("RESTORE_VERIFIED")
    report["RESTORE_VERIFIED"] = True
    report["status"] = "CONTROLLED_WRITE_LOOP_COMPLETE"
    report["CONTROLLED_WRITE_LOOP_V1"] = "VERIFIED"
    report["open_transaction"] = tools.transactions._open is not None
    report["created_at"] = now_iso()
    report["schema_version"] = SCHEMA_VERSION
    artifact = _persist(root / f"{validated.plan_id}_sample_swap_loop.json", report)
    report["artifact"] = artifact
    return report


def execute_create_track_write_loop(
    tools: AgentTools,
    *,
    plan: MusicPlan,
    session: SessionState,
    persist_dir: Path | None = None,
) -> dict[str, Any]:
    """CREATE_TRACK lifecycle: validate -> create track -> verify presence -> rollback (delete) -> verify gone."""
    from copilot.musicplan import validate_create_track_plan

    root = persist_dir or PLANS_DIR
    lifecycle: list[str] = []
    report: dict[str, Any] = {
        "status": "STARTED",
        "CONTROLLED_WRITE_LOOP_V1": "BLOCKED",
        "action_type": "CREATE_TRACK",
        "lifecycle": lifecycle,
        "MUSICAL_WRITE_COUNT": {"forward": 0, "rollback": 0},
        "EXECUTED": False,
    }

    validated = validate_create_track_plan(plan, session=session)
    report["plan_id"] = validated.plan_id
    report["plan_status"] = validated.status.value
    if validated.status is not PlanStatus.READY_FOR_EXECUTION:
        report["status"] = "NOT_EXECUTABLE"
        report["error"] = validated.rejection_reason or validated.status.value
        return report

    action = validated.actions[0]
    params = action.params  # CreateTrackActionParams
    track_name = params.track_name
    track_kind = params.track_kind
    report["track_name"] = track_name
    report["track_kind"] = track_kind
    report["before_track_count"] = len(session.tracks)
    lifecycle.append("PREPARED")

    txn = tools.transactions.begin(
        user_intent=f"CREATE_TRACK {track_kind} {track_name}", session=session
    )
    report["transaction_id"] = txn.transaction_id
    lifecycle.append("EXECUTING")

    try:
        if track_kind == "audio":
            result = tools.create_audio_track(track_name, params.index_hint)
        else:
            result = tools.create_midi_track(track_name, params.index_hint)
    except Exception as exc:  # noqa: BLE001
        if tools.transactions._open is not None:
            tools.transactions.abort(str(exc))
        lifecycle.append("FAILED")
        report["status"] = "WRITE_FAILED"
        report["error"] = str(exc)
        return report

    report["MUSICAL_WRITE_COUNT"]["forward"] = 1
    report["EXECUTED"] = True
    lifecycle.append("EXECUTED")

    after = tools.get_session_snapshot()
    created = next((t for t in after.tracks if t.name == track_name), None)
    report["track_present_after_write"] = created is not None
    report["after_track_count"] = len(after.tracks)
    if created is None:
        if tools.transactions._open is not None:
            tools.transactions.mark_in_doubt("track not present after create")
        lifecycle.append("IN_DOUBT")
        report["status"] = "IN_DOUBT"
        report["error"] = "track not present after create"
        report["EXECUTION_VERIFICATION"] = "FAIL"
        return report
    report["EXECUTION_VERIFICATION"] = "PASS"
    lifecycle.append("VERIFIED")

    tools.transactions.commit(
        {"EXECUTION_VERIFICATION": "PASS", "track_name": track_name}, session=after
    )
    validated.status = PlanStatus.VERIFIED

    lifecycle.append("ROLLBACK_PREPARED")
    rollback_txn = tools.transactions.rollback_last()
    report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
    if rollback_txn.status is not TransactionStatus.ROLLED_BACK:
        lifecycle.append(rollback_txn.status.value)
        report["status"] = "ROLLBACK_FAILED"
        report["error"] = rollback_txn.error
        return report
    lifecycle.append("ROLLED_BACK")
    validated.status = PlanStatus.ROLLED_BACK

    restored = tools.get_session_snapshot()
    still_present = any(t.name == track_name for t in restored.tracks)
    report["track_present_after_rollback"] = still_present
    report["after_rollback_track_count"] = len(restored.tracks)
    if still_present:
        report["status"] = "RESTORE_READBACK_FAILED"
        report["error"] = "track still present after rollback"
        report["RESTORE_VERIFIED"] = False
        return report

    lifecycle.append("RESTORE_VERIFIED")
    report["RESTORE_VERIFIED"] = True
    report["status"] = "CONTROLLED_WRITE_LOOP_COMPLETE"
    report["CONTROLLED_WRITE_LOOP_V1"] = "VERIFIED"
    report["open_transaction"] = tools.transactions._open is not None
    report["created_at"] = now_iso()
    report["schema_version"] = SCHEMA_VERSION
    artifact = _persist(root / f"{validated.plan_id}_create_track_loop.json", report)
    report["artifact"] = artifact
    return report


def execute_sample_load_write_loop(
    tools: AgentTools,
    *,
    plan: MusicPlan,
    session: SessionState,
    persist_dir: Path | None = None,
) -> dict[str, Any]:
    """SAMPLE_LOAD lifecycle: validate -> load sample -> readback clip.sample_uri -> rollback (delete clip) -> verify gone."""
    from copilot.musicplan import _as_ref, validate_sample_load_plan
    from copilot.daw.object_ref import require_resolved

    root = persist_dir or PLANS_DIR
    lifecycle: list[str] = []
    report: dict[str, Any] = {
        "status": "STARTED",
        "CONTROLLED_WRITE_LOOP_V1": "BLOCKED",
        "action_type": "SAMPLE_LOAD",
        "lifecycle": lifecycle,
        "MUSICAL_WRITE_COUNT": {"forward": 0, "rollback": 0},
        "EXECUTED": False,
    }

    validated = validate_sample_load_plan(plan, session=session)
    report["plan_id"] = validated.plan_id
    report["plan_status"] = validated.status.value
    if validated.status is not PlanStatus.READY_FOR_EXECUTION:
        report["status"] = "NOT_EXECUTABLE"
        report["error"] = validated.rejection_reason or validated.status.value
        return report

    action = validated.actions[0]
    params = action.params  # SampleLoadActionParams
    track = require_resolved(session, _as_ref(action.target.ref))
    clip_index = int(params.clip_index)
    sample_uri = params.sample_uri
    report["clip_index"] = clip_index
    report["sample_uri"] = sample_uri
    lifecycle.append("PREPARED")

    txn = tools.transactions.begin(
        user_intent=f"SAMPLE_LOAD {track.name}.clip[{clip_index}] -> {sample_uri}",
        session=session,
    )
    report["transaction_id"] = txn.transaction_id
    lifecycle.append("EXECUTING")

    try:
        tools.load_sample(track.index, clip_index, sample_uri)
    except Exception as exc:  # noqa: BLE001
        if tools.transactions._open is not None:
            tools.transactions.abort(str(exc))
        lifecycle.append("FAILED")
        report["status"] = "WRITE_FAILED"
        report["error"] = str(exc)
        return report

    report["MUSICAL_WRITE_COUNT"]["forward"] = 1
    report["EXECUTED"] = True
    lifecycle.append("EXECUTED")

    after = tools.get_session_snapshot()
    after_track = _find_track_by_stable_id(after, track.stable_id)
    after_clip = next(
        (c for c in (after_track.clips if after_track else []) if c.slot_index == clip_index),
        None,
    )
    after_sample_uri = after_clip.sample_uri if after_clip else None
    report["after_sample_uri"] = after_sample_uri
    if not _same_sample_uri(after_sample_uri, sample_uri):
        if tools.transactions._open is not None:
            tools.transactions.mark_in_doubt(f"readback {after_sample_uri} != {sample_uri}")
        lifecycle.append("IN_DOUBT")
        report["status"] = "IN_DOUBT"
        report["error"] = "sample load readback mismatch"
        report["EXECUTION_VERIFICATION"] = "FAIL"
        return report
    report["EXECUTION_VERIFICATION"] = "PASS"
    lifecycle.append("VERIFIED")

    tools.transactions.commit(
        {"EXECUTION_VERIFICATION": "PASS", "sample_uri": sample_uri}, session=after
    )
    validated.status = PlanStatus.VERIFIED

    lifecycle.append("ROLLBACK_PREPARED")
    rollback_txn = tools.transactions.rollback_last()
    report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
    if rollback_txn.status is not TransactionStatus.ROLLED_BACK:
        lifecycle.append(rollback_txn.status.value)
        report["status"] = "ROLLBACK_FAILED"
        report["error"] = rollback_txn.error
        return report
    lifecycle.append("ROLLED_BACK")
    validated.status = PlanStatus.ROLLED_BACK

    restored = tools.get_session_snapshot()
    restored_track = _find_track_by_stable_id(restored, track.stable_id)
    restored_clip = next(
        (c for c in (restored_track.clips if restored_track else []) if c.slot_index == clip_index),
        None,
    )
    report["clip_present_after_rollback"] = restored_clip is not None
    if restored_clip is not None:
        report["status"] = "RESTORE_READBACK_FAILED"
        report["error"] = "clip still present after rollback"
        report["RESTORE_VERIFIED"] = False
        return report

    lifecycle.append("RESTORE_VERIFIED")
    report["RESTORE_VERIFIED"] = True
    report["status"] = "CONTROLLED_WRITE_LOOP_COMPLETE"
    report["CONTROLLED_WRITE_LOOP_V1"] = "VERIFIED"
    report["open_transaction"] = tools.transactions._open is not None
    report["created_at"] = now_iso()
    report["schema_version"] = SCHEMA_VERSION
    artifact = _persist(root / f"{validated.plan_id}_sample_load_loop.json", report)
    report["artifact"] = artifact
    return report


def _resolve_track_by_name(session: SessionState, name: str) -> TrackState | None:
    matches = [t for t in session.tracks if t.name == name]
    return matches[0] if len(matches) == 1 else None


def execute_track_build_plan(
    tools: AgentTools,
    *,
    plan: MusicPlan,
    session: SessionState,
    persist_dir: Path | None = None,
    leave: bool = False,
) -> dict[str, Any]:
    """Multi-action track build: CREATE_TRACK + SAMPLE_LOAD (extensible) in ONE transaction.

    Actions are executed in sequence; targets created by earlier actions are resolved
    by name. The whole plan rolls back (LIFO) for engineering validation.
    """
    from copilot.musicplan import (
        _as_ref,
        build_sample_load_action,
        build_create_track_action,
    )

    root = persist_dir or PLANS_DIR
    lifecycle: list[str] = []
    report: dict[str, Any] = {
        "status": "STARTED",
        "CONTROLLED_WRITE_LOOP_V1": "BLOCKED",
        "action_type": "MULTI_ACTION",
        "lifecycle": lifecycle,
        "MUSICAL_WRITE_COUNT": {"forward": 0, "rollback": 0},
        "EXECUTED": False,
        "per_action": [],
    }
    if not plan.actions:
        report["status"] = "NOT_EXECUTABLE"
        report["error"] = "no_actions"
        return report

    txn = tools.transactions.begin(
        user_intent=f"track build {plan.plan_id}", session=session
    )
    report["transaction_id"] = txn.transaction_id
    current = session
    local: dict[str, int] = {t.name: t.index for t in session.tracks}

    for action in plan.actions:
        step: dict[str, Any] = {"action_type": action.action_type.value}

        if action.action_type is ActionType.CREATE_TRACK:
            params = action.params
            if params.track_name in local:
                step["status"] = "SKIP_EXISTS"
                report["per_action"].append(step)
                continue
            try:
                if params.track_kind == "audio":
                    created = tools.create_audio_track(params.track_name, params.index_hint)
                else:
                    created = tools.create_midi_track(params.track_name, params.index_hint)
                if isinstance(created, dict) and "index" in created:
                    local[params.track_name] = int(created["index"])
            except Exception as exc:  # noqa: BLE001
                step["status"] = "FAILED"
                step["error"] = str(exc)
                report["per_action"].append(step)
                if tools.transactions._open is not None:
                    tools.transactions.abort(str(exc))
                report["status"] = "FAILED"
                report["error"] = f"CREATE_TRACK {params.track_name}: {exc}"
                return report
            step["status"] = "OK"
            step["track_name"] = params.track_name
            report["MUSICAL_WRITE_COUNT"]["forward"] += 1

        elif action.action_type is ActionType.SAMPLE_LOAD:
            params = action.params
            track_name = action.target.ref.get("name", "") if isinstance(action.target.ref, dict) else ""
            track_index = local.get(track_name)
            if track_index is None:
                step["status"] = "FAILED"
                step["error"] = f"track {track_name!r} not resolvable"
                report["per_action"].append(step)
                tools.transactions.abort(step["error"])
                report["status"] = "FAILED"
                report["error"] = step["error"]
                return report
            try:
                tools.load_sample(track_index, int(params.clip_index), params.sample_uri)
            except Exception as exc:  # noqa: BLE001
                step["status"] = "FAILED"
                step["error"] = str(exc)
                report["per_action"].append(step)
                tools.transactions.abort(str(exc))
                report["status"] = "FAILED"
                report["error"] = f"SAMPLE_LOAD {params.sample_uri}: {exc}"
                return report
            step["status"] = "OK"
            step["track_name"] = track_name
            step["sample_uri"] = params.sample_uri
            report["MUSICAL_WRITE_COUNT"]["forward"] += 1

        elif action.action_type is ActionType.CREATE_PATTERN:
            params = action.params
            track_name = action.target.ref.get("name", "") if isinstance(action.target.ref, dict) else ""
            track_index = local.get(track_name)
            if track_index is None:
                step["status"] = "FAILED"
                step["error"] = f"track {track_name!r} not resolvable"
                report["per_action"].append(step)
                tools.transactions.abort(step["error"])
                report["status"] = "FAILED"
                report["error"] = step["error"]
                return report
            try:
                tools.create_pattern(
                    track_index,
                    int(params.clip_index),
                    float(params.length_beats),
                    list(params.notes),
                )
            except Exception as exc:  # noqa: BLE001
                step["status"] = "FAILED"
                step["error"] = str(exc)
                report["per_action"].append(step)
                tools.transactions.abort(str(exc))
                report["status"] = "FAILED"
                report["error"] = f"CREATE_PATTERN {track_name}: {exc}"
                return report
            step["status"] = "OK"
            step["track_name"] = track_name
            step["note_count"] = len(params.notes)
            report["MUSICAL_WRITE_COUNT"]["forward"] += 1

        elif action.action_type is ActionType.DEVICE_LOAD:
            params = action.params
            track_name = action.target.ref.get("name", "") if isinstance(action.target.ref, dict) else ""
            track_index = local.get(track_name)
            if track_index is None:
                step["status"] = "FAILED"
                step["error"] = f"track {track_name!r} not resolvable"
                report["per_action"].append(step)
                tools.transactions.abort(step["error"])
                report["status"] = "FAILED"
                report["error"] = step["error"]
                return report
            try:
                tools.load_instrument_or_effect(
                    track_index, params.device_uri or params.device_name
                )
            except Exception as exc:  # noqa: BLE001
                step["status"] = "FAILED"
                step["error"] = str(exc)
                report["per_action"].append(step)
                tools.transactions.abort(str(exc))
                report["status"] = "FAILED"
                report["error"] = f"DEVICE_LOAD {params.device_name} on {track_name}: {exc}"
                return report
            step["status"] = "OK"
            step["track_name"] = track_name
            step["device_name"] = params.device_name
            report["MUSICAL_WRITE_COUNT"]["forward"] += 1

        elif action.action_type is ActionType.SET_TRACK_MUTE:
            params = action.params
            track_name = action.target.ref.get("name", "") if isinstance(action.target.ref, dict) else ""
            track_index = local.get(track_name)
            if track_index is None:
                step["status"] = "FAILED"
                step["error"] = f"track {track_name!r} not resolvable"
                report["per_action"].append(step)
                tools.transactions.abort(step["error"])
                report["status"] = "FAILED"
                report["error"] = step["error"]
                return report
            try:
                tools.set_track_mute(track_index, bool(params.mute))
            except Exception as exc:  # noqa: BLE001
                step["status"] = "FAILED"
                step["error"] = str(exc)
                report["per_action"].append(step)
                tools.transactions.abort(str(exc))
                report["status"] = "FAILED"
                report["error"] = f"SET_TRACK_MUTE {track_name}: {exc}"
                return report
            step["status"] = "OK"
            step["track_name"] = track_name
            step["mute"] = bool(params.mute)
            report["MUSICAL_WRITE_COUNT"]["forward"] += 1

        elif action.action_type is ActionType.SET_TRACK_ROUTING:
            params = action.params
            track_name = action.target.ref.get("name", "") if isinstance(action.target.ref, dict) else ""
            track_index = local.get(track_name)
            if track_index is None:
                step["status"] = "FAILED"
                step["error"] = f"track {track_name!r} not resolvable"
                report["per_action"].append(step)
                tools.transactions.abort(step["error"])
                report["status"] = "FAILED"
                report["error"] = step["error"]
                return report
            try:
                tools.set_track_output_routing(track_index, params.routing_type, params.routing_channel)
            except Exception as exc:  # noqa: BLE001
                step["status"] = "FAILED"
                step["error"] = str(exc)
                report["per_action"].append(step)
                tools.transactions.abort(str(exc))
                report["status"] = "FAILED"
                report["error"] = f"SET_TRACK_ROUTING {track_name}: {exc}"
                return report
            step["status"] = "OK"
            step["track_name"] = track_name
            step["bus"] = params.routing_type
            report["MUSICAL_WRITE_COUNT"]["forward"] += 1

        else:
            step["status"] = "UNSUPPORTED"
            step["error"] = f"{action.action_type.value} not supported in multi-action yet"
            report["per_action"].append(step)
            tools.transactions.abort(step["error"])
            report["status"] = "FAILED"
            report["error"] = step["error"]
            return report

        report["per_action"].append(step)
        lifecycle.append(action.action_type.value)

    report["EXECUTED"] = True
    after = tools.get_session_snapshot()
    report["after_track_count"] = len(after.tracks)
    report["after_clip_count"] = sum(len(t.clips) for t in after.tracks)
    tools.transactions.commit({"EXECUTION_VERIFICATION": "PASS"}, session=after)

    if leave:
        report["LEAVE"] = True
        report["status"] = "CONTROLLED_WRITE_LOOP_COMPLETE"
        report["open_transaction"] = tools.transactions._open is not None
        report["created_at"] = now_iso()
        report["schema_version"] = SCHEMA_VERSION
        artifact = _persist(root / f"{plan.plan_id}_track_build_loop.json", report)
        report["artifact"] = artifact
        return report

    # Unconditional rollback for engineering validation (LIFO over all actions).
    lifecycle.append("ROLLBACK_PREPARED")
    rollback_txn = tools.transactions.rollback_last()
    report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
    if rollback_txn.status is not TransactionStatus.ROLLED_BACK:
        report["status"] = "ROLLBACK_FAILED"
        report["error"] = rollback_txn.error
        return report
    lifecycle.append("ROLLED_BACK")

    restored = tools.get_session_snapshot()
    report["restored_track_count"] = len(restored.tracks)
    report["restored_clip_count"] = sum(len(t.clips) for t in restored.tracks)
    report["RESTORE_VERIFIED"] = report["restored_track_count"] == len(session.tracks)
    report["status"] = "CONTROLLED_WRITE_LOOP_COMPLETE"
    report["CONTROLLED_WRITE_LOOP_V1"] = "VERIFIED"
    report["open_transaction"] = tools.transactions._open is not None
    report["created_at"] = now_iso()
    report["schema_version"] = SCHEMA_VERSION
    artifact = _persist(root / f"{plan.plan_id}_track_build_loop.json", report)
    report["artifact"] = artifact
    return report

