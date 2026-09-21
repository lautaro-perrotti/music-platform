"""Read-only producer-analyze.

project-ready → region → capture → isolation → evidence pack → Astra once → gate → restore.

NO WRITE. Statuses are never collapsed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copilot.audio.arrangement_activity import load_arrangement_clips
from copilot.audio.evidence_pack_v1 import build_evidence_pack, persist_evidence_pack
from copilot.audio.fullmix import compute_fullmix_observation
from copilot.audio.generic_source_isolation_v1 import (
    inventory_generic_sources,
    select_activity_region,
)
from copilot.audio.live_capture import AudioAsset
from copilot.audio.lowend_features import compute_lowend_features
from copilot.audio.project_ready_v1 import project_ready
from copilot.audio.session_diagnose import WORKING_COPY_CANDIDATE
from copilot.audio.source_capture_pool_v1 import (
    capture_source_post_mixer_ref,
    capture_sources_post_mixer_batch_refs,
)
from copilot.audio.source_capture_batch_v1 import plan_batches
from copilot.audio.terminal_state_v1 import verify_terminal_state
from copilot.schemas.observation import CaptureView, ObservationSource, SignalPoint
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.audio.cross_project_bootstrap_v1 import retain_tokens
from copilot.daw.state_tokens import attach_tokens
from copilot.human_eval.store import now_iso
from copilot.reasoning.errors import ReasoningFailure
from copilot.reasoning.pipeline import ReasoningResult, reason
from copilot.reasoning.provider import ReasoningProvider, configured_http_provider
from copilot.reasoning.session_astra import ASTRA_TIMEOUT_S
from copilot.schemas.diagnosis import CandidateActionType, DiagnosisStatus, FindingType

MILESTONE = "PRODUCER_ANALYZE_V1"
ARTIFACT = "producer_analyze_v1.json"
PRESERVED_STATUSES = (
    DiagnosisStatus.SUPPORTED.value,
    DiagnosisStatus.WEAKLY_SUPPORTED.value,
    DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
    DiagnosisStatus.NO_ACTION_REQUIRED.value,
    DiagnosisStatus.DIAGNOSIS_UNSTABLE.value,
    "ACTION_NOT_AVAILABLE",
)
NON_VOLUME = frozenset(
    {
        CandidateActionType.SHORTEN_BASS_RELEASE.value,
        CandidateActionType.CHANGE_BASS_NOTE_LENGTH.value,
        CandidateActionType.CHANGE_OCTAVE.value,
        CandidateActionType.REDUCE_LOW_BAND_ENERGY.value,
        CandidateActionType.CHANGE_SOUND_SELECTION.value,
        CandidateActionType.SIDECHAIN.value,
    }
)


def preserve_status(status: str | None) -> str:
    raw = str(status or "")
    if raw in PRESERVED_STATUSES:
        return raw
    return DiagnosisStatus.INSUFFICIENT_EVIDENCE.value


def gate_read_only(
    *,
    diagnosis_status: str | None,
    diagnosis_accepted: bool = False,
    category: str | None = None,
    candidate_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read-only gate. Never collapses WEAKLY_SUPPORTED into INSUFFICIENT_EVIDENCE."""
    status = preserve_status(diagnosis_status)
    actions = list(candidate_actions or [])
    types = [str(item.get("action_type") or "") for item in actions]
    non_volume = [item for item in types if item in NON_VOLUME]
    only_no_change = bool(types) and all(
        item == CandidateActionType.NO_CHANGE.value for item in types
    )
    base = {
        "diagnosis_status": status,
        "diagnosis_accepted": bool(diagnosis_accepted),
        "category": category,
        "candidate_action_types": types,
        "decision": "ABSTAIN",
        "proceed_to_write": False,
        "NO WRITE": True,
    }
    # Never collapse IE / unstable / weak into NO_ACTION_REQUIRED.
    if status in {
        DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
        DiagnosisStatus.DIAGNOSIS_UNSTABLE.value,
        DiagnosisStatus.WEAKLY_SUPPORTED.value,
    }:
        return {
            **base,
            "milestone_status": status,
            "reason": f"status_{status}",
        }
    if status == DiagnosisStatus.NO_ACTION_REQUIRED.value or only_no_change:
        return {
            **base,
            "milestone_status": DiagnosisStatus.NO_ACTION_REQUIRED.value,
            "reason": "no_action_required",
        }
    if status == "ACTION_NOT_AVAILABLE":
        return {**base, "milestone_status": "ACTION_NOT_AVAILABLE", "reason": "action_not_available"}
    if status == DiagnosisStatus.SUPPORTED.value and non_volume:
        return {
            **base,
            "milestone_status": "ACTION_NOT_AVAILABLE",
            "reason": "supported_requires_non_volume_tool",
            "required_tools": non_volume,
        }
    if status == DiagnosisStatus.SUPPORTED.value and "SET_TRACK_VOLUME" not in types:
        return {
            **base,
            "milestone_status": "ACTION_NOT_AVAILABLE",
            "reason": "supported_without_SET_TRACK_VOLUME_candidate",
        }
    if status == DiagnosisStatus.SUPPORTED.value and category != FindingType.LEVEL_IMBALANCE.value:
        return {
            **base,
            "milestone_status": "ACTION_NOT_AVAILABLE",
            "reason": "SET_TRACK_VOLUME only justified for LEVEL_IMBALANCE",
        }
    if status == DiagnosisStatus.SUPPORTED.value:
        return {
            **base,
            "milestone_status": DiagnosisStatus.SUPPORTED.value,
            "reason": "supported_volume_candidate_read_only",
            "write_justified_if_autonomous": True,
        }
    return {
        **base,
        "milestone_status": DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
        "reason": "unrecognized_status",
    }


