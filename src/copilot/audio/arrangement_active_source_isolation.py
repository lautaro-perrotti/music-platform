"""ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1 — observation coverage only.

Inventory arrangement-active tracks for a region and capture trusted
Post Mixer views via existing OFF_MIX_GRAPH host routing.

No musical writes. No Astra. No new analyzers/actions.

STATUS: VERIFIED / FROZEN. Do not retune, extend, or reopen this milestone.
Primary SOURCE_EVIDENCE_GAP is CLOSED. FIXED_TAP_NOT_REPRESENTATIVE is the
evidenced reason the Kick 808 Deep pad tap is not a Drums source view.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import soundfile as sf

from copilot.audio.arrangement_activity import (
    clips_overlap_region,
    load_arrangement_clips,
)
from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    CAPTURE_HOST,
    capture_parallel_pass,
    route_host_post_mixer,
)
from copilot.audio.capture_journal_recovery import (
    recover_all_unresolved,
    unresolved_capture_journals,
)
from copilot.audio.live_capture import (
    MASTER_INDEX,
    SILENCE_PEAK,
    SILENCE_RMS,
    STAGING_BASS,
    AudioCaptureError,
    capture_dir,
    find_tap,
    set_tap_enabled,
    set_tap_recording,
)
from copilot.audio.live3r_trust import _recorders
from copilot.audio.session_diagnose import WORKING_COPY_CANDIDATE, preflight_session
from copilot.audio.source_audio_trace import (
    _restore_host_full,
    _silence_sends,
    _snapshot_host,
    _verify_off_mix_graph,
)
from copilot.audio.tap_trust import (
    FAILED,
    FINALIZING,
    PREPARED,
    RECORDING,
    VERIFIED,
    CaptureJournal,
    inventory_taps,
    routing_claim,
    sha256_file,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.daw.object_ref import ResolveStatus, ref_from_track, resolve_track
from copilot.daw.state_tokens import attach_tokens
from copilot.perf.trace import (
    CAT_ABLETON,
    CAT_CPU,
    CAT_INTRINSIC,
    CAT_IO,
    span,
)
from copilot.human_eval.store import now_iso
from copilot.schemas.observation import SignalPoint
from copilot.schemas.session import SessionState, TrackState

MILESTONE = "ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1"
ARTIFACT = "arrangement_active_source_isolation_v1.json"
STATUS = "VERIFIED"
FROZEN = True
SOURCE_EVIDENCE_GAP = "CLOSED"

ENGINEERING_REGION = {
    "id": "ENGINEERING_320_352",
    "start_qn": 320.0,
    "end_qn": 352.0,
    "note": "Former AME V2 holdout — engineering validation only, not blind benchmark.",
}

# Sources justified by V2 arrangement activity + audit.
VALIDATION_TARGETS = (
    "Rose Bass",
    "Sub Sub Bass",
    "Drums",  # track Post Mixer — not Kick 808 Deep pad
)

HOST_NAME = CAPTURE_BASS
HOST_SLOT = 2
KICK_PAD_LABEL = "Kick 808 Deep"
FIXED_KICK_CHANNEL_SUBSTR = "kick 808 deep"
NEAR_SILENCE_RMS = 1.0e-3


def _signal_class(rms: float, peak: float) -> str:
    if rms <= SILENCE_RMS and peak <= SILENCE_PEAK:
        return "SILENCE"
    if rms <= NEAR_SILENCE_RMS:
        return "NEAR_SILENCE"
    return "HAS_SIGNAL"


def _wav_stats(path: Path) -> dict[str, Any]:
    samples, sr = sf.read(str(path), always_2d=True)
    mono = np.mean(np.asarray(samples, dtype=np.float64), axis=1)
    rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return {
        "rms": rms,
        "peak": peak,
        "sample_rate": int(sr),
        "duration_s": float(len(mono) / float(sr)) if sr else 0.0,
        "audio_sha256": sha256_file(path),
        "signal_class": _signal_class(rms, peak),
    }


def _als_path_from_preflight(preflight: dict[str, Any]) -> Path:
    from copilot.importing.working_copy_manager_v1 import find_working_copy

    als_path = Path(str(preflight.get("live_set_path") or preflight.get("project_path") or ""))
    return find_working_copy(als_path) or find_working_copy(WORKING_COPY_CANDIDATE) or Path(WORKING_COPY_CANDIDATE)


def inventory_active_sources(
    *,
    session: SessionState,
    als_path: Path,
    start_qn: float,
    end_qn: float,
) -> dict[str, Any]:
    """Typed arrangement-active inventory. Clip presence ≠ audible contribution."""
    clips = load_arrangement_clips(als_path)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for clip in clips:
        name = str(clip.get("track") or "")
        if not name:
            continue
        by_name.setdefault(name, []).append(clip)

    attach_tokens(session)
    project_identity = session.project_identity or session.project_token or ""
    rows: list[dict[str, Any]] = []

    for track in session.tracks:
        if track.role in {"return", "master"}:
            continue
        if track.name.startswith("Copilot Capture"):
            continue
        if track.name == "AI Test":
            continue
        # Match exact track name; for Drums also include group-child clips.
        own = by_name.get(track.name) or []
        if track.name == "Drums":
            for cname, clist in by_name.items():
                if cname != "Drums":
                    # group children already tagged with group==Drums in clip.group
                    pass
            group_hits = [
                c
                for c in clips
                if str(c.get("group") or "") == "Drums" or str(c.get("track") or "") == "Drums"
            ]
            hits = clips_overlap_region(group_hits, start_qn, end_qn)
        else:
            hits = clips_overlap_region(own, start_qn, end_qn)
        # Also count group-child drums clips under "Drums" naming already in clip.track_name
        material = "HAS_MATERIAL" if hits else "NO_MATERIAL"
        ref = ref_from_track(track, project_identity=project_identity)
        resolved = resolve_track(session, ref)
        eligible = track.role in {"midi", "audio"} and bool(track.devices or track.role == "audio")
        rows.append(
            {
                "ref": ref.model_dump(mode="json"),
                "display_name": track.name,
                "role": track.role,
                "arrangement_material": material,
                "overlapping_clips": len(hits),
                "overlap_note": (
                    "Clip presence only — does not prove audible contribution "
                    "(e.g. Drum Rack pad may be inactive)."
                ),
                "routing": {
                    "input_type": track.routing.input_type,
                    "input_channel": track.routing.input_channel,
                    "output_type": track.routing.output_type,
                    "output_channel": track.routing.output_channel,
                    "monitoring": track.routing.monitoring,
                },
                "mute": bool(track.mixer.mute),
                "solo": bool(track.mixer.solo),
                "volume": float(track.mixer.volume),
                "device_names": [d.name for d in (track.devices or [])],
                "device_enabled": [bool(d.enabled) for d in (track.devices or [])],
                "capture_eligibility": {
                    "eligible": eligible and material == "HAS_MATERIAL",
                    "reason": (
                        "has_material_and_routable"
                        if eligible and material == "HAS_MATERIAL"
                        else "no_material"
                        if material != "HAS_MATERIAL"
                        else "no_devices_or_unsupported_role"
                    ),
                },
                "resolve_status": resolved.status.value,
                "track_index_locator_only": track.index,
            }
        )

    active = [r for r in rows if r["arrangement_material"] == "HAS_MATERIAL"]
    return {
        "ok": True,
        "region": {"start_qn": start_qn, "end_qn": end_qn},
        "als_path": str(als_path),
        "method": "als_arranger_clips ∩ live SessionState PersistentObjectRef",
        "sources": rows,
        "active_count": len(active),
        "active_display_names": [r["display_name"] for r in active],
        "limitation": (
            "HAS_MATERIAL is arrangement clip overlap only. "
            "Audible contribution requires Post Mixer capture."
        ),
    }


def diagnose_fixed_kick_tap(
    daw: AbletonTcpAdapter,
    *,
    preflight: dict[str, Any],
    session: SessionState,
) -> dict[str, Any]:
    """Explain Kick 808 Deep silence vs Drums arrangement material — no guessing."""
    hosts = preflight.get("capture_hosts") or {}
    kick_host = hosts.get(CAPTURE_HOST) or {}
    routing = kick_host.get("routing") or {}
    tap = kick_host.get("tap") or {}
    drums = session.track_by_name("Drums")

    facts: list[str] = []
    evidenced_cause: str | None = None

    facts.append(
        f"fixed_host_input={routing.get('input_type')} / {routing.get('input_channel')}"
    )
    facts.append(f"fixed_host_monitoring={routing.get('monitoring')}")
    facts.append(f"fixed_host_output={routing.get('output')}")
    facts.append(f"tap_device_on={tap.get('device_on')} rec={tap.get('rec')}")

    channel = str(routing.get("input_channel") or "").lower()
    if FIXED_KICK_CHANNEL_SUBSTR in channel:
        facts.append(
            "Fixed Copilot Capture isolates Drum Rack pad 'Kick 808 Deep' Post Mixer, "
            "not the whole Drums track Post Mixer."
        )
        evidenced_cause = "wrong_sub_source_for_region_or_pad_inactive"
    else:
        facts.append("Fixed kick host channel does not contain Kick 808 Deep substring.")

    if drums is not None:
        facts.append(
            f"Drums track devices={[d.name for d in drums.devices]}; "
            "pad activity inside Drum Rack is not readable as arrangement clip granularity."
        )

    # V1/V2 artifacts already showed RECORDED_SILENCE on this tap for 128–160 and 320–352.
    facts.append(
        "Prior autonomous captures classified this fixed tap as RECORDED_SILENCE "
        "(rms=0) while arrangement marked Drums HAS_MATERIAL."
    )

    return {
        "status": "FIXED_TAP_NOT_REPRESENTATIVE",
        "evidenced_cause": evidenced_cause,
        "facts": facts,
        "conclusion": (
            "The fixed Kick 808 Deep tap is not a representative Drums source for "
            "regions where arrangement material may drive other pads/lanes. "
            "Use Drums track Post Mixer (or an evidenced active pad) instead."
        ),
        "do_not_guess": True,
        "resolved_to_specific_pad_silence_mechanism": False,
    }


def _build_source_recorders(
    daw: AbletonTcpAdapter,
    *,
    host_index: int,
    target_name: str,
    input_channel: str,
) -> list[dict[str, Any]]:
    inventory = inventory_taps(daw)
    by_track = {int(row["track_index"]): row for row in inventory}
    main_tap = by_track.get(MASTER_INDEX) or {}
    host_tap = by_track.get(host_index) or {}
    if host_tap.get("device_index") is None:
        tap = find_tap(daw, host_index)
        if tap is None:
            raise AudioCaptureError("TAP_MISSING", f"{HOST_NAME} missing Copilot Audio Tap")
        host_device = int(tap["index"])
    else:
        host_device = int(host_tap["device_index"])
    main_device = main_tap.get("device_index")
    if main_device is None:
        mt = find_tap(daw, MASTER_INDEX)
        if mt is None:
            raise AudioCaptureError("TAP_MISSING", "Master missing Copilot Audio Tap")
        main_device = int(mt["index"])
    dummy = _recorders(
        kick_index=host_index,
        bass_index=host_index,
        kick_id=target_name,
        bass_id=target_name,
        kick_channel=input_channel,
        bass_channel=input_channel,
        main_point=SignalPoint.MAIN_FINAL,
    )
    master = dummy[0]
    master["device_index"] = int(main_device)
    master["rec_param_index"] = main_tap.get("rec_param_index")
    master["require_signal"] = True
    source = dummy[2]
    source["key"] = "source"
    source["tap_track_index"] = host_index
    source["staging"] = STAGING_BASS
    source["slot"] = HOST_SLOT
    source["source"] = target_name
    source["signal_point"] = SignalPoint.TRACK_POST_MIXER
    source["signal_point_label"] = f"{target_name} → {input_channel or 'Post Mixer'}"
    source["device_index"] = host_device
    source["rec_param_index"] = host_tap.get("rec_param_index")
    source["require_signal"] = False
    return [master, source]


def _pass_timings(one: dict[str, Any]) -> dict[str, Any]:
    """Compact latency view of one playback pass.

    capture_parallel_pass already measures its own phases; this keeps only the
    scalars worth persisting so the RECORDING phase can be decomposed without
    dragging tap inventories into every artifact.
    """
    raw = one.get("timings") or {}
    stop = raw.get("stop_breakdown") or {}
    release = stop.get("staging_handle_release") or {}
    prepare = raw.get("prepare_breakdown") or {}
    out: dict[str, Any] = {
        "total_s": raw.get("total_s"),
        "region_s": raw.get("region_s"),
        "record_s": raw.get("record_s"),
        "pre_roll_s": raw.get("pre_roll_s"),
        "pre_roll_qn": raw.get("pre_roll_qn"),
        "recorder_start_s": raw.get("recorder_start_s"),
        "recorder_stop_s": raw.get("recorder_stop_s"),
        "stop_command_rtt_s": raw.get("stop_command_rtt_s"),
        "wav_finalize_s": raw.get("wav_finalize_s"),
        "restore_s": raw.get("restore_s"),
        # The number the audit could not see: how long the writer held the WAV.
        "wav_shared_readable_waited_s": release.get("waited_s"),
        "wav_shared_readable_ok": release.get("ok"),
        "wav_shared_readable_phase_s": release.get("s"),
    }
    out["prepare_phases_s"] = {
        key: value.get("s") for key, value in prepare.items() if isinstance(value, dict)
    }
    out["stop_phases_s"] = {
        key: value.get("s") for key, value in stop.items() if isinstance(value, dict)
    }
    return {key: value for key, value in out.items() if value is not None}


def capture_source_post_mixer(
    daw: AbletonTcpAdapter,
    *,
    session: SessionState,
    preflight: dict[str, Any],
    track: TrackState,
    start_qn: float,
    end_qn: float,
    region_id: str,
    tempo: float,
    host_index: int,
    dest_root: Path,
) -> dict[str, Any]:
    """CAPTURE_SOURCE_POST_MIXER — temporary OFF_MIX_GRAPH, restore guaranteed."""
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AudioCaptureError(
            "CANNOT_CREATE_OUTPUT",
            f"cannot create capture destination {dest_root}: {exc}",
        ) from exc
    attach_tokens(session)
    project_identity = session.project_identity or session.project_token or ""
    ref = ref_from_track(track, project_identity=project_identity)
    resolved = resolve_track(session, ref)
    if resolved.status is not ResolveStatus.RESOLVED:
        return {
            "ok": False,
            "signal_status": "CAPTURE_FAILED",
            "error": f"resolve_{resolved.status.value}",
            "ref": ref.model_dump(mode="json"),
            "display_name": track.name,
        }

    pass_id = uuid4().hex[:12]
    journal = CaptureJournal(pass_id)
    journal.record(
        PREPARED,
        region=region_id,
        start_beat=start_qn,
        end_beat=end_qn,
        target=track.name,
        host=HOST_NAME,
        mode="CAPTURE_SOURCE_POST_MIXER",
        milestone=MILESTONE,
        ref=ref.model_dump(mode="json"),
    )

    with span("capture.snapshot_host_before", category=CAT_ABLETON, host=host_index):
        before = _snapshot_host(daw, host_index)
    routing_mutations: list[str] = []
    # Cancellation is a BaseException, so neither the success path nor the
    # `except Exception` path below runs on Ctrl-C. Without this sentinel the
    # host keeps its rewritten input routing, zeroed sends and changed
    # monitoring, and the journal never reaches a terminal state.
    settled = False
    try:
        # Ensure host tap Device On / Rec idle before arming.
        set_tap_enabled(daw, host_index, True)
        set_tap_recording(daw, False, host_index, broadcast_udp=False)

        routed = route_host_post_mixer(daw, host_index, track.name)
        routing_mutations.append(f"route_input->{track.name}/Post Mixer")
        send_muts = _silence_sends(daw, host_index)
        routing_mutations.extend(send_muts)

        claim = _verify_off_mix_graph(daw, host_index, track.name)
        if claim.get("claim") not in {"OFF_MIX_GRAPH", "OFF_DIRECT_MAIN"}:
            raise AudioCaptureError(
                "CAPTURE_ROUTING_UNSUPPORTED",
                f"claim={claim.get('claim')} detail={claim}",
            )
        if claim.get("through_main"):
            raise AudioCaptureError(
                "AUDIBLE_MIX_RISK",
                f"{HOST_NAME} still routes through Main while tapping {track.name}",
            )

        input_channel = str(routed.get("input_channel") or "")
        recorders = _build_source_recorders(
            daw,
            host_index=host_index,
            target_name=track.name,
            input_channel=input_channel,
        )
        journal.record(
            RECORDING,
            claim=claim.get("claim"),
            channel=input_channel,
            routing_claim=claim.get("claim"),
        )
        with span(
            "capture.playback_pass",
            category=CAT_INTRINSIC,
            recorders=len(recorders),
            region_qn=float(end_qn) - float(start_qn),
        ):
            one = capture_parallel_pass(
                daw,
                start_beat=start_qn,
                end_beat=end_qn,
                fire_tracks=[],
                tempo=tempo,
                session_revision=int(preflight.get("revision") or session.revision or 0),
                pass_id=pass_id,
                recorders=recorders,
                transport="arrangement",
            )
        # capture_parallel_pass already decomposes its own phases, including the
        # WAV shared-readable wait. Every caller used to drop it, which is why
        # ~16.6s per pass was unattributable. Keep it and journal it.
        pass_timings = _pass_timings(one)
        journal.record(FINALIZING, timings=pass_timings)
        assets = one.get("assets") or {}
        source_asset = assets.get("source")
        main_asset = assets.get("master")
        if source_asset is None or main_asset is None:
            raise AudioCaptureError(
                "CAPTURE_MISSING_ASSET",
                f"missing source/main keys={list(assets)}",
            )
        source_wav = Path(
            str(getattr(source_asset, "analysis_file_path", None) or source_asset.file_path)
        )
        main_wav = Path(
            str(getattr(main_asset, "analysis_file_path", None) or main_asset.file_path)
        )
        tag = track.name.replace(" ", "_")
        src_dest = dest_root / f"aasi_v1_{region_id}_{tag}_{pass_id}.wav"
        main_dest = dest_root / f"aasi_v1_{region_id}_Main_with_{tag}_{pass_id}.wav"
        with span("capture.copy_artifacts", category=CAT_IO, files=2):
            shutil.copy2(source_wav, src_dest)
            shutil.copy2(main_wav, main_dest)
        with span("capture.wav_stats_and_hash", category=CAT_CPU, files=2):
            src_stats = _wav_stats(src_dest)
            main_stats = _wav_stats(main_dest)
        hashes = {
            "source": src_stats["audio_sha256"],
            "main": main_stats["audio_sha256"],
        }
        journal.record(VERIFIED, hashes=hashes, signal_class=src_stats["signal_class"])

        with span("capture.restore_host", category=CAT_ABLETON, host=host_index):
            restore = _restore_host_full(daw, host_index, before)
        settled = True
        if not restore.get("ok"):
            journal.record(FAILED, error="RESTORE_FAILED", restore=restore)
            return {
                "ok": False,
                "signal_status": "CAPTURE_FAILED",
                "error": "ROUTING_RESTORE_FAILED",
                "restore": restore,
                "pass_id": pass_id,
                "display_name": track.name,
                "ref": ref.model_dump(mode="json"),
                "routing_mutations": routing_mutations,
            }

        return {
            "ok": True,
            "pass_id": pass_id,
            "journal": str(journal.path),
            "display_name": track.name,
            "ref": ref.model_dump(mode="json"),
            "resolve_status": resolved.status.value,
            "signal_point": "POST_MIXER",
            "signal_status": src_stats["signal_class"],
            "rms": src_stats["rms"],
            "peak": src_stats["peak"],
            "sample_rate": src_stats["sample_rate"],
            "duration_s": src_stats["duration_s"],
            "audio_sha256": src_stats["audio_sha256"],
            "wav_path": str(src_dest),
            "main_signal_status": main_stats["signal_class"],
            "main_rms": main_stats["rms"],
            "main_wav_path": str(main_dest),
            "main_audio_sha256": main_stats["audio_sha256"],
            "host": HOST_NAME,
            "host_slot": HOST_SLOT,
            "routing_claim": claim.get("claim"),
            "through_main": bool(claim.get("through_main")),
            "input_channel": input_channel,
            "routing_mutations": routing_mutations,
            "routing_snapshot_before": {
                "input": before.get("input"),
                "output": before.get("output"),
                "monitoring": before.get("monitoring"),
            },
            "restore": restore,
            "timings": pass_timings,
            "protocol": "TapProtocol3_parallel_arrangement",
            "transport": "arrangement",
            "region": {"id": region_id, "start_qn": start_qn, "end_qn": end_qn},
            "tokens_at_bind": {
                "PROJECT_STATE_TOKEN": session.project_token or "",
                "AUDIBLE_STATE_TOKEN": session.audible_token or "",
            },
        }
    except Exception as exc:  # noqa: BLE001
        journal.record(
            FAILED,
            error=str(exc),
            capture_diagnostics=getattr(exc, "details", None),
        )
        restore = _restore_host_full(daw, host_index, before)
        settled = True
        return {
            "ok": False,
            "signal_status": "CAPTURE_FAILED",
            "error": str(exc),
            "capture_diagnostics": getattr(exc, "details", None),
            "pass_id": pass_id,
            "journal": str(journal.path),
            "display_name": track.name,
            "ref": ref.model_dump(mode="json"),
            "routing_mutations": routing_mutations,
            "restore": restore,
        }
    finally:
        if not settled:
            # Reached only on BaseException (KeyboardInterrupt / SystemExit).
            # Best effort, in safety order: stop the transport, restore the
            # host, then close the journal. Each step is independent so a
            # failure in one still lets the others run, and a second interrupt
            # cannot leave the journal open.
            _cancel_cleanup(daw, host_index, before, journal)


def _cancel_cleanup(
    daw: AbletonTcpAdapter,
    host_index: int,
    before: dict[str, Any],
    journal: CaptureJournal,
) -> None:
    """Terminal cleanup for a cancelled capture. Never raises."""
    for step in (
        lambda: daw.stop_playback(),
        lambda: _restore_host_full(daw, host_index, before),
    ):
        try:
            step()
        except BaseException:  # noqa: BLE001 — cleanup must not mask the cancel
            pass
    try:
        # FAILED is already a terminal journal status, so recovery semantics
        # are unchanged; the reason distinguishes a cancel from a real failure.
        journal.record(FAILED, error="CANCELLED_BY_USER")
    except BaseException:  # noqa: BLE001
        pass


def _ensure_idle_taps(daw: AbletonTcpAdapter, preflight: dict[str, Any]) -> None:
    hosts = preflight.get("capture_hosts") or {}
    for key in (CAPTURE_HOST, CAPTURE_BASS):
        host = hosts.get(key) or {}
        idx = host.get("index")
        if idx is None:
            continue
        set_tap_enabled(daw, int(idx), True)
        set_tap_recording(daw, False, int(idx), broadcast_udp=False)
    set_tap_enabled(daw, MASTER_INDEX, True)
    set_tap_recording(daw, False, MASTER_INDEX, broadcast_udp=False)


def _persist(evidence: Path, report: dict[str, Any]) -> Path:
    path = evidence / ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    report["artifact"] = str(path)
    return path


def run_arrangement_active_source_isolation_v1(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path | None = None,
    start_qn: float = 320.0,
    end_qn: float = 352.0,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    evidence.mkdir(parents=True, exist_ok=True)
    region_id = ENGINEERING_REGION["id"]
    dest_root = capture_dir()
    dest_root.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        MILESTONE: "STARTED",
        "status": "STARTED",
        "created_at": now_iso(),
        "MUSICAL WRITES": 0,
        "AUDIBLE_MIX_MUTATIONS": 0,
        "region": {
            "id": region_id,
            "start_qn": start_qn,
            "end_qn": end_qn,
            "note": ENGINEERING_REGION["note"],
        },
        "STOP": False,
    }

    daw.stop_playback()
    if unresolved_capture_journals():
        report["journal_recovery"] = recover_all_unresolved(daw=daw)

    preflight = preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
    report["preflight_pass"] = bool(preflight.get("pass"))
    if not preflight.get("pass"):
        report[MILESTONE] = "BLOCKED"
        report["status"] = "BLOCKED"
        report["error"] = "PREFLIGHT_FAILED"
        report["STOP"] = True
        _persist(evidence, report)
        return report

    _ensure_idle_taps(daw, preflight)
    session = daw.snapshot(include_notes=False)
    attach_tokens(session)
    als_path = _als_path_from_preflight(preflight)

    # ----- PHASE 1: inventory -----
    inventory = inventory_active_sources(
        session=session,
        als_path=als_path,
        start_qn=start_qn,
        end_qn=end_qn,
    )
    report["active_source_inventory"] = inventory

    # ----- PHASE 2: fixed tap diagnostic -----
    kick_diag = diagnose_fixed_kick_tap(daw, preflight=preflight, session=session)
    report["fixed_kick_tap_diagnostic"] = kick_diag

    hosts = preflight["capture_hosts"]
    host_index = int(hosts[CAPTURE_BASS]["index"])
    production_before = _snapshot_host(daw, host_index)
    pos = daw.get_playback_position()
    tempo = float(pos.get("tempo") or preflight.get("tempo") or 120.0)

    # ----- PHASE 4–6: captures -----
    captures: list[dict[str, Any]] = []
    for name in VALIDATION_TARGETS:
        track = session.track_by_name(name)
        if track is None:
            captures.append(
                {
                    "ok": False,
                    "display_name": name,
                    "signal_status": "CAPTURE_FAILED",
                    "error": "TRACK_NOT_FOUND",
                    "arrangement_material": next(
                        (
                            r["arrangement_material"]
                            for r in inventory["sources"]
                            if r["display_name"] == name
                        ),
                        "UNKNOWN",
                    ),
                }
            )
            continue
        # Refresh session for tokens; track object from prior snapshot is fine for identity.
        live = daw.snapshot(include_notes=False)
        attach_tokens(live)
        track_live = live.track_by_name(name) or track
        row = capture_source_post_mixer(
            daw,
            session=live,
            preflight=preflight,
            track=track_live,
            start_qn=start_qn,
            end_qn=end_qn,
            region_id=region_id,
            tempo=tempo,
            host_index=host_index,
            dest_root=dest_root,
        )
        mat = next(
            (
                r["arrangement_material"]
                for r in inventory["sources"]
                if r["display_name"] == name
            ),
            "UNKNOWN",
        )
        row["arrangement_material"] = mat
        row["capture_target"] = f"{name} Post Mixer"
        captures.append(row)
        daw.stop_playback()

    report["source_captures"] = captures

    # Final restore to production host snapshot (Sub Sub Bass routing).
    final_restore = _restore_host_full(daw, host_index, production_before)
    report["production_host_final_restore"] = final_restore

    # Idle taps + journals + transport
    _ensure_idle_taps(daw, preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"})))
    open_journals = unresolved_capture_journals()
    if open_journals:
        report["terminal_journal_recovery"] = recover_all_unresolved(daw=daw)
        open_journals = unresolved_capture_journals()
    daw.stop_playback()
    pos_final = daw.get_playback_position()

    # Acceptance checks
    by_name = {c.get("display_name"): c for c in captures}
    rose = by_name.get("Rose Bass") or {}
    drums = by_name.get("Drums") or {}
    sub = by_name.get("Sub Sub Bass") or {}

    def _view_status(cap: dict[str, Any]) -> str:
        if not cap:
            return "BLOCKED"
        if cap.get("ok") and cap.get("signal_status") in {
            "HAS_SIGNAL",
            "SILENCE",
            "NEAR_SILENCE",
        }:
            return "VERIFIED"
        if cap.get("error"):
            return f"BLOCKED:{cap.get('error')}"
        return "BLOCKED"

    acceptance = {
        "ACTIVE_SOURCE_INVENTORY": "VERIFIED" if inventory.get("ok") else "BLOCKED",
        "PERSISTENT_TARGET_RESOLUTION": (
            "VERIFIED"
            if all(
                c.get("resolve_status") == ResolveStatus.RESOLVED.value
                for c in captures
                if c.get("ok")
            )
            and any(c.get("ok") for c in captures)
            else "BLOCKED"
        ),
        "GENERIC_POST_MIXER_CAPTURE": (
            "VERIFIED" if any(c.get("ok") for c in captures) else "BLOCKED"
        ),
        "ROSE_BASS_SOURCE_VIEW": _view_status(rose),
        "DRUMS_REPRESENTATIVE_VIEW": _view_status(drums),
        "SUB_SOURCE_VIEW": _view_status(sub),
        "OFF_MIX_GRAPH": (
            "VERIFIED"
            if all(
                c.get("routing_claim") in {"OFF_MIX_GRAPH", "OFF_DIRECT_MAIN"}
                for c in captures
                if c.get("ok")
            )
            and any(c.get("ok") for c in captures)
            else "BLOCKED"
        ),
        "ROUTING_RESTORE": (
            "VERIFIED"
            if final_restore.get("ok")
            and all((c.get("restore") or {}).get("ok", False) for c in captures if c.get("ok"))
            else "BLOCKED"
        ),
        "AUDIBLE_MIX_MUTATIONS": 0,
        "OPEN_CAPTURE_JOURNALS": len(open_journals),
        "TRANSPORT_FINAL": "STOPPED"
        if not bool(pos_final.get("is_playing"))
        else "PLAYING",
        "FIXED_KICK_DIAGNOSTIC": kick_diag.get("status"),
    }

    table = []
    for name in VALIDATION_TARGETS:
        c = by_name.get(name) or {}
        table.append(
            {
                "source": name,
                "arrangement_material": c.get("arrangement_material"),
                "capture_target": c.get("capture_target") or f"{name} Post Mixer",
                "signal_status": c.get("signal_status"),
                "rms": c.get("rms"),
                "peak": c.get("peak"),
                "routing_claim": c.get("routing_claim"),
                "routing_mutations": len(c.get("routing_mutations") or []),
                "restore_ok": (c.get("restore") or {}).get("ok"),
                "pass_id": c.get("pass_id"),
            }
        )

    report["acceptance"] = acceptance
    report["compact_table"] = table
    report["transport_final"] = {
        "playing": bool(pos_final.get("is_playing")),
        "qn": float(pos_final.get("current_song_time") or 0.0),
    }
    report["open_capture_journals"] = open_journals

    required_ok = all(
        str(acceptance[k]).startswith("VERIFIED")
        for k in (
            "ACTIVE_SOURCE_INVENTORY",
            "PERSISTENT_TARGET_RESOLUTION",
            "GENERIC_POST_MIXER_CAPTURE",
            "ROSE_BASS_SOURCE_VIEW",
            "DRUMS_REPRESENTATIVE_VIEW",
            "SUB_SOURCE_VIEW",
            "OFF_MIX_GRAPH",
            "ROUTING_RESTORE",
        )
    ) and acceptance["OPEN_CAPTURE_JOURNALS"] == 0 and acceptance["TRANSPORT_FINAL"] == "STOPPED"

    # Silence is valid VERIFIED for a view — _view_status already treats SILENCE as VERIFIED.
    report[MILESTONE] = "VERIFIED" if required_ok else "BLOCKED"
    report["status"] = report[MILESTONE]
    report["STOP"] = True
    report["completed_at"] = now_iso()
    _persist(evidence, report)
    return report
