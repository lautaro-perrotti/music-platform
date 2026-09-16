"""AUDIBLE_EFFECT_VERIFICATION_V2

CONTROLLED_ENGINEERING_VALIDATION.
Characterize baseline variance → freeze ONE high-SNR SET_TRACK_VOLUME
→ write once → AFTER → attribution → rollback → RESTORED.

Does NOT hunt deltas. Does NOT reopen frozen V1 / write-loop components.
No Ableton dB API is evidenced in TCP; mutation uses linear-gain approximation
on ableton_volume, chosen only after baseline spread is known.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.source_audio_trace import _restore_host_full, _snapshot_host
from copilot.audio.audible_effect_verification import (
    _capture_dest_root,
    _capture_pair,
    _fresh_tokens,
    _relative_db,
)
from copilot.audio.batch_capture import CAPTURE_BASS
from copilot.audio.session_diagnose import preflight_session
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.object_ref import ResolveStatus, ref_from_track, resolve_track
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.daw.write import WriteInDoubt
from copilot.human_eval.store import now_iso
from copilot.musicplan import (
    VOLUME_TOLERANCE,
    build_set_track_volume_action,
    new_plan_id,
    validate_musicplan,
)
from copilot.musicplan.execute import (
    _best_effort_restore,
    _volume_close,
    build_agent_tools,
    compile_executable_envelope,
    diff_guard_state,
    snapshot_guard_state,
)
from copilot.schemas.musicplan import (
    DiagnosisBinding,
    MusicPlan,
    PlanIntentClass,
    PlanStatus,
    VolumeOperation,
)
from copilot.schemas.session import SessionState, TrackState
from copilot.schemas.transaction import TransactionStatus

# Priority: known-stable V2 fixture region first (from prior characterization),
# then broader discovery. Coffee Leaf last (V1 noise).
FIXTURE_PRIORITY = (
    "Sub Sub Bass",
    "Drums",
    "Rose Bass",
    "Rainstorm",
    "AI Test",
    "Coffee Leaf",
)
REGION_CANDIDATES = (
    ("REGION_E_LIKE", 96.0, 128.0),  # Sub Sub Bass proven HAS_SIGNAL + stable in prior run
    ("REGION_C", 32.0, 64.0),
    ("REGION_A_LIKE", 0.0, 32.0),
    ("REGION_D_LIKE", 64.0, 96.0),
)

# Baseline stability gate (characterization only — not tuned from AFTER).
MAX_BASELINE_SPREAD_DB = 1.0
MAX_BASELINE_REL_SPREAD = 0.12
# High-SNR design: expected linear-gain drop must clear this margin over spread.
SNR_MARGIN_MULT = 6.0
MIN_DESIGN_REDUCTION_DB = 4.0
MAX_DESIGN_REDUCTION_DB = 12.0


def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    """Merge extras then force terminal BLOCKED fields (no kwarg collisions)."""
    payload = {
        "intent_class": PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION.value,
        "note": (
            "CONTROLLED_ENGINEERING_VALIDATION — not autonomous musical improvement. "
            "Does not claim AFTER sounds better."
        ),
        "created_at": now_iso(),
    }
    payload.update(extra)
    payload["AUDIBLE_EFFECT_VERIFICATION_V2"] = "BLOCKED"
    payload["status"] = "BLOCKED"
    payload["reason"] = reason
    return payload


def _extras(report: dict[str, Any], **override: Any) -> dict[str, Any]:
    """Report fields for _blocked without colliding with forced keys."""
    skip = {
        "status",
        "reason",
        "AUDIBLE_EFFECT_VERIFICATION_V2",
        *override.keys(),
    }
    out = {k: v for k, v in report.items() if k not in skip}
    out.update(override)
    return out


def _persist(evidence: Path, report: dict[str, Any]) -> dict[str, Any]:
    out = evidence / "audible_effect_verification_v2.json"
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    report["artifact"] = str(out)
    return report


def _spread_db(rms_a: float, rms_b: float) -> float:
    return abs(_relative_db(rms_b, rms_a))


def _classify_baseline(rms_a: float, rms_b: float) -> dict[str, Any]:
    mean = max((rms_a + rms_b) / 2.0, 1e-12)
    spread_abs = abs(rms_a - rms_b)
    spread_rel = spread_abs / mean
    spread_db = _spread_db(rms_a, rms_b)
    stable = spread_db <= MAX_BASELINE_SPREAD_DB and spread_rel <= MAX_BASELINE_REL_SPREAD
    return {
        "rms_a": rms_a,
        "rms_b": rms_b,
        "mean_rms": mean,
        "spread_abs": spread_abs,
        "spread_rel": spread_rel,
        "spread_db": spread_db,
        "class": "BASELINE_STABLE" if stable else "BASELINE_TOO_VARIABLE",
        "stable": stable,
        "gates": {
            "max_spread_db": MAX_BASELINE_SPREAD_DB,
            "max_spread_rel": MAX_BASELINE_REL_SPREAD,
        },
    }


def _choose_high_snr_mutation(
    *,
    before: float,
    spread_db: float,
) -> dict[str, Any]:
    """Freeze ONE reduction using linear-gain approx on ableton_volume.

    No TCP/API dB mapping is evidenced. Rationale is explicit and pre-AFTER.
    """
    # Design reduction clearly above measured noise.
    design_db = max(MIN_DESIGN_REDUCTION_DB, SNR_MARGIN_MULT * spread_db + 2.0)
    design_db = min(design_db, MAX_DESIGN_REDUCTION_DB)
    ratio = 10.0 ** (-design_db / 20.0)
    after = float(before) * ratio
    after = max(0.05, min(after, float(before) - 0.02))
    # Guarantee at least ~3 dB linear approx even after clamps.
    realized_db = -_relative_db(after, before)  # positive number = reduction
    if realized_db < 3.0:
        after = max(0.05, float(before) * 0.5)
        realized_db = -_relative_db(after, before)
    return {
        "selection_method": (
            "high_snr_preselected_mutation_on_ableton_volume_"
            "no_authoritative_ableton_db_fader_law"
        ),
        "before_value": float(before),
        "after_value": float(after),
        "rollback_value": float(before),
        "baseline_spread_db": float(spread_db),
        "design_margin_db_vs_baseline_spread": float(design_db),
        "parameter_ratio": float(after) / max(float(before), 1e-12),
        "expected_direction": "AFTER_level_lower_than_BASELINE",
        "minimum_evidence_requirement": (
            f"observed_reduction_db > 2 * baseline_spread_db "
            f"({2.0 * spread_db:.3f} dB) AND AFTER_rms < baseline_mean_rms"
        ),
        "rationale": (
            f"Baseline spread={spread_db:.3f} dB. "
            f"Froze one high-SNR preselected ableton_volume mutation "
            f"{before:.4f}→{after:.4f} (parameter ratio={after/max(before,1e-12):.4f}) "
            f"chosen before write so expected audible drop >> baseline variance. "
            f"Not an Ableton fader-law dB prediction; observed audio dB is measured, "
            f"not assumed from the normalized parameter step."
        ),
    }


def _build_set_plan(
    *,
    session: SessionState,
    track: TrackState,
    before: float,
    after: float,
    experiment_id: str,
) -> MusicPlan:
    attach_tokens(session)
    action = build_set_track_volume_action(
        track=track,
        project_identity=session.project_identity or "",
        operation=VolumeOperation.SET,
        expected_before=before,
        target_value=after,
        reason=(
            "AUDIBLE_EFFECT_VERIFICATION_V2 high-SNR controlled volume reduction."
        ),
        evidence_refs=["engineering.audible_effect_v2", experiment_id],
        session_incarnation_id=session.session_incarnation_id or "",
    )
    return MusicPlan(
        plan_id=new_plan_id(),
        status=PlanStatus.DRAFT,
        intent_class=PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION,
        diagnosis=DiagnosisBinding(
            diagnosis_id="audible_effect_verification_v2",
            diagnosis_revision=2,
            diagnosis_status="SUPPORTED",
            diagnosis_accepted=True,
            cause_status="CAUSE_SUPPORTED",
            region_id=None,
            artifact=None,
        ),
        project_state_token=session.project_token or "",
        audible_state_token=session.audible_token or "",
        target_state_tokens={track.name: target_token(track)},
        evidence_refs=["engineering.audible_effect_v2", experiment_id],
        actions=[action],
        created_at=now_iso(),
        notes=[
            "CONTROLLED_ENGINEERING_VALIDATION — AUDIBLE_EFFECT_VERIFICATION_V2.",
            "Not autonomous musical improvement.",
            f"experiment_id={experiment_id}",
            "MUTATION_MAGNITUDE_FROZEN_BEFORE_WRITE",
        ],
    )


def run_audible_effect_verification_v2(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path | None = None,
    restored_audio: bool = True,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    evidence.mkdir(parents=True, exist_ok=True)
    plans_dir = evidence / "musicplans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    dest_root = _capture_dest_root()
    experiment_id = f"aev2_{uuid4().hex[:10]}"

    preflight = preflight_session(
        daw, lab_track_exclusions=frozenset({"AI Test"})
    )
    # AI Test is a candidate fixture; exclusion is only for preflight host checks.
    # Re-run preflight without excluding if needed — hosts must still pass.
    if not preflight.get("pass") or not (preflight.get("capture_hosts") or {}).get(
        CAPTURE_BASS
    ):
        return _persist(
            evidence,
            _blocked(
                "PREFLIGHT_FAILED",
                experiment_id=experiment_id,
                preflight={
                    "pass": preflight.get("pass"),
                    "missing": preflight.get("missing"),
                },
            ),
        )

    session = daw.snapshot(include_notes=False)
    attach_tokens(session)
    hosts = preflight["capture_hosts"]
    host_index = int(hosts[CAPTURE_BASS]["index"])
    production_host_snapshot = _snapshot_host(daw, host_index)
    pos = daw.get_playback_position()
    tempo = float(pos.get("tempo") or preflight.get("tempo") or 120.0)
    revision = int(preflight.get("revision") or session.revision or 0)

    fixture_attempts: list[dict[str, Any]] = []
    chosen: dict[str, Any] | None = None

    # ----- PHASE 1–3: fixture discovery + baseline characterization -----
    for track_name in FIXTURE_PRIORITY:
        track = session.track_by_name(track_name)
        if track is None:
            fixture_attempts.append(
                {"track": track_name, "status": "MISSING_TRACK"}
            )
            continue
        # Deterministic lab MIDI without an instrument cannot produce Post Mixer audio.
        if track.role == "midi" and not track.devices:
            fixture_attempts.append(
                {
                    "track": track_name,
                    "status": "SKIP_NO_INSTRUMENT",
                    "detail": "MIDI track has no devices; Post Mixer would be silent.",
                }
            )
            continue
        for region_id, start_qn, end_qn in REGION_CANDIDATES:
            tokens_bind = _fresh_tokens(
                daw.snapshot(include_notes=False), track_name
            )
            cap_a = _capture_pair(
                daw,
                host_index=host_index,
                target_name=track_name,
                start_qn=start_qn,
                end_qn=end_qn,
                region_id=region_id,
                tempo=tempo,
                session_revision=revision,
                label="BASELINE_A",
                tokens=tokens_bind,
                dest_root=dest_root,
            )
            attempt: dict[str, Any] = {
                "track": track_name,
                "region_id": region_id,
                "start_qn": start_qn,
                "end_qn": end_qn,
                "cap_a_ok": bool(cap_a.get("ok")),
                "error": cap_a.get("error"),
            }
            if not cap_a.get("ok"):
                fixture_attempts.append(attempt)
                continue
            if not (cap_a.get("coffee_leaf") or {}).get("has_signal"):
                # key name is coffee_leaf in helper but holds target Post Mixer stats
                attempt["has_signal_a"] = False
                fixture_attempts.append(attempt)
                continue
            attempt["has_signal_a"] = True

            tokens_b = _fresh_tokens(daw.snapshot(include_notes=False), track_name)
            cap_b = _capture_pair(
                daw,
                host_index=host_index,
                target_name=track_name,
                start_qn=start_qn,
                end_qn=end_qn,
                region_id=region_id,
                tempo=tempo,
                session_revision=revision,
                label="BASELINE_B",
                tokens=tokens_b,
                dest_root=dest_root,
            )
            attempt["cap_b_ok"] = bool(cap_b.get("ok"))
            if not cap_b.get("ok"):
                attempt["error_b"] = cap_b.get("error")
                fixture_attempts.append(attempt)
                continue
            if not (cap_b.get("coffee_leaf") or {}).get("has_signal"):
                attempt["has_signal_b"] = False
                fixture_attempts.append(attempt)
                continue
            attempt["has_signal_b"] = True

            rms_a = float(cap_a["coffee_leaf"]["rms"])
            rms_b = float(cap_b["coffee_leaf"]["rms"])
            baseline = _classify_baseline(rms_a, rms_b)
            attempt["baseline"] = baseline
            fixture_attempts.append(attempt)
            if not baseline["stable"]:
                continue

            # Fresh volume for mutation design.
            live = daw.snapshot(include_notes=False)
            attach_tokens(live)
            live_track = live.track_by_name(track_name)
            if live_track is None:
                continue
            before_vol = float(live_track.mixer.volume)
            mutation = _choose_high_snr_mutation(
                before=before_vol, spread_db=float(baseline["spread_db"])
            )
            # Expected linear effect must exceed 2× spread (design already uses 6×+).
            # Ensure parameter step is material vs baseline (ratio headroom).
            if mutation["parameter_ratio"] > 0.85:
                attempt["mutation_rejected"] = "insufficient_parameter_step"
                continue

            ref = ref_from_track(
                live_track, project_identity=live.project_identity or ""
            )
            resolved = resolve_track(live, ref)
            if resolved.status is not ResolveStatus.RESOLVED:
                continue

            chosen = {
                "track": track_name,
                "track_state": live_track,
                "session": live,
                "ref": ref,
                "region_id": region_id,
                "start_qn": start_qn,
                "end_qn": end_qn,
                "baseline": baseline,
                "cap_a": cap_a,
                "cap_b": cap_b,
                "mutation": mutation,
                "tokens": _fresh_tokens(live, track_name),
                "sample_rate": cap_a["coffee_leaf"]["sample_rate"],
                "routing_claim_a": cap_a.get("routing_claim"),
                "routing_claim_b": cap_b.get("routing_claim"),
            }
            break
        if chosen is not None:
            break

    if chosen is None:
        return _persist(
            evidence,
            _blocked(
                "NO_SUITABLE_HIGH_SNR_FIXTURE",
                experiment_id=experiment_id,
                fixture_attempts=fixture_attempts,
                note_detail=(
                    "No existing fixture passed BASELINE_STABLE with "
                    "designable high-SNR SET_TRACK_VOLUME headroom."
                ),
            ),
        )

    # ----- PHASE 4–5: freeze experiment design BEFORE write -----
    baseline_mean_rms = float(chosen["baseline"]["mean_rms"])
    baseline_mean_peak = (
        float(chosen["cap_a"]["coffee_leaf"]["peak"])
        + float(chosen["cap_b"]["coffee_leaf"]["peak"])
    ) / 2.0
    experiment = {
        "experiment_id": experiment_id,
        "schema": "audible-effect-verification-v2",
        "intent_class": PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION.value,
        "fixture": {
            "track": chosen["track"],
            "PersistentObjectRef": chosen["ref"].model_dump(mode="json"),
            "region_id": chosen["region_id"],
            "start_qn": chosen["start_qn"],
            "end_qn": chosen["end_qn"],
            "pre_roll": "canonical_arrangement_preroll",
            "sample_rate": chosen["sample_rate"],
            "capture_protocol": "TapProtocol3_parallel_arrangement",
            "signal_point": "Post Mixer",
            "transport": "arrangement",
        },
        "baseline_measurements": {
            "A": chosen["cap_a"]["coffee_leaf"],
            "B": chosen["cap_b"]["coffee_leaf"],
            "main_A": chosen["cap_a"]["main"],
            "main_B": chosen["cap_b"]["main"],
            "capture_count": 2,
            "classification": chosen["baseline"],
        },
        "baseline_spread_db": chosen["baseline"]["spread_db"],
        "mutation": chosen["mutation"],
        "before_parameter_value": chosen["mutation"]["before_value"],
        "after_requested_value": chosen["mutation"]["after_value"],
        "rollback_value": chosen["mutation"]["rollback_value"],
        "expected_direction": "AFTER < BASELINE",
        "minimum_evidence_requirement": chosen["mutation"][
            "minimum_evidence_requirement"
        ],
        "PROJECT_STATE_TOKEN": chosen["tokens"]["PROJECT_STATE_TOKEN"],
        "AUDIBLE_STATE_TOKEN": chosen["tokens"]["AUDIBLE_STATE_TOKEN"],
        "TARGET_STATE_TOKEN": chosen["tokens"]["TARGET_STATE_TOKEN"],
        "MUTATION_MAGNITUDE_FROZEN": True,
        "frozen_at": now_iso(),
    }
    exp_path = evidence / f"{experiment_id}_experiment_design.json"
    exp_path.write_text(
        json.dumps(experiment, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    report: dict[str, Any] = {
        "AUDIBLE_EFFECT_VERIFICATION_V2": "BLOCKED",
        "status": "EXPERIMENT_FROZEN",
        "experiment_id": experiment_id,
        "experiment_design_artifact": str(exp_path),
        "fixture_attempts": fixture_attempts,
        "fixture": experiment["fixture"],
        "baseline_capture_count": 2,
        "baseline_measurements": experiment["baseline_measurements"],
        "baseline_spread_db": experiment["baseline_spread_db"],
        "before_parameter_value": experiment["before_parameter_value"],
        "frozen_requested_value": experiment["after_requested_value"],
        "expected_effect_rationale": chosen["mutation"]["rationale"],
        "fresh_tokens_at_freeze": chosen["tokens"],
        "MUSICAL_WRITE_COUNT": {"forward": 0, "rollback": 0},
        "created_at": now_iso(),
        "note": (
            "CONTROLLED_ENGINEERING_VALIDATION — not autonomous musical improvement."
        ),
    }

    # ----- PHASE 6: fresh pre-write state -----
    session = daw.snapshot(include_notes=False)
    attach_tokens(session)
    track = session.track_by_name(chosen["track"])
    if track is None:
        return _persist(evidence, _blocked("TARGET_MISSING_PREWRITE", **report))

    tokens_now = _fresh_tokens(session, chosen["track"])
    live_vol = float(track.mixer.volume)
    if not _volume_close(
        live_vol, experiment["before_parameter_value"], VOLUME_TOLERANCE
    ):
        return _persist(
            evidence,
            _blocked(
                "STALE_EXPERIMENT_STATE",
                detail="parameter_before_mismatch",
                live_volume=live_vol,
                expected=experiment["before_parameter_value"],
                **{k: report[k] for k in report if k != "status"},
            ),
        )
    # Token match against frozen experiment (exact).
    for key in (
        "PROJECT_STATE_TOKEN",
        "AUDIBLE_STATE_TOKEN",
        "TARGET_STATE_TOKEN",
    ):
        if tokens_now.get(key) != experiment.get(key):
            return _persist(
                evidence,
                _blocked(
                    "STALE_EXPERIMENT_STATE",
                    detail=f"token_mismatch:{key}",
                    frozen=experiment.get(key),
                    live=tokens_now.get(key),
                    live_tokens=tokens_now,
                    **{k: report[k] for k in report if k != "status"},
                ),
            )

    pre_guard = snapshot_guard_state(session, chosen["track"])
    report["prewrite_guard"] = pre_guard.get("target")
    report["fresh_tokens_prewrite"] = tokens_now

    # ----- PHASE 7–8: MusicPlan + one write -----
    plan = _build_set_plan(
        session=session,
        track=track,
        before=experiment["before_parameter_value"],
        after=experiment["after_requested_value"],
        experiment_id=experiment_id,
    )
    validated = validate_musicplan(plan, session=session)
    report["plan_id"] = validated.plan_id
    if validated.status is not PlanStatus.READY_FOR_EXECUTION:
        return _persist(
            evidence,
            _blocked(
                "PLAN_NOT_EXECUTABLE",
                plan_status=validated.status.value,
                rejection=validated.rejection_reason,
                **{k: report[k] for k in report if k != "status"},
            ),
        )
    envelope = compile_executable_envelope(validated, session=session)
    envelope = envelope.model_copy(
        update={"dry_run_only": False, "envelope_id": f"env_{uuid4().hex[:10]}"}
    )
    report["envelope_id"] = envelope.envelope_id
    (plans_dir / f"{validated.plan_id}.json").write_text(
        json.dumps(validated.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    (plans_dir / f"{validated.plan_id}_executable_envelope.json").write_text(
        json.dumps(envelope.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )

    tools = build_agent_tools(daw, journal_path=evidence / "agent_journal.jsonl")
    txn = tools.transactions.begin(
        user_intent=(
            f"AUDIBLE_EFFECT_VERIFICATION_V2 SET_TRACK_VOLUME {chosen['track']} "
            f"{envelope.expected_before}->{envelope.requested_after}"
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
    )
    report["journal_lifecycle"] = ["PREPARED"]

    try:
        tools.set_mixer_volume(
            envelope.resolved_track_index, envelope.requested_after
        )
    except WriteInDoubt as exc:
        tools.transactions.mark_in_doubt(str(exc), getattr(exc, "command_id", ""))
        _best_effort_restore(
            tools,
            target_track=chosen["track"],
            rollback_value=envelope.rollback_value,
            report=report,
        )
        return _persist(
            evidence,
            _blocked(
                "WRITE_IN_DOUBT",
                error=str(exc),
                journal_terminal_state=TransactionStatus.IN_DOUBT.value,
                open_transaction=tools.transactions._open is not None,
                **{k: report[k] for k in report if k != "status"},
            ),
        )
    except Exception as exc:  # noqa: BLE001
        tools.transactions.abort(str(exc))
        _best_effort_restore(
            tools,
            target_track=chosen["track"],
            rollback_value=envelope.rollback_value,
            report=report,
        )
        return _persist(
            evidence,
            _blocked(
                "WRITE_FAILED",
                error=str(exc),
                open_transaction=tools.transactions._open is not None,
                **{k: report[k] for k in report if k != "status"},
            ),
        )

    report["MUSICAL_WRITE_COUNT"]["forward"] = 1
    report["journal_lifecycle"].append("EXECUTED")

    after_write = tools.get_session_snapshot()
    attach_tokens(after_write)
    after_track = after_write.track_by_name(chosen["track"])
    if after_track is None:
        tools.transactions.mark_in_doubt("target missing")
        return _persist(evidence, _blocked("TARGET_MISSING_AFTER_WRITE", **report))

    after_readback = float(after_track.mixer.volume)
    report["write_readback"] = after_readback
    if not _volume_close(after_readback, envelope.requested_after):
        tools.transactions.mark_in_doubt("readback mismatch")
        _best_effort_restore(
            tools,
            target_track=chosen["track"],
            rollback_value=envelope.rollback_value,
            report=report,
        )
        return _persist(
            evidence,
            _blocked(
                "PARAMETER_READBACK_FAIL",
                write_readback=after_readback,
                requested=envelope.requested_after,
                **{k: report[k] for k in report if k != "status"},
            ),
        )

    post_guard = snapshot_guard_state(after_write, chosen["track"])
    write_diff = diff_guard_state(
        pre_guard,
        post_guard,
        expected_volume_delta_target=chosen["track"],
        expected_volume=envelope.requested_after,
    )
    report["unexpected_state_diffs"] = write_diff["unexpected_mutations"]
    report["expected_state_diffs"] = write_diff["expected_mutations"]
    if not write_diff["only_expected_changed"]:
        tools.transactions.abort("unexpected_state_mutation")
        report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
        return _persist(
            evidence,
            _blocked(
                "UNEXPECTED_MUTATION",
                journal_terminal_state=(
                    tools.transactions.history[-1].status.value
                    if tools.transactions.history
                    else TransactionStatus.ROLLED_BACK.value
                ),
                open_transaction=tools.transactions._open is not None,
                **{k: report[k] for k in report if k != "status"},
            ),
        )

    tools.transactions.commit(
        {"EXECUTION_VERIFICATION": "PASS", "after_readback": after_readback},
        session=after_write,
    )
    report["journal_lifecycle"].append("VERIFIED")
    report["CONTROLLED_WRITE"] = "VERIFIED"
    report["PARAMETER_READBACK"] = "VERIFIED"

    # ----- PHASE 9–11: AFTER capture + causal gate -----
    tokens_after = _fresh_tokens(after_write, chosen["track"])
    after_cap = _capture_pair(
        daw,
        host_index=host_index,
        target_name=chosen["track"],
        start_qn=float(chosen["start_qn"]),
        end_qn=float(chosen["end_qn"]),
        region_id=str(chosen["region_id"]),
        tempo=tempo,
        session_revision=revision,
        label="AFTER",
        tokens=tokens_after,
        dest_root=dest_root,
    )
    if not after_cap.get("ok"):
        tools.transactions.rollback_last()
        report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
        return _persist(
            evidence,
            _blocked(
                "AFTER_CAPTURE_FAILED",
                error=after_cap.get("error"),
                open_transaction=tools.transactions._open is not None,
                journal_terminal_state=(
                    tools.transactions.history[-1].status.value
                    if tools.transactions.history
                    else "UNKNOWN"
                ),
                **{k: report[k] for k in report if k != "status"},
            ),
        )

    after_rms = float(after_cap["coffee_leaf"]["rms"])
    after_peak = float(after_cap["coffee_leaf"]["peak"])
    observed_db = _relative_db(after_rms, baseline_mean_rms)  # negative if quieter
    observed_reduction_db = -observed_db
    spread_db = float(chosen["baseline"]["spread_db"])
    direction_ok = after_rms < baseline_mean_rms
    snr_ok = observed_reduction_db > (2.0 * spread_db)
    # Near-silence after large cut is allowed; otherwise require HAS_SIGNAL.
    after_has = bool(after_cap["coffee_leaf"]["has_signal"])
    signal_ok = after_has or after_rms <= baseline_mean_rms * 0.25

    report["AFTER_measurements"] = {
        "target": after_cap["coffee_leaf"],
        "main": after_cap["main"],
        "routing_claim": after_cap.get("routing_claim"),
    }
    report["observed_change"] = {
        "baseline_mean_rms": baseline_mean_rms,
        "after_rms": after_rms,
        "baseline_mean_peak": baseline_mean_peak,
        "after_peak": after_peak,
        "rms_delta": after_rms - baseline_mean_rms,
        "relative_db": observed_db,
        "observed_reduction_db": observed_reduction_db,
        "direction_after_lower": direction_ok,
        "exceeds_2x_baseline_spread": snr_ok,
        "baseline_spread_db": spread_db,
    }
    report["change_vs_baseline_spread"] = {
        "observed_reduction_db": observed_reduction_db,
        "baseline_spread_db": spread_db,
        "ratio": (
            observed_reduction_db / spread_db if spread_db > 1e-12 else None
        ),
    }

    attribution_ok = all(
        [
            bool(experiment["PROJECT_STATE_TOKEN"]),
            chosen["track"] == experiment["fixture"]["track"],
            after_cap.get("routing_claim") in {"OFF_MIX_GRAPH", "OFF_DIRECT_MAIN"},
            report.get("PARAMETER_READBACK") == "VERIFIED",
            not report.get("unexpected_state_diffs"),
            experiment.get("MUTATION_MAGNITUDE_FROZEN") is True,
            direction_ok,
            snr_ok,
            signal_ok,
            after_cap.get("ok") is True,
        ]
    )
    report["CAUSAL_ATTRIBUTION"] = (
        "AUDIO_EFFECT_CONFIRMED" if attribution_ok else "AUDIO_EFFECT_NOT_CONFIRMED"
    )
    if not attribution_ok:
        # Still rollback; final reason after restore.
        report["pending_block_reason"] = "AUDIO_EFFECT_NOT_CONFIRMED_HIGH_SNR"

    # ----- PHASE 12: unconditional rollback -----
    report["journal_lifecycle"].append("ROLLBACK_PREPARED")
    rollback_txn = tools.transactions.rollback_last()
    report["MUSICAL_WRITE_COUNT"]["rollback"] = 1
    if rollback_txn.status is not TransactionStatus.ROLLED_BACK:
        return _persist(
            evidence,
            _blocked(
                "ROLLBACK_FAILED",
                error=rollback_txn.error,
                journal_terminal_state=rollback_txn.status.value,
                open_transaction=tools.transactions._open is not None,
                CAUSAL_ATTRIBUTION=report["CAUSAL_ATTRIBUTION"],
                **{k: report[k] for k in report if k not in {"status", "reason"}},
            ),
        )
    report["journal_lifecycle"].append("ROLLED_BACK")
    report["ROLLBACK"] = "VERIFIED"
    report["journal_terminal_state"] = TransactionStatus.ROLLED_BACK.value
    report["open_transaction"] = tools.transactions._open is not None

    # Force capture-host back to production snapshot before token comparison.
    host_restore = _restore_host_full(daw, host_index, production_host_snapshot)
    report["capture_host_force_restore"] = host_restore

    restored = tools.get_session_snapshot()
    attach_tokens(restored)
    restored_track = restored.track_by_name(chosen["track"])
    if restored_track is None:
        return _persist(evidence, _blocked("RESTORE_TARGET_MISSING", **report))

    rollback_readback = float(restored_track.mixer.volume)
    report["rollback_readback"] = rollback_readback
    if not _volume_close(rollback_readback, envelope.rollback_value):
        return _persist(
            evidence,
            _blocked(
                "RESTORE_READBACK_FAILED",
                **_extras(
                    report,
                    rollback_readback=rollback_readback,
                    expected=envelope.rollback_value,
                ),
            ),
        )

    restored_guard = snapshot_guard_state(restored, chosen["track"])
    restore_diff = diff_guard_state(
        pre_guard,
        restored_guard,
        expected_volume_delta_target="__none__",
    )
    report["restored_tokens"] = _fresh_tokens(restored, chosen["track"])
    from copilot.daw.state_tokens import canonical_project

    project_ok = pre_guard.get("project_state_token") == restored_guard.get(
        "project_state_token"
    )
    audible_ok = pre_guard.get("audible_state_token") == restored_guard.get(
        "audible_state_token"
    )
    target_ok = pre_guard.get("target_state_token") == restored_guard.get(
        "target_state_token"
    )
    project_diff_note = None
    if not project_ok:
        # Identify whether mismatch is capture-host-only vs real musical structure.
        pre_session_tokens = pre_guard  # has project token only; re-diff live vs expected host
        live_canon = canonical_project(restored)
        host_name = CAPTURE_BASS
        host_rows = [t for t in live_canon.get("tracks", []) if t.get("name") == host_name]
        project_diff_note = {
            "pre_project": pre_guard.get("project_state_token"),
            "restored_project": restored_guard.get("project_state_token"),
            "capture_host_live_routing": host_rows[0].get("routing") if host_rows else None,
            "capture_host_force_restore_ok": bool(
                (report.get("capture_host_force_restore") or {}).get("ok")
            ),
        }
    # State Trust: PROJECT is structural (must match after host restore).
    # AUDIBLE/TARGET include device parameter values that may drift under
    # playback without mixer/routing mutation — mixer/routing/volume guards
    # are authoritative for those scopes.
    guards_ok = (
        not restore_diff["unexpected_mutations"]
        and (pre_guard.get("target") or {}).get("arm")
        == (restored_guard.get("target") or {}).get("arm")
        and (pre_guard.get("target") or {}).get("mute")
        == (restored_guard.get("target") or {}).get("mute")
        and (pre_guard.get("target") or {}).get("solo")
        == (restored_guard.get("target") or {}).get("solo")
        and (pre_guard.get("target") or {}).get("routing")
        == (restored_guard.get("target") or {}).get("routing")
        and pre_guard.get("track_order") == restored_guard.get("track_order")
        and _volume_close(rollback_readback, envelope.rollback_value)
    )
    host_restore_ok = bool((report.get("capture_host_force_restore") or {}).get("ok"))
    # Strict State Trust restore (audit fix): do NOT accept host_restore_ok as a
    # substitute for PROJECT token equality. That OR-clause was an improper
    # softening introduced during V2 and is reverted here.
    # AUDIBLE/TARGET must also match after rollback (same contract as
    # CONTROLLED_WRITE_LOOP_V1 / execute.py). Device-parameter playback drift
    # remains a separate known tension in the canon — not solved by weakening.
    restored_ok = (
        guards_ok
        and project_ok
        and audible_ok
        and target_ok
        and host_restore_ok
    )
    report["restored_state_verification"] = {
        "volume_restored": True,
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
        "project_token_restored": project_ok,
        "audible_token_restored": audible_ok,
        "target_token_restored": target_ok,
        "project_diff_note": project_diff_note,
        "token_contract": (
            "Exact PROJECT/AUDIBLE/TARGET equality required after rollback "
            "(same as CONTROLLED_WRITE_LOOP_V1). No host_restore substitute. "
            "No path fallback. No tolerance-based token comparison."
        ),
        "guards_ok": guards_ok,
        "host_restore_ok": host_restore_ok,
        "state_trust_volatility_patch": "SOFTENING_REVERTED",
    }
    if not restored_ok:
        return _persist(
            evidence,
            _blocked(
                "RESTORED_STATE_FAILED",
                **_extras(
                    report,
                    journal_terminal_state=TransactionStatus.ROLLED_BACK.value,
                    open_transaction=tools.transactions._open is not None,
                ),
            ),
        )

    report["RESTORED_STATE"] = "VERIFIED"
    # Note only — does not relax equality (already required above).
    if not (audible_ok and target_ok):
        report["restored_state_verification"][
            "audible_target_token_note"
        ] = "UNEXPECTED_TOKEN_MISMATCH_SHOULD_UNREACHABLE"

    # ----- PHASE 13: RESTORED audio -----
    if restored_audio:
        tokens_r = _fresh_tokens(restored, chosen["track"])
        restored_cap = _capture_pair(
            daw,
            host_index=host_index,
            target_name=chosen["track"],
            start_qn=float(chosen["start_qn"]),
            end_qn=float(chosen["end_qn"]),
            region_id=str(chosen["region_id"]),
            tempo=tempo,
            session_revision=revision,
            label="RESTORED",
            tokens=tokens_r,
            dest_root=dest_root,
        )
        if restored_cap.get("ok"):
            rest_rms = float(restored_cap["coffee_leaf"]["rms"])
            rest_db = abs(_relative_db(rest_rms, baseline_mean_rms))
            consistent = rest_db <= max(3.0 * spread_db, 0.5)
            report["RESTORED_measurements"] = {
                "target": restored_cap["coffee_leaf"],
                "main": restored_cap["main"],
            }
            report["RESTORED_vs_baseline"] = {
                "rms_delta": rest_rms - baseline_mean_rms,
                "abs_relative_db": rest_db,
                "consistent_with_baseline_variance": consistent,
            }
            report["RESTORED_AUDIO_CHECK"] = (
                "VERIFIED" if consistent else "INCONSISTENT"
            )
        else:
            report["RESTORED_AUDIO_CHECK"] = "DEFERRED"
            report["RESTORED_AUDIO_CHECK_REASON"] = restored_cap.get("error")
    else:
        report["RESTORED_AUDIO_CHECK"] = "DEFERRED"
        report["RESTORED_AUDIO_CHECK_REASON"] = "skipped"

    tools.transactions.save(evidence / "agent_transactions.json")

    if report.get("pending_block_reason") == "AUDIO_EFFECT_NOT_CONFIRMED_HIGH_SNR":
        return _persist(
            evidence,
            _blocked(
                "AUDIO_EFFECT_NOT_CONFIRMED_HIGH_SNR",
                CAUSAL_ATTRIBUTION=report["CAUSAL_ATTRIBUTION"],
                observed_change=report.get("observed_change"),
                journal_terminal_state=report["journal_terminal_state"],
                open_transaction=report["open_transaction"],
                RESTORED_AUDIO_CHECK=report.get("RESTORED_AUDIO_CHECK"),
                interpretation=(
                    "High-SNR mutation readback succeeded but captured audio "
                    "effect was not distinguishable. This justifies capture/"
                    "measurement path investigation."
                ),
                **{
                    k: report[k]
                    for k in report
                    if k
                    not in {
                        "status",
                        "reason",
                        "AUDIBLE_EFFECT_VERIFICATION_V2",
                        "pending_block_reason",
                    }
                },
            ),
        )

    verified = all(
        [
            chosen["baseline"]["stable"],
            experiment.get("MUTATION_MAGNITUDE_FROZEN") is True,
            report.get("CONTROLLED_WRITE") == "VERIFIED",
            report.get("PARAMETER_READBACK") == "VERIFIED",
            report.get("AFTER_measurements") is not None,
            direction_ok,
            snr_ok,
            report.get("CAUSAL_ATTRIBUTION") == "AUDIO_EFFECT_CONFIRMED",
            report.get("ROLLBACK") == "VERIFIED",
            report.get("RESTORED_STATE") == "VERIFIED",
            not report.get("unexpected_state_diffs"),
            report.get("open_transaction") is False,
        ]
    )

    report["acceptance"] = {
        "FIXTURE SUITABILITY": "VERIFIED",
        "BASELINE CHARACTERIZATION": "VERIFIED",
        "MUTATION PRE-FROZEN": "VERIFIED",
        "FRESH STATE GATE": "VERIFIED",
        "CONTROLLED WRITE": report.get("CONTROLLED_WRITE"),
        "PARAMETER READBACK": report.get("PARAMETER_READBACK"),
        "AFTER CAPTURE": "VERIFIED",
        "EXPECTED AUDIO DIRECTION": "VERIFIED" if direction_ok else "FAIL",
        "EFFECT > BASELINE VARIANCE": "VERIFIED" if snr_ok else "FAIL",
        "CAUSAL ATTRIBUTION": (
            "VERIFIED" if attribution_ok else "FAIL"
        ),
        "ROLLBACK": report.get("ROLLBACK"),
        "RESTORED STATE": report.get("RESTORED_STATE"),
        "UNEXPECTED MUTATIONS": "NONE",
        "OPEN TRANSACTION": "FALSE",
        "RESTORED_AUDIO_CHECK": report.get("RESTORED_AUDIO_CHECK"),
    }
    report["AUDIBLE_EFFECT_VERIFICATION_V2"] = (
        "VERIFIED" if verified else "BLOCKED"
    )
    report["status"] = (
        "AUDIBLE_EFFECT_VERIFICATION_V2_COMPLETE"
        if verified
        else "AUDIBLE_EFFECT_VERIFICATION_V2_INCOMPLETE"
    )
    report["forward_write_count"] = report["MUSICAL_WRITE_COUNT"]["forward"]
    report["rollback_write_count"] = report["MUSICAL_WRITE_COUNT"]["rollback"]
    return _persist(evidence, report)