def apply_reasoning_result(result: ReasoningResult) -> dict[str, Any]:
    """Map Astra output. Rejected reasoning is never reported as musical IE."""
    audit = {
        "accepted": result.accepted,
        "failure": None if result.failure is None else result.failure.value,
        "attempts": result.audit.attempts,
        "raw_hash": result.audit.raw_hash,
        "output_hash": result.audit.output_hash,
        "issues": list(result.audit.issues),
        "timings": dict(result.audit.timings),
        "provider": result.audit.provider,
        "provider_version": result.audit.provider_version,
        "pack_id": result.audit.pack_id,
    }
    output = result.output
    diagnosis = None if output is None else output.model_dump(mode="json")
    if result.accepted and output is not None:
        gate = gate_read_only(
            diagnosis_status=output.status.value
            if hasattr(output.status, "value")
            else str(output.status),
            diagnosis_accepted=True,
            category=output.category.value
            if hasattr(output.category, "value")
            else str(output.category),
            candidate_actions=[
                item.model_dump(mode="json")
                if hasattr(item, "model_dump")
                else dict(item)
                for item in (output.candidate_actions or [])
            ],
        )
        gate["accepted"] = True
        return {
            "status": gate["milestone_status"],
            "diagnosis": diagnosis,
            "gate": gate,
            "reasoning_audit": audit,
        }
    failure = (
        result.failure.value
        if result.failure is not None
        else ReasoningFailure.DIAGNOSIS_UNSTABLE.value
    )
    return {
        "status": DiagnosisStatus.DIAGNOSIS_UNSTABLE.value,
        "diagnosis": diagnosis,
        "gate": {
            "milestone_status": DiagnosisStatus.DIAGNOSIS_UNSTABLE.value,
            "decision": "ABSTAIN",
            "reason": failure,
            "reason_detail": list(result.audit.issues),
            "NO WRITE": True,
            "proceed_to_write": False,
            "accepted": False,
        },
        "reasoning_audit": audit,
    }


def _als_path(session_path: str | None) -> Path | None:
    from copilot.importing.working_copy_manager_v1 import find_working_copy

    return find_working_copy(session_path) or find_working_copy(WORKING_COPY_CANDIDATE)


