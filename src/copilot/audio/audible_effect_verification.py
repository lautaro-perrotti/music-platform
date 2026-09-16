"""AUDIBLE_EFFECT_VERIFICATION_V1

CONTROLLED_ENGINEERING_VALIDATION only.
Proves SET_TRACK_VOLUME produces an observable Coffee Leaf Post Mixer level drop.
Does not evaluate musical quality. Does not KEEP. Unconditional rollback.

Does not modify CONTROLLED_WRITE_LOOP_V1.
Reuses: source_audio_trace capture helpers + musicplan.execute write helpers.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import soundfile as sf

from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    capture_parallel_pass,
    route_host_post_mixer,
)
from copilot.audio.live_capture import AudioCaptureError
from copilot.audio.session_diagnose import preflight_session
from copilot.audio.source_audio_trace import (
    HOST_NAME,
    _build_recorders,
    _restore_host_full,
    _silence_sends,
    _snapshot_host,
    _verify_off_mix_graph,
    analyze_source_event,
)
from copilot.audio.tap_trust import (
    FAILED,
    FINALIZING,
    PREPARED,
    RECORDING,
    VERIFIED,
    CaptureJournal,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.object_ref import ResolveStatus, ref_from_track, resolve_track
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.daw.write import WriteInDoubt
from copilot.human_eval.store import now_iso
from copilot.musicplan import VOLUME_TOLERANCE, validate_musicplan
from copilot.musicplan.execute import (
    _best_effort_restore,
    _volume_close,
    build_agent_tools,
    compile_executable_envelope,
    create_executable_controlled_revision,
    diff_guard_state,
    snapshot_guard_state,
)
from copilot.schemas.musicplan import PlanIntentClass, PlanStatus
from copilot.schemas.session import SessionState
from copilot.schemas.transaction import TransactionStatus

TARGET_TRACK = "Coffee Leaf"
EXPECTED_BEFORE = 0.75
EXPECTED_AFTER = 0.73
DELTA = -0.02
REGION_CANDIDATES = (
    ("REGION_C", 32.0, 64.0),
    ("REGION_A_LIKE", 0.0, 32.0),
    ("REGION_D_LIKE", 64.0, 96.0),
    ("REGION_E_LIKE", 96.0, 128.0),
)
# Pipeline-level evidence from foundation (Kick/Bass 5x); Coffee Leaf still needs
# a local pair check — reused only as supporting context, not as Coffee Leaf proof.
FOUNDATION_REPEATABILITY_ARTIFACT = Path("logs") / "live3r_trust.json"


def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "AUDIBLE_EFFECT_VERIFICATION_V1": "BLOCKED",
        "status": "BLOCKED",
        "reason": reason,
        "intent_class": PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION.value,
        "note": (
            "CONTROLLED_ENGINEERING_VALIDATION — not autonomous musical improvement. "
            "Does not claim 0.73 sounds better."
        ),
        "created_at": now_iso(),
        **extra,
    }
    return payload


def _region_stats(wav_path: Path) -> dict[str, Any]:
    """Full-region level metrics via existing analyze helpers (whole file = region)."""
    samples, sample_rate = sf.read(str(wav_path), always_2d=True)
    mono = np.asarray(samples, dtype=np.float64)
    if mono.ndim > 1:
        if mono.shape[0] <= 8 and mono.shape[0] < mono.shape[1]:
            mono = np.mean(mono, axis=0)
        else:
            mono = np.mean(mono, axis=1)
    duration_s = float(len(mono) / float(sample_rate)) if sample_rate else 0.0
    # Reuse analyze_source_event over the full region window.
    stats = analyze_source_event(
        wav_path,
        event_audio_start_s=0.0,
        event_audio_end_s=max(duration_s, 1e-6),
    )
    return {
        "wav_path": str(wav_path),
        "audio_sha256": stats["audio_sha256"],
        "sample_rate": int(sample_rate),
        "duration_s": duration_s,
        "rms": float(stats["event_rms"]),
        "peak": float(stats["event_peak"]),
        "signal_class": stats["signal_class"],
        "has_signal": bool(stats["has_signal"]),
    }


def _relative_db(after_rms: float, before_rms: float) -> float:
    return 20.0 * math.log10(max(after_rms, 1e-12) / max(before_rms, 1e-12))


def _capture_dest_root() -> Path:
    dest = Path(r"D:\MusicCopilot\captures")
    if not dest.is_dir():
        dest = Path("captures")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _capture_pair(
    daw: AbletonTcpAdapter,
    *,
    host_index: int,
    target_name: str,
    start_qn: float,
    end_qn: float,
    region_id: str,
    tempo: float,
    session_revision: int,
    label: str,
    tokens: dict[str, str],
    dest_root: Path,
) -> dict[str, Any]:
    """Temporary OFF_MIX_GRAPH host route → parallel Main + Post Mixer → restore."""
    pass_id = uuid4().hex[:12]
    journal = CaptureJournal(pass_id)
    journal.record(
        PREPARED,
        region=region_id,
        start_beat=start_qn,
        end_beat=end_qn,
        target=target_name,
        host=HOST_NAME,
        mode="AUDIBLE_EFFECT_VERIFICATION",
        label=label,
        **tokens,
    )
    before_host = _snapshot_host(daw, host_index)
    capture_routing_mutations = 0
    try:
        routed = route_host_post_mixer(daw, host_index, target_name)
        capture_routing_mutations += 1
        send_muts = _silence_sends(daw, host_index)
        capture_routing_mutations += len(send_muts)
        claim = _verify_off_mix_graph(daw, host_index, target_name)
        if claim.get("claim") not in {"OFF_MIX_GRAPH", "OFF_DIRECT_MAIN"}:
            raise AudioCaptureError(
                "CAPTURE_ROUTING_UNSUPPORTED",
                f"claim={claim.get('claim')} detail={claim}",
            )
        if claim.get("through_main"):
            raise AudioCaptureError(
                "AUDIBLE_MIX_RISK",
                f"{HOST_NAME} still routes through Main while tapping {target_name}",
            )
        input_channel = str(routed.get("input_channel") or "")
        recorders = _build_recorders(
            daw,
            host_index=host_index,
            target_name=target_name,
            input_channel=input_channel,
        )
        journal.record(
            RECORDING,
            claim=claim.get("claim"),
            channel=input_channel,
            routing_claim="OFF_MIX_GRAPH",
        )
        one = capture_parallel_pass(
            daw,
            start_beat=start_qn,
            end_beat=end_qn,
            fire_tracks=[],
            tempo=tempo,
            session_revision=session_revision,
            pass_id=pass_id,
            recorders=recorders,
            transport="arrangement",
        )
        journal.record(FINALIZING)
        assets = one.get("assets") or {}
        source_asset = assets.get("source")
        main_asset = assets.get("master")
        if source_asset is None or main_asset is None:
            raise AudioCaptureError(
                "CAPTURE_MISSING_ASSET",
                f"missing source/main keys={list(assets)}",
            )
        source_wav = Path(
            str(
                getattr(source_asset, "analysis_file_path", None)
                or source_asset.file_path
            )
        )
        main_wav = Path(
            str(
                getattr(main_asset, "analysis_file_path", None) or main_asset.file_path
            )
        )
        tag = target_name.replace(" ", "_")
        src_dest = dest_root / f"aev1_{label}_{region_id}_{tag}_{pass_id}.wav"
        main_dest = dest_root / f"aev1_{label}_{region_id}_Main_{tag}_{pass_id}.wav"
        shutil.copy2(source_wav, src_dest)
        shutil.copy2(main_wav, main_dest)
        leaf = _region_stats(src_dest)
        main = _region_stats(main_dest)
        hashes = {
            "coffee_leaf": leaf["audio_sha256"],
            "main": main["audio_sha256"],
        }
        journal.record(VERIFIED, hashes=hashes, label=label)
        return {
            "ok": True,
            "pass_id": pass_id,
            "label": label,
            "region_id": region_id,
            "start_qn": start_qn,
            "end_qn": end_qn,
            "routing_claim": claim.get("claim"),
            "through_main": bool(claim.get("through_main")),
            "input_channel": input_channel,
            "capture_routing_mutations": capture_routing_mutations,
            "signal_point": "Post Mixer",
            "capture_protocol": "TapProtocol3_parallel_arrangement",
            "transport": "arrangement",
            "sample_rate": leaf["sample_rate"],
            "coffee_leaf": leaf,
            "main": main,
            "tokens_at_bind": dict(tokens),
            "journal_pass_id": pass_id,
        }
    except Exception as exc:  # noqa: BLE001
        journal.record(FAILED, error=str(exc))
        return {
            "ok": False,
            "pass_id": pass_id,
            "label": label,
            "error": str(exc),
            "capture_routing_mutations": capture_routing_mutations,
        }
    finally:
        _restore_host_full(daw, host_index, before_host)


def _foundation_repeatability_note() -> dict[str, Any]:
    path = FOUNDATION_REPEATABILITY_ARTIFACT
    if not path.is_file():
        return {"available": False, "artifact": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        block = payload.get("5x REPEATABILITY") or {}
        rms_spread = (block.get("rms_spread") or {}) if isinstance(block, dict) else {}
        return {
            "available": True,
            "artifact": str(path),
            "claim": "pipeline_repeatability_established_for_kick_bass_hosts",
            "rms_spread": rms_spread,
            "note": (
                "Foundation 5x PASS is Kick/Bass host evidence. "
                "Coffee Leaf still requires a local baseline pair in this run."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "artifact": str(path), "error": str(exc)}


def _fresh_tokens(session: SessionState, track_name: str) -> dict[str, str]:
    attach_tokens(session)
    track = session.track_by_name(track_name)
    return {
        "PROJECT_STATE_TOKEN": session.project_token or "",
        "AUDIBLE_STATE_TOKEN": session.audible_token or "",
        "TARGET_STATE_TOKEN": "" if track is None else target_token(track),
    }


def run_audible_effect_verification(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path | None = None,
    target_track: str = TARGET_TRACK,
    expected_before: float = EXPECTED_BEFORE,
    delta: float = DELTA,
    restored_audio: bool = True,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    evidence.mkdir(parents=True, exist_ok=True)
    plans_dir = evidence / "musicplans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    dest_root = _capture_dest_root()

    # ----- PHASE 1: fresh state -----
    preflight = preflight_session(
        daw, lab_track_exclusions=frozenset({"AI Test"})
    )
    if not preflight.get("pass") or not (preflight.get("capture_hosts") or {}).get(
        CAPTURE_BASS
    ):
        return _blocked(
            "PREFLIGHT_FAILED",
            preflight={
                "pass": preflight.get("pass"),
                "missing": preflight.get("missing"),
            },
        )

    session = daw.snapshot(include_notes=False)
    attach_tokens(session)
    track = session.track_by_name(target_track)
    if track is None:
        return _blocked("TARGET_NOT_FOUND", target=target_track)

    ref = ref_from_track(track, project_identity=session.project_identity or "")
    resolved = resolve_track(session, ref)
    if resolved.status is not ResolveStatus.RESOLVED:
        return _blocked(
            "TARGET_UNRESOLVED",
            resolve=resolved.model_dump(mode="json"),
        )

    live_vol = float(track.mixer.volume)
    if not _volume_close(live_vol, expected_before, VOLUME_TOLERANCE):
        return _blocked(
            "PRECONDITION_MISMATCH",
            before_value=live_vol,
            expected_before=expected_before,
        )

    tokens0 = _fresh_tokens(session, target_track)
    hosts = preflight["capture_hosts"]
    host_index = int(hosts[CAPTURE_BASS]["index"])
    pos = daw.get_playback_position()
    tempo = float(pos.get("tempo") or preflight.get("tempo") or 120.0)
    revision = int(preflight.get("revision") or session.revision or 0)

    report: dict[str, Any] = {
        "AUDIBLE_EFFECT_VERIFICATION_V1": "BLOCKED",
        "status": "STARTED",
        "intent_class": PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION.value,
        "target": target_track,
        "fresh_tokens": tokens0,
        "before_parameter_readback": live_vol,
        "prewrite_guard": snapshot_guard_state(session, target_track).get("target"),
        "foundation_repeatability": _foundation_repeatability_note(),
        "created_at": now_iso(),
        "note": (
            "CONTROLLED_ENGINEERING_VALIDATION — not autonomous musical improvement."
        ),
    }

    # ----- PHASE 2–4: region + baseline + repeatability -----
    selected = None
    baseline_a = None
    baseline_b = None
    region_attempts: list[dict[str, Any]] = []

    for region_id, start_qn, end_qn in REGION_CANDIDATES:
        # Ensure host is production-steady before measuring tokens for bind.
        tokens_bind = _fresh_tokens(daw.snapshot(include_notes=False), target_track)
        cap_a = _capture_pair(
            daw,
            host_index=host_index,
            target_name=target_track,
            start_qn=start_qn,
            end_qn=end_qn,
            region_id=region_id,
            tempo=tempo,
            session_revision=revision,
            label="BEFORE_A",
            tokens=tokens_bind,
            dest_root=dest_root,
        )
        region_attempts.append(
            {
                "region_id": region_id,
                "start_qn": start_qn,
                "end_qn": end_qn,
                "ok": cap_a.get("ok"),
                "has_signal": (cap_a.get("coffee_leaf") or {}).get("has_signal"),
                "error": cap_a.get("error"),
            }
        )
        if not cap_a.get("ok"):
            continue
        if not (cap_a.get("coffee_leaf") or {}).get("has_signal"):
            continue

        # Baseline repeat for this region.
        tokens_bind2 = _fresh_tokens(daw.snapshot(include_notes=False), target_track)
        cap_b = _capture_pair(
            daw,
            host_index=host_index,
            target_name=target_track,
            start_qn=start_qn,
            end_qn=end_qn,
            region_id=region_id,
            tempo=tempo,
            session_revision=revision,
            label="BEFORE_B",
            tokens=tokens_bind2,
            dest_root=dest_root,
        )
        if not cap_b.get("ok"):
            region_attempts[-1]["repeat_error"] = cap_b.get("error")
            continue
        if not (cap_b.get("coffee_leaf") or {}).get("has_signal"):
            region_attempts[-1]["repeat_silent"] = True
            continue

        rms_a = float(cap_a["coffee_leaf"]["rms"])
        rms_b = float(cap_b["coffee_leaf"]["rms"])
        spread = abs(rms_a - rms_b)
        # Expected relative change from 0.75→0.73 if level tracks mixer gain linearly.
        expected_rel = abs(delta) / max(expected_before, 1e-9)
        expected_abs = expected_rel * max((rms_a + rms_b) / 2.0, 1e-12)
        region_attempts[-1]["baseline_rms_spread"] = spread
        region_attempts[-1]["expected_abs_effect_approx"] = expected_abs
        # Distinguishable if expected effect clearly exceeds observed baseline spread.
        if expected_abs <= 2.0 * spread + 1e-9:
            region_attempts[-1]["variance_gate"] = "FAIL"
            region_attempts[-1]["baseline_a"] = cap_a["coffee_leaf"]
            region_attempts[-1]["baseline_b"] = cap_b["coffee_leaf"]
            continue

        selected = {
            "region_id": region_id,
            "start_qn": start_qn,
            "end_qn": end_qn,
            "tempo": tempo,
            "transport": "arrangement",
            "pre_roll": "canonical_arrangement_preroll",
            "capture_views": ["MAIN_FINAL", "TRACK_POST_MIXER"],
            "signal_point": "Post Mixer",
            "capture_protocol": "TapProtocol3_parallel_arrangement",
            "sample_rate": cap_a["coffee_leaf"]["sample_rate"],
        }
        baseline_a = cap_a
        baseline_b = cap_b
        report["baseline_variation"] = {
            "rms_a": rms_a,
            "rms_b": rms_b,
            "rms_spread": spread,
            "expected_abs_effect_approx": expected_abs,
            "sufficient": True,
            "method": "paired_BEFORE_A_BEFORE_B",
        }
        break

    report["region_attempts"] = region_attempts
    if selected is None:
        variance_fails = [
            row
            for row in region_attempts
            if row.get("variance_gate") == "FAIL"
        ]
        reason = (
            "AUDIO_EFFECT_UNRESOLVED_FROM_BASELINE_VARIANCE"
            if variance_fails
            else "NO_TRUSTWORTHY_REGION"
        )
        blocked = _blocked(
            reason,
            region_attempts=region_attempts,
            fresh_tokens=tokens0,
            before_parameter_readback=live_vol,
            selected_region=(
                {
                    "region_id": variance_fails[0].get("region_id"),
                    "start_qn": variance_fails[0].get("start_qn"),
                    "end_qn": variance_fails[0].get("end_qn"),
                }
                if variance_fails
                else None
            ),
            baseline_a=(variance_fails[0].get("baseline_a") if variance_fails else None),
            baseline_b=(variance_fails[0].get("baseline_b") if variance_fails else None),
            baseline_rms_spread=(
                variance_fails[0].get("baseline_rms_spread") if variance_fails else None
            ),
            expected_abs_effect_approx=(
                variance_fails[0].get("expected_abs_effect_approx")
                if variance_fails
                else None
            ),
        )
        out = evidence / "audible_effect_verification_v1.json"
        out.write_text(
            json.dumps(blocked, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        blocked["artifact"] = str(out)
        return blocked

    report["selected_region"] = selected
    report["BEFORE_AUDIO"] = {
        "A": baseline_a["coffee_leaf"],
        "B": baseline_b["coffee_leaf"],
        "main_A": baseline_a["main"],
        "main_B": baseline_b["main"],
        "routing_claim_A": baseline_a.get("routing_claim"),
        "routing_claim_B": baseline_b.get("routing_claim"),
    }
    # Mean baseline for comparison.
    before_rms = (
        float(baseline_a["coffee_leaf"]["rms"]) + float(baseline_b["coffee_leaf"]["rms"])
    ) / 2.0
    before_peak = (
        float(baseline_a["coffee_leaf"]["peak"])
        + float(baseline_b["coffee_leaf"]["peak"])
    ) / 2.0
    before_main_rms = (
        float(baseline_a["main"]["rms"]) + float(baseline_b["main"]["rms"])
    ) / 2.0

    # ----- PHASE 5–6: prepare + execute controlled write -----
    session = daw.snapshot(include_notes=False)
    attach_tokens(session)
    track = session.track_by_name(target_track)
    assert track is not None
    live_vol = float(track.mixer.volume)
    if not _volume_close(live_vol, expected_before, VOLUME_TOLERANCE):
        return _blocked(
            "PRECONDITION_MISMATCH_PREWRITE",
            before_value=live_vol,
            expected_before=expected_before,
            selected_region=selected,
        )

    pre_guard = snapshot_guard_state(session, target_track)
    tokens_prewrite = _fresh_tokens(session, target_track)
    report["fresh_tokens_prewrite"] = tokens_prewrite

    tools = build_agent_tools(daw, journal_path=evidence / "agent_journal.jsonl")
    plan = create_executable_controlled_revision(
        session=session,
        track=track,
        delta=delta,
        source_plan_id="audible_effect_verification_v1",
        source_dry_run_envelope_id="n/a",
        expected_before=expected_before,
    )
    plan.notes.append("AUDIBLE_EFFECT_VERIFICATION_V1 capture-around-write.")
    validated = validate_musicplan(plan, session=session)
    report["plan_id"] = validated.plan_id
    if validated.status is not PlanStatus.READY_FOR_EXECUTION:
        return _blocked(
            "PLAN_NOT_EXECUTABLE",
            plan_status=validated.status.value,
            rejection=validated.rejection_reason,
            selected_region=selected,
            **{k: report[k] for k in ("BEFORE_AUDIO", "baseline_variation") if k in report},
        )

    envelope = compile_executable_envelope(validated, session=session)
    report["envelope_id"] = envelope.envelope_id
    report["requested_after"] = envelope.requested_after
    report["rollback_value"] = envelope.rollback_value

    (plans_dir / f"{validated.plan_id}.json").write_text(
        json.dumps(validated.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    (plans_dir / f"{validated.plan_id}_executable_envelope.json").write_text(
        json.dumps(envelope.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )

    txn = tools.transactions.begin(
        user_intent=(
            f"AUDIBLE_EFFECT_VERIFICATION SET_TRACK_VOLUME {target_track} "
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
        write_result = tools.set_mixer_volume(
            envelope.resolved_track_index, envelope.requested_after
        )
    except WriteInDoubt as exc:
        tools.transactions.mark_in_doubt(str(exc), getattr(exc, "command_id", ""))
        _best_effort_restore(
            tools,
            target_track=target_track,
            rollback_value=envelope.rollback_value,
            report=report,
        )
        return _blocked(
            "WRITE_IN_DOUBT",
            error=str(exc),
            selected_region=selected,
            transaction_id=txn.transaction_id,
            journal_terminal_state=TransactionStatus.IN_DOUBT.value,
            open_transaction=tools.transactions._open is not None,
        )
    except Exception as exc:  # noqa: BLE001
        tools.transactions.abort(str(exc))
        _best_effort_restore(
            tools,
            target_track=target_track,
            rollback_value=envelope.rollback_value,
            report=report,
        )
        return _blocked(
            "WRITE_FAILED",
            error=str(exc),
            selected_region=selected,
            transaction_id=txn.transaction_id,
            open_transaction=tools.transactions._open is not None,
        )

    report["journal_lifecycle"].append("EXECUTED")
    after_write = tools.get_session_snapshot()
    attach_tokens(after_write)
    after_track = after_write.track_by_name(target_track)
    if after_track is None:
        tools.transactions.mark_in_doubt("target missing after write")
        return _blocked("TARGET_MISSING_AFTER_WRITE", selected_region=selected)

    after_readback = float(after_track.mixer.volume)
    report["after_parameter_readback"] = after_readback
    if not _volume_close(after_readback, envelope.requested_after):
        tools.transactions.mark_in_doubt("readback mismatch")
        _best_effort_restore(
            tools,
            target_track=target_track,
            rollback_value=envelope.rollback_value,
            report=report,
        )
        return _blocked(
            "PARAMETER_READBACK_FAIL",
            after_parameter_readback=after_readback,
            requested=envelope.requested_after,
            selected_region=selected,
        )

    post_write_guard = snapshot_guard_state(after_write, target_track)
    write_diff = diff_guard_state(
        pre_guard,
        post_write_guard,
        expected_volume_delta_target=target_track,
        expected_volume=envelope.requested_after,
    )
    report["unexpected_state_diffs"] = write_diff["unexpected_mutations"]
    report["expected_state_diffs"] = write_diff["expected_mutations"]
    if not write_diff["only_expected_changed"]:
        tools.transactions.abort("unexpected_state_mutation_after_write")
        return _blocked(
            "UNEXPECTED_MUTATION",
            unexpected_state_diffs=write_diff["unexpected_mutations"],
            selected_region=selected,
            journal_terminal_state=(
                tools.transactions.history[-1].status.value
                if tools.transactions.history
                else TransactionStatus.ROLLED_BACK.value
            ),
            open_transaction=tools.transactions._open is not None,
        )

    tools.transactions.commit(
        {
            "EXECUTION_VERIFICATION": "PASS",
            "after_readback": after_readback,
        },
        session=after_write,
    )
    report["journal_lifecycle"].append("VERIFIED")
    report["CONTROLLED_WRITE"] = "VERIFIED"
    report["PARAMETER_READBACK"] = "VERIFIED"

    # ----- PHASE 7: AFTER capture -----
    tokens_after = _fresh_tokens(after_write, target_track)
    after_cap = _capture_pair(
        daw,
        host_index=host_index,
        target_name=target_track,
        start_qn=float(selected["start_qn"]),
        end_qn=float(selected["end_qn"]),
        region_id=str(selected["region_id"]),
        tempo=tempo,
        session_revision=revision,
        label="AFTER",
        tokens=tokens_after,
        dest_root=dest_root,
    )
    if not after_cap.get("ok"):
        # Still must rollback.
        tools.transactions.rollback_last()
        return _blocked(
            "AFTER_CAPTURE_FAILED",
            error=after_cap.get("error"),
            selected_region=selected,
            after_parameter_readback=after_readback,
            BEFORE_AUDIO=report.get("BEFORE_AUDIO"),
            rollback_attempted=True,
            journal_terminal_state=(
                tools.transactions.history[-1].status.value
                if tools.transactions.history
                else "UNKNOWN"
            ),
            open_transaction=tools.transactions._open is not None,
        )

    report["AFTER_AUDIO"] = {
        "coffee_leaf": after_cap["coffee_leaf"],
        "main": after_cap["main"],
        "routing_claim": after_cap.get("routing_claim"),
    }
    after_rms = float(after_cap["coffee_leaf"]["rms"])
    after_peak = float(after_cap["coffee_leaf"]["peak"])
    after_main_rms = float(after_cap["main"]["rms"])
    level_delta_rms = after_rms - before_rms
    level_delta_db = _relative_db(after_rms, before_rms)
    spread = float(report["baseline_variation"]["rms_spread"])
    direction_ok = after_rms < before_rms and abs(level_delta_rms) > 2.0 * spread
    signal_both = bool(after_cap["coffee_leaf"]["has_signal"]) and bool(
        baseline_a["coffee_leaf"]["has_signal"]
    )

    report["audio_comparison"] = {
        "before_rms": before_rms,
        "after_rms": after_rms,
        "before_peak": before_peak,
        "after_peak": after_peak,
        "rms_delta": level_delta_rms,
        "relative_db": level_delta_db,
        "main_before_rms": before_main_rms,
        "main_after_rms": after_main_rms,
        "main_rms_delta": after_main_rms - before_main_rms,
        "direction_after_lower": after_rms < before_rms,
        "distinguishable_from_baseline": abs(level_delta_rms) > 2.0 * spread,
        "expected_audio_direction": "VERIFIED" if direction_ok else "FAIL",
    }

    # ----- PHASE 9 attribution (computed; rollback still unconditional) -----
    attribution_ok = all(
        [
            bool(tokens_prewrite.get("PROJECT_STATE_TOKEN")),
            target_track == TARGET_TRACK or target_track == report["target"],
            selected is not None,
            after_cap.get("routing_claim") in {"OFF_MIX_GRAPH", "OFF_DIRECT_MAIN"},
            baseline_a.get("routing_claim") in {"OFF_MIX_GRAPH", "OFF_DIRECT_MAIN"},
            report.get("PARAMETER_READBACK") == "VERIFIED",
            not report.get("unexpected_state_diffs"),
            signal_both,
            direction_ok,
            after_cap.get("ok") is True,
            baseline_a.get("ok") is True,
        ]
    )
    report["CAUSAL_ATTRIBUTION"] = (
        "AUDIO_EFFECT_CONFIRMED" if attribution_ok else "AUDIO_EFFECT_NOT_CONFIRMED"
    )

    # ----- PHASE 10: unconditional rollback -----
    report["journal_lifecycle"].append("ROLLBACK_PREPARED")
    rollback_txn = tools.transactions.rollback_last()
    if rollback_txn.status is not TransactionStatus.ROLLED_BACK:
        return _blocked(
            "ROLLBACK_FAILED",
            error=rollback_txn.error,
            CAUSAL_ATTRIBUTION=report["CAUSAL_ATTRIBUTION"],
            audio_comparison=report.get("audio_comparison"),
            selected_region=selected,
            journal_terminal_state=rollback_txn.status.value,
            open_transaction=tools.transactions._open is not None,
        )
    report["journal_lifecycle"].append("ROLLED_BACK")

    restored = tools.get_session_snapshot()
    attach_tokens(restored)
    restored_track = restored.track_by_name(target_track)
    if restored_track is None:
        return _blocked("RESTORE_TARGET_MISSING", selected_region=selected)

    rollback_readback = float(restored_track.mixer.volume)
    report["rollback_readback"] = rollback_readback
    if not _volume_close(rollback_readback, envelope.rollback_value):
        return _blocked(
            "RESTORE_READBACK_FAILED",
            rollback_readback=rollback_readback,
            expected=envelope.rollback_value,
            selected_region=selected,
        )

    restored_guard = snapshot_guard_state(restored, target_track)
    restore_diff = diff_guard_state(
        pre_guard,
        restored_guard,
        expected_volume_delta_target="__none__",
    )
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
    }
    restored_ok = (
        not restore_diff["unexpected_mutations"]
        and all(
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
        )
    )
    if not restored_ok:
        return _blocked(
            "RESTORED_STATE_FAILED",
            restored_state_verification=report["restored_state_verification"],
            selected_region=selected,
            CAUSAL_ATTRIBUTION=report["CAUSAL_ATTRIBUTION"],
            journal_terminal_state=TransactionStatus.ROLLED_BACK.value,
            open_transaction=tools.transactions._open is not None,
        )

    report["ROLLBACK"] = "VERIFIED"
    report["RESTORED_STATE"] = "VERIFIED"
    report["journal_terminal_state"] = TransactionStatus.ROLLED_BACK.value
    report["open_transaction"] = tools.transactions._open is not None

    # ----- PHASE 11: optional restored audio -----
    if restored_audio:
        tokens_restored = _fresh_tokens(restored, target_track)
        restored_cap = _capture_pair(
            daw,
            host_index=host_index,
            target_name=target_track,
            start_qn=float(selected["start_qn"]),
            end_qn=float(selected["end_qn"]),
            region_id=str(selected["region_id"]),
            tempo=tempo,
            session_revision=revision,
            label="RESTORED",
            tokens=tokens_restored,
            dest_root=dest_root,
        )
        if restored_cap.get("ok"):
            rest_rms = float(restored_cap["coffee_leaf"]["rms"])
            rest_spread_ok = abs(rest_rms - before_rms) <= max(3.0 * spread, 1e-6)
            report["RESTORED_AUDIO_CHECK"] = {
                "status": "PERFORMED",
                "coffee_leaf": restored_cap["coffee_leaf"],
                "main": restored_cap["main"],
                "vs_before_rms_delta": rest_rms - before_rms,
                "consistent_with_baseline_variation": rest_spread_ok,
            }
        else:
            report["RESTORED_AUDIO_CHECK"] = {
                "status": "DEFERRED",
                "reason": restored_cap.get("error") or "capture_failed",
            }
    else:
        report["RESTORED_AUDIO_CHECK"] = {"status": "DEFERRED", "reason": "skipped"}

    tools.transactions.save(evidence / "agent_transactions.json")

    verified = all(
        [
            report.get("BEFORE_AUDIO") is not None,
            report.get("baseline_variation", {}).get("sufficient") is True,
            report.get("CONTROLLED_WRITE") == "VERIFIED",
            report.get("PARAMETER_READBACK") == "VERIFIED",
            report.get("AFTER_AUDIO") is not None,
            report.get("audio_comparison", {}).get("expected_audio_direction")
            == "VERIFIED",
            report.get("CAUSAL_ATTRIBUTION") == "AUDIO_EFFECT_CONFIRMED",
            report.get("ROLLBACK") == "VERIFIED",
            report.get("RESTORED_STATE") == "VERIFIED",
            not report.get("unexpected_state_diffs"),
            report.get("open_transaction") is False,
        ]
    )

    report["acceptance"] = {
        "BEFORE AUDIO": "VERIFIED",
        "BASELINE REPEATABILITY": "SUFFICIENT",
        "CONTROLLED WRITE": report.get("CONTROLLED_WRITE"),
        "PARAMETER READBACK": report.get("PARAMETER_READBACK"),
        "AFTER AUDIO": "VERIFIED" if report.get("AFTER_AUDIO") else "FAIL",
        "EXPECTED AUDIO DIRECTION": report.get("audio_comparison", {}).get(
            "expected_audio_direction"
        ),
        "CAUSAL ATTRIBUTION": (
            "VERIFIED"
            if report.get("CAUSAL_ATTRIBUTION") == "AUDIO_EFFECT_CONFIRMED"
            else "FAIL"
        ),
        "ROLLBACK": report.get("ROLLBACK"),
        "RESTORED STATE": report.get("RESTORED_STATE"),
        "UNEXPECTED MUTATIONS": "NONE"
        if not report.get("unexpected_state_diffs")
        else "PRESENT",
        "OPEN TRANSACTION": "FALSE" if not report.get("open_transaction") else "TRUE",
    }
    report["AUDIBLE_EFFECT_VERIFICATION_V1"] = "VERIFIED" if verified else "BLOCKED"
    report["status"] = (
        "AUDIBLE_EFFECT_VERIFICATION_COMPLETE"
        if verified
        else "AUDIBLE_EFFECT_NOT_CONFIRMED"
    )
    report["AUDIO_EFFECT_CONFIRMED"] = bool(attribution_ok)

    out = evidence / "audible_effect_verification_v1.json"
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    report["artifact"] = str(out)
    return report