def _asset_from_wav(
    path: Path,
    *,
    start_qn: float,
    end_qn: float,
    tempo: float,
    source: str,
) -> AudioAsset | None:
    if not path.is_file():
        return None
    import soundfile as sf

    info = sf.info(str(path))
    return AudioAsset(
        capture_id=path.stem,
        raw_file_path=str(path),
        analysis_file_path=str(path),
        file_path=str(path),
        source=source,
        source_type=source,
        requested_start_beat=start_qn,
        requested_end_beat=end_qn,
        start_beat=start_qn,
        end_beat=end_qn,
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        raw_duration=float(info.duration),
        analysis_duration=float(info.duration),
        duration=float(info.duration),
        session_revision=0,
        tempo=tempo,
        capture_view=CaptureView.MASTER_CONTEXT if source == "master" else CaptureView.TRACK_ISOLATED,
        observation_source=ObservationSource.MASTER_CONTEXT
        if source == "master"
        else ObservationSource.TRACK_ISOLATED,
        signal_point=SignalPoint.MAIN_FINAL if source == "master" else SignalPoint.TRACK_POST_MIXER,
        capture_quality="LIMITED",
    )


def observations_from_captures(
    captures: list[dict[str, Any]],
    *,
    region_id: str,
    start_qn: float,
    end_qn: float,
    tempo: float,
) -> dict[str, Any]:
    """Use Main sidecar already recorded with source isolation. No extra hidden capture."""
    main = None
    fullmix = None
    lowend = None
    extra_limitations: list[dict[str, str]] = []
    ok_sources = [row for row in captures if row.get("ok") and row.get("wav_path")]
    for row in captures:
        main_path = row.get("main_wav_path")
        if not main_path:
            continue
        path = Path(str(main_path))
        if not path.is_file():
            continue
        main = {
            "ok": True,
            "signal_class": row.get("main_signal_status"),
            "rms": row.get("main_rms"),
            "audio_sha256": row.get("main_audio_sha256"),
            "path": str(path),
            "quality": "LIMITED",
            "source_ref": "main_sidecar",
        }
        try:
            obs = compute_fullmix_observation(
                path,
                region_id=region_id,
                region_label=region_id,
                audio_sha256=row.get("main_audio_sha256"),
            )
            fullmix = obs.model_dump(mode="json")
            fullmix["ok"] = True
            fullmix["analyzer_id"] = fullmix.get("analyzer_id") or "fullmix-obs-1"
        except Exception as exc:  # noqa: BLE001
            extra_limitations.append(
                {"code": "FULLMIX_FAILED", "detail": str(exc)[:300]}
            )
        break
    if main is None:
        extra_limitations.append(
            {
                "code": "MAIN_CAPTURE_MISSING",
                "detail": "No Main sidecar from source isolation captures",
            }
        )
    if len(ok_sources) >= 2 and main and main.get("path"):
        kick = _asset_from_wav(
            Path(str(ok_sources[0]["wav_path"])),
            start_qn=start_qn,
            end_qn=end_qn,
            tempo=tempo,
            source="kick",
        )
        bass = _asset_from_wav(
            Path(str(ok_sources[1]["wav_path"])),
            start_qn=start_qn,
            end_qn=end_qn,
            tempo=tempo,
            source="bass",
        )
        master = _asset_from_wav(
            Path(str(main["path"])),
            start_qn=start_qn,
            end_qn=end_qn,
            tempo=tempo,
            source="master",
        )
        if kick and bass and master:
            lowend = compute_lowend_features({"master": master, "kick": kick, "bass": bass})
            if not lowend.get("ok"):
                extra_limitations.append(
                    {
                        "code": "LOWEND_NOT_JUSTIFIED",
                        "detail": str(lowend.get("missing") or "lowend features not ok"),
                    }
                )
        else:
            extra_limitations.append(
                {
                    "code": "LOWEND_NOT_JUSTIFIED",
                    "detail": "could not wrap isolated wavs as AudioAsset",
                }
            )
    elif main is not None:
        extra_limitations.append(
            {
                "code": "LOWEND_NOT_JUSTIFIED",
                "detail": "need two isolated Post Mixer captures plus Main",
            }
        )
    return {
        "main_capture": main,
        "fullmix": fullmix,
        "lowend": lowend,
        "extra_limitations": extra_limitations,
    }


def plan_observation(
    session,
    *,
    region_id: str | None = None,
    start_qn: float | None = None,
    end_qn: float | None = None,
) -> dict[str, Any]:
    als = _als_path(session.project_path)
    clips: list[dict[str, Any]] = []
    if als is not None:
        try:
            clips = load_arrangement_clips(als)
        except OSError:
            clips = []
    if start_qn is not None and end_qn is not None:
        region = {
            "id": region_id or f"MANUAL_{int(start_qn)}_{int(end_qn)}",
            "start_qn": float(start_qn),
            "end_qn": float(end_qn),
            "why": "explicit",
        }
    else:
        region = select_activity_region(clips) or {
            "id": region_id or "AUTO_0_32",
            "start_qn": 0.0,
            "end_qn": 32.0,
            "why": "fallback_empty_arrangement",
        }
    isolation = inventory_generic_sources(
        session=session,
        clips=clips,
        start_qn=float(region["start_qn"]),
        end_qn=float(region["end_qn"]),
    )
    return {"region": region, "clips": clips, "isolation": isolation}


def capture_bounded_sources(
    daw: AbletonTcpAdapter,
    *,
    session,
    ready: dict[str, Any],
    isolation: dict[str, Any],
    region: dict[str, Any],
    evidence: Path,
    cancellation: Any | None = None,
) -> list[dict[str, Any]]:
    """Reuse SOURCE_CAPTURE_BATCH_V1. Does not copy batch routing logic."""
    captures: list[dict[str, Any]] = []
    preflight = {
        "pass": True,
        "capture_hosts": ready.get("capture_readiness") or {},
    }
    saved_path = session.project_path
    saved_name = session.project_name
    bound_identity = session.project_identity

    def _annotate(captured: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        attach_tokens(session, path=saved_path, name=saved_name)
        captured["why_included"] = (target.get("capture_eligibility") or {}).get("reason")
        captured["identity_after_capture"] = {
            "project_identity": session.project_identity,
            "bound_identity": bound_identity,
            "match": session.project_identity == bound_identity,
            "project_path": session.project_path,
        }
        return captured

    def _check() -> None:
        if cancellation is not None:
            cancellation.check()

    from copilot.audio.capture_scalability_v2 import (
        discover_capacity,
        ensure_capture_pool,
    )
    from copilot.audio.tap_trust import inventory_taps

    targets = list(isolation.get("bounded_targets") or [])
    inventory: list[dict[str, Any]] = []
    if daw is not None:
        try:
            inventory = inventory_taps(daw)
        except Exception:
            inventory = []
    capacity = discover_capacity(session=session, inventory=inventory)
    ready: list[str] | None = None
    if daw is not None and targets and hasattr(daw, "snapshot"):
        pooled = ensure_capture_pool(
            daw, session, len(targets), capacity=capacity
        )
        session = pooled.get("session") or session
        ready = list(pooled.get("ready_hosts") or [])
        capacity = discover_capacity(
            session=session,
            inventory=inventory_taps(daw),
            ready_hosts=ready,
        )
    groups = plan_batches(targets, session, capacity=capacity, ready_names=ready)
    for group in groups:
        _check()
        attach_tokens(session, path=saved_path, name=saved_name)
        if len(group) == 1:
            target = group[0]
            try:
                captured = capture_source_post_mixer_ref(
                    daw,
                    session=session,
                    preflight=preflight,
                    target_ref=target["ref"],
                    start_qn=float(region["start_qn"]),
                    end_qn=float(region["end_qn"]),
                    region_id=str(region["id"]),
                    tempo=float(session.transport.tempo),
                    dest_root=evidence / "captures",
                )
            except (DawError, OSError, ValueError) as exc:
                captured = {
                    "ok": False,
                    "signal_status": "CAPTURE_FAILED",
                    "error": str(exc),
                    "ref": target.get("ref"),
                }
            captures.append(_annotate(captured, target))
            continue
        try:
            batched = capture_sources_post_mixer_batch_refs(
                daw,
                session=session,
                preflight=preflight,
                target_refs=[item["ref"] for item in group],
                start_qn=float(region["start_qn"]),
                end_qn=float(region["end_qn"]),
                region_id=str(region["id"]),
                tempo=float(session.transport.tempo),
                dest_root=evidence / "captures",
                ready_names=ready,
            )
        except (DawError, OSError, ValueError) as exc:
            batched = [
                {
                    "ok": False,
                    "signal_status": "CAPTURE_FAILED",
                    "error": str(exc),
                    "ref": item.get("ref"),
                }
                for item in group
            ]
        for target, captured in zip(group, batched):
            captures.append(_annotate(captured, target))
    return captures


def producer_analyze(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path,
    region_id: str | None = None,
    start_qn: float | None = None,
    end_qn: float | None = None,
    provider: ReasoningProvider | None = None,
) -> dict[str, Any]:
    ready = project_ready(daw, evidence=evidence)
    if ready.get("PROJECT_READY") != "VERIFIED":
        report = {
            "milestone": MILESTONE,
            "status": "BLOCKED",
            "reason": "PROJECT_NOT_READY",
            "project_ready": ready,
            "ts": now_iso(),
            "NO WRITE": True,
        }
        _persist(report, evidence)
        return report

    session = daw.snapshot(include_notes=False)
    retain_tokens(session)
    planned = plan_observation(
        session, region_id=region_id, start_qn=start_qn, end_qn=end_qn
    )
    region = planned["region"]
    isolation = planned["isolation"]
    captures = capture_bounded_sources(
        daw,
        session=session,
        ready=ready,
        isolation=isolation,
        region=region,
        evidence=evidence,
    )

    derived = observations_from_captures(
        captures,
        region_id=str(region["id"]),
        start_qn=float(region["start_qn"]),
        end_qn=float(region["end_qn"]),
        tempo=float(session.transport.tempo),
    )
    built = build_evidence_pack(
        project_token=session.project_token,
        audible_token=session.audible_token,
        project_identity=session.project_identity or session.project_token,
        region_id=str(region["id"]),
        region=region,
        main_capture=derived.get("main_capture"),
        source_captures=captures,
        fullmix=derived.get("fullmix"),
        lowend=derived.get("lowend"),
        arrangement=isolation,
        routing=ready.get("capture_readiness"),
        extra_limitations=derived.get("extra_limitations") or [],
    )
    persist_evidence_pack(built, evidence)

    http = provider if provider is not None else configured_http_provider()
    diagnosis: dict[str, Any] | None = None
    reasoning_audit: dict[str, Any] | None = None
    if http is None:
        status = "BLOCKED"
        gate = {
            "milestone_status": "BLOCKED",
            "decision": "ABSTAIN",
            "reason": "ASTRA_NOT_CONFIGURED",
            "NO WRITE": True,
            "proceed_to_write": False,
        }
    else:
        try:
            from copilot.schemas.evidence import EvidencePack

            pack = EvidencePack.model_validate(built["pack"])
            result = reason(pack, http, timeout_s=ASTRA_TIMEOUT_S)
            applied = apply_reasoning_result(result)
            diagnosis = applied["diagnosis"]
            gate = applied["gate"]
            status = applied["status"]
            reasoning_audit = applied["reasoning_audit"]
        except Exception as exc:  # noqa: BLE001 — fail closed, never fake a diagnosis
            status = DiagnosisStatus.DIAGNOSIS_UNSTABLE.value
            gate = {
                "milestone_status": status,
                "decision": "ABSTAIN",
                "reason": f"reason_failed:{exc}",
                "NO WRITE": True,
                "proceed_to_write": False,
            }

    session = daw.snapshot(include_notes=False)
    terminal = verify_terminal_state(transport_playing=session.transport.playing)
    report = {
        "milestone": MILESTONE,
        "status": status,
        "ts": now_iso(),
        "project_ready": {"PROJECT_READY": ready.get("PROJECT_READY")},
        "region": region,
        "isolation": {
            "active_count": isolation.get("active_count"),
            "eligible_count": isolation.get("eligible_count"),
        },
        "captures": [
            {
                "ok": row.get("ok"),
                "signal_status": row.get("signal_status") or row.get("signal_class"),
                "error": row.get("error"),
                "ref": (row.get("ref") or {}).get("content_fingerprint"),
            }
            for row in captures
        ],
        "evidence_pack_id": built["pack"]["pack_id"],
        "diagnosis": diagnosis,
        "gate": gate,
        "reasoning_audit": reasoning_audit,
        "terminal": terminal,
        "NO WRITE": True,
        "statuses_preserved": list(PRESERVED_STATUSES),
    }
    if status not in PRESERVED_STATUSES and status != "BLOCKED":
        report["status"] = DiagnosisStatus.INSUFFICIENT_EVIDENCE.value
    _persist(report, evidence)
    return report


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
