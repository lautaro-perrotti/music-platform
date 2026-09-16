"""AUTONOMOUS_MUSICAL_EVALUATION_V3 — one new blind evaluation.

Uses frozen ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1 for Post Mixer views of
arrangement-active sources. Does not recycle A/B/C, prior holdouts, or
ENGINEERING_320_352. SET_TRACK_VOLUME only. Safe abstention is success.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.arrangement_active_source_isolation import (
    FROZEN as ISOLATION_FROZEN,
    HOST_NAME,
    STATUS as ISOLATION_STATUS,
    VALIDATION_TARGETS,
    _als_path_from_preflight,
    _ensure_idle_taps,
    _restore_host_full,
    _snapshot_host,
    capture_source_post_mixer,
    inventory_active_sources,
)
from copilot.audio.arrangement_activity import (
    BAR_QN,
    MIN_OVERLAP_QN,
    WINDOW_QN,
    _track_clips,
    intersect_intervals,
    load_arrangement_clips,
    merge_intervals,
    overlap_duration,
)
from copilot.audio.autonomous_musical_evaluation_v2 import (
    map_gate_to_milestone,
    pre_run_restore_check,
)
from copilot.audio.batch_capture import CAPTURE_BASS, CAPTURE_HOST
from copilot.audio.capture_journal_recovery import unresolved_capture_journals
from copilot.audio.first_autonomous_musical_improvement_v1 import (
    NON_VOLUME_ACTION_TYPES,
    PRODUCTION_ACTION,
    _execute_one_volume_write,
    _mixer_snapshot,
    _resolve_volume_target,
    _rollback_volume,
    evaluate_action_gate,
)
from copilot.audio.fullmix import compute_fullmix_observation
from copilot.audio.live_capture import AudioAsset, capture_dir, observe_asset
from copilot.audio.lowend_features import compute_lowend_features
from copilot.audio.session_diagnose import WORKING_COPY_CANDIDATE, preflight_session
from copilot.audio.session_run1 import _jsonable, _observation_card
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens, target_token
from copilot.human_eval.store import now_iso
from copilot.musicplan import (
    abstain_from_diagnosis,
    build_set_track_volume_action,
    new_plan_id,
)
from copilot.musicplan.execute import build_agent_tools
from copilot.reasoning.from_dsp import pack_from_lowend_features
from copilot.reasoning.from_fullmix import merge_lowend_and_fullmix
from copilot.reasoning.pipeline import reason
from copilot.reasoning.provider import configured_http_provider
from copilot.reasoning.schema import ANALYSIS_VERSION, SCHEMA_VERSION
from copilot.schemas.diagnosis import DiagnosisStatus, FindingType
from copilot.schemas.evidence import (
    CaptureQuality,
    EntityKind,
    EntityRef,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)
from copilot.schemas.musicplan import (
    DiagnosisBinding,
    MusicPlan,
    PlanIntentClass,
    PlanStatus,
    VolumeOperation,
)
from copilot.schemas.observation import CaptureView, SignalPoint
from copilot.schemas.session import SessionState

MILESTONE = "AUTONOMOUS_MUSICAL_EVALUATION_V3"
ARTIFACT = "autonomous_musical_evaluation_v3.json"
ACCEPTANCE_ARTIFACT = "autonomous_musical_evaluation_v3_acceptance.json"
HOLDOUT_ARTIFACT = "ame_v3_holdout_region.json"

CONTROLLED_ENGINEERING = "CLOSED / FROZEN"
ISOLATION_MILESTONE = "ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1 = VERIFIED / FROZEN"
FAMI_V1_STATUS = "INSUFFICIENT_EVIDENCE"
AME_V2_STATUS = "INSUFFICIENT_EVIDENCE"
SOURCE_EVIDENCE_GAP = "CLOSED"

DRUMS_NAME = "Drums"
ROSE_NAME = "Rose Bass"
SUB_NAME = "Sub Sub Bass"
RELEVANT_SOURCES = VALIDATION_TARGETS  # Drums, Rose Bass, Sub Sub Bass

# Explicit non-independent regions (calibration / engineering / prior evals).
FORBIDDEN_QN_RANGES: tuple[tuple[float, float, str], ...] = (
    (0.0, 32.0, "AUDIBLE_EFFECT_REGION_A_LIKE"),
    (32.0, 64.0, "RUN1_REGION_C_CAUSAL_AUDIBLE"),
    (64.0, 96.0, "AUDIBLE_EFFECT_REGION_D_LIKE"),
    (96.0, 128.0, "RUN1_REGION_B"),
    (128.0, 160.0, "FAMI_V1_HOLDOUT_128_160"),
    (256.0, 288.0, "RUN1_REGION_A"),
    (320.0, 352.0, "AME_V2_AND_SOURCE_ISOLATION_ENGINEERING"),
)

INDEPENDENCE_SCAN_GLOBS = (
    "session_run1.json",
    "session_run1_fullmix.json",
    "first_autonomous_musical_improvement_v1.json",
    "fami_v1_holdout_region.json",
    "autonomous_musical_evaluation_v2.json",
    "ame_v2_holdout_region.json",
    "arrangement_active_source_isolation_v1.json",
    "audible_effect_verification*.json",
    "aev2_*.json",
    "musicplan_v1*.json",
    "controlled_write*.json",
)

SUCCESS_MODES = frozenset(
    {
        "AUTONOMOUS_IMPROVEMENT_VERIFIED",
        "INSUFFICIENT_EVIDENCE",
        "NO_ACTION_REQUIRED",
        "ACTION_NOT_AVAILABLE",
        "AUTONOMOUS_CHANGE_ROLLED_BACK",
        "DIAGNOSIS_UNSTABLE",
        "NO_INDEPENDENT_ACTIVE_HOLDOUT",
    }
)


def _overlaps(start: float, end: float, lo: float, hi: float) -> bool:
    return start < hi and end > lo


def forbidden_hit(start_qn: float, end_qn: float) -> str | None:
    for lo, hi, label in FORBIDDEN_QN_RANGES:
        if _overlaps(start_qn, end_qn, lo, hi):
            return label
    return None


def _relevant_intervals(clips: list[dict[str, Any]]) -> dict[str, list[tuple[float, float]]]:
    return {
        DRUMS_NAME: merge_intervals(_track_clips(clips, DRUMS_NAME, grouped=True)),
        ROSE_NAME: merge_intervals(_track_clips(clips, ROSE_NAME, grouped=False)),
        SUB_NAME: merge_intervals(_track_clips(clips, SUB_NAME, grouped=False)),
    }


def window_interaction(
    intervals: dict[str, list[tuple[float, float]]],
    start_qn: float,
    end_qn: float,
) -> dict[str, Any]:
    drums = intervals[DRUMS_NAME]
    rose = intervals[ROSE_NAME]
    sub = intervals[SUB_NAME]
    drums_qn = overlap_duration(drums, start_qn, end_qn)
    rose_qn = overlap_duration(rose, start_qn, end_qn)
    sub_qn = overlap_duration(sub, start_qn, end_qn)
    drums_rose = overlap_duration(intersect_intervals(drums, rose), start_qn, end_qn)
    drums_sub = overlap_duration(intersect_intervals(drums, sub), start_qn, end_qn)
    rose_sub = overlap_duration(intersect_intervals(rose, sub), start_qn, end_qn)
    all_three = overlap_duration(
        intersect_intervals(intersect_intervals(drums, rose), sub),
        start_qn,
        end_qn,
    )
    pairwise = max(drums_rose, drums_sub, rose_sub)
    simultaneous_sources = 0
    if all_three >= MIN_OVERLAP_QN:
        simultaneous_sources = 3
    elif pairwise >= MIN_OVERLAP_QN:
        simultaneous_sources = 2
    interacting = []
    if drums_qn >= MIN_OVERLAP_QN:
        interacting.append(DRUMS_NAME)
    if rose_qn >= MIN_OVERLAP_QN:
        interacting.append(ROSE_NAME)
    if sub_qn >= MIN_OVERLAP_QN:
        interacting.append(SUB_NAME)
    return {
        "start_qn": start_qn,
        "end_qn": end_qn,
        "drums_overlap_qn": drums_qn,
        "rose_bass_overlap_qn": rose_qn,
        "sub_sub_bass_overlap_qn": sub_qn,
        "simultaneous_drums_rose_qn": drums_rose,
        "simultaneous_drums_sub_qn": drums_sub,
        "simultaneous_rose_sub_qn": rose_sub,
        "simultaneous_all_three_qn": all_three,
        "simultaneous_sources": simultaneous_sources,
        "max_pairwise_simultaneous_qn": pairwise,
        "present_relevant_sources": interacting,
        "useful": simultaneous_sources >= 2,
    }


def select_holdout_from_clips(
    clips: list[dict[str, Any]],
    *,
    extra_forbidden: list[tuple[float, float, str]] | None = None,
) -> dict[str, Any]:
    """Arrangement-activity only. No DSP. Prefer multi-source coincidence."""
    intervals = _relevant_intervals(clips)
    end = 0.0
    for spans in intervals.values():
        if spans:
            end = max(end, max(item[1] for item in spans))
    forbidden = list(FORBIDDEN_QN_RANGES) + list(extra_forbidden or [])
    scanned: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    start = 0.0
    while start + WINDOW_QN <= max(end, WINDOW_QN) + 1e-9:
        end_qn = start + WINDOW_QN
        row = window_interaction(intervals, start, end_qn)
        hit = None
        for lo, hi, label in forbidden:
            if _overlaps(start, end_qn, lo, hi):
                hit = label
                break
        row["forbidden"] = hit
        scanned.append(row)
        if row["useful"] and hit is None:
            eligible.append(row)
        start += BAR_QN * 8  # bar-aligned 32-qn windows only; do not slide into used edges

    if not eligible:
        return {
            "ok": False,
            "status": "NO_INDEPENDENT_ACTIVE_HOLDOUT",
            "region": None,
            "scanned": scanned,
            "eligible_count": 0,
            "selection_basis": "arrangement_activity_simultaneous_relevant_sources_before_diagnosis",
            "note": (
                "No unseen 32-qn window with simultaneous multi-source interaction. "
                "Do not recycle a used region. Next evaluation needs another song."
            ),
        }

    chosen = max(
        eligible,
        key=lambda r: (
            int(r["simultaneous_sources"]),
            float(r["max_pairwise_simultaneous_qn"]),
            -float(r["start_qn"]),
        ),
    )
    region_id = f"HOLDOUT_{int(chosen['start_qn'])}_{int(chosen['end_qn'])}"
    return {
        "ok": True,
        "status": "SELECTED",
        "region": {
            "id": region_id,
            "start_qn": float(chosen["start_qn"]),
            "end_qn": float(chosen["end_qn"]),
            "selection_rationale": (
                "Chosen from arrangement activity only, before diagnosis, because "
                f"{chosen['simultaneous_sources']} relevant sources coincide "
                f"(pairwise simultaneous {chosen['max_pairwise_simultaneous_qn']:.1f} qn). "
                "NOT selected for a known defect."
            ),
            "present_relevant_sources": list(chosen["present_relevant_sources"]),
            "simultaneous_sources": chosen["simultaneous_sources"],
        },
        "chosen_metrics": chosen,
        "scanned": scanned,
        "eligible_count": len(eligible),
        "selection_basis": "arrangement_activity_simultaneous_relevant_sources_before_diagnosis",
    }


def select_holdout_from_als(als_path: Path) -> dict[str, Any]:
    clips = load_arrangement_clips(Path(als_path))
    out = select_holdout_from_clips(clips)
    out["als_path"] = str(als_path)
    out["clip_count"] = len(clips)
    return out


def persist_holdout_v3(
    evidence: Path,
    *,
    selection: dict[str, Any],
    project_token: str,
) -> Path:
    region = selection.get("region")
    payload = {
        "milestone": MILESTONE,
        "persisted_at": now_iso(),
        "region": None if region is None else {
            "id": region["id"],
            "start_qn": region["start_qn"],
            "end_qn": region["end_qn"],
        },
        "selection_rationale": None if region is None else region.get("selection_rationale"),
        "PROJECT_STATE_TOKEN_at_selection": project_token,
        "forbidden_ranges": [
            {"start_qn": a, "end_qn": b, "label": lab}
            for a, b, lab in FORBIDDEN_QN_RANGES
        ],
        "NO_ANALYZER_RETUNE": True,
        "selection_basis": selection.get("selection_basis"),
        "not_selected_for_known_defect": True,
        "selection_status": selection.get("status"),
        "eligible_count": selection.get("eligible_count"),
        "ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1": f"{ISOLATION_STATUS} / FROZEN={ISOLATION_FROZEN}",
        "SOURCE_EVIDENCE_GAP": SOURCE_EVIDENCE_GAP,
    }
    if region is not None:
        hit = forbidden_hit(float(region["start_qn"]), float(region["end_qn"]))
        if hit:
            raise ValueError(f"holdout overlaps forbidden region: {hit}")
    path = Path(evidence) / HOLDOUT_ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def causal_target_from_candidates(
    session: SessionState,
    candidate_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """A named, resolvable track is a supported causal target. Do not invent one."""
    for row in candidate_actions:
        if str(row.get("action_type") or "") != PRODUCTION_ACTION:
            continue
        name = row.get("target")
        if not name:
            continue
        track = session.track_by_name(str(name))
        if track is not None:
            return {
                "status": "CAUSE_SUPPORTED",
                "track": track.name,
                "index": track.index,
            }
    return {"status": None, "track": None, "index": None}


def _asset_from_wav(
    *,
    path: str,
    capture_id: str,
    source: str,
    start_qn: float,
    end_qn: float,
    tempo: float,
    sample_rate: int,
    duration_s: float,
    rms: float,
    peak: float,
    signal_point: SignalPoint,
    label: str,
) -> AudioAsset:
    return AudioAsset(
        capture_id=capture_id,
        raw_file_path=path,
        analysis_file_path=path,
        file_path=path,
        source=source,
        source_type="TRACK" if source != "Main" else "MASTER",
        requested_start_beat=start_qn,
        requested_end_beat=end_qn,
        start_beat=start_qn,
        end_beat=end_qn,
        sample_rate=int(sample_rate),
        channels=2,
        raw_duration=float(duration_s),
        analysis_duration=float(duration_s),
        duration=float(duration_s),
        session_revision=0,
        tempo=tempo,
        requested_start_qn=start_qn,
        requested_end_qn=end_qn,
        rms=float(rms),
        peak=float(peak),
        capture_view=CaptureView.TRACK_ISOLATED if source != "Main" else CaptureView.MASTER_CONTEXT,
        signal_point=signal_point,
        signal_point_label=label,
        capture_quality="OK",
    )


def _public_source_row(row: dict[str, Any]) -> dict[str, Any]:
    skip = {"routing_snapshot_before", "restore"}
    return {k: v for k, v in row.items() if k not in skip}


def capture_active_source_pack(
    daw: AbletonTcpAdapter,
    *,
    session: SessionState,
    preflight: dict[str, Any],
    region: dict[str, Any],
    tempo: float,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    """Main + Post Mixer views for arrangement-active relevant sources.

    Frozen isolation primitive. No fixed Kick 808 Deep tap as a source view.
    """
    dest_root = capture_dir()
    dest_root.mkdir(parents=True, exist_ok=True)
    hosts = preflight.get("capture_hosts") or {}
    bass_host = hosts.get(CAPTURE_BASS) or {}
    if bass_host.get("index") is None:
        return {"ok": False, "error": "CAPTURE_BASS_HOST_MISSING", "sources": []}
    host_index = int(bass_host["index"])
    production_before = _snapshot_host(daw, host_index)
    material_by_name = {
        str(row.get("display_name")): row.get("arrangement_material")
        for row in (inventory.get("sources") or [])
    }
    targets = [
        name
        for name in RELEVANT_SOURCES
        if material_by_name.get(name) == "HAS_MATERIAL"
    ]
    captures: list[dict[str, Any]] = []
    try:
        for name in targets:
            live = daw.snapshot(include_notes=False)
            attach_tokens(live)
            track = live.track_by_name(name)
            if track is None:
                captures.append(
                    {
                        "ok": False,
                        "display_name": name,
                        "signal_status": "CAPTURE_FAILED",
                        "error": "TRACK_NOT_FOUND",
                        "arrangement_material": material_by_name.get(name),
                    }
                )
                continue
            row = capture_source_post_mixer(
                daw,
                session=live,
                preflight=preflight,
                track=track,
                start_qn=float(region["start_qn"]),
                end_qn=float(region["end_qn"]),
                region_id=str(region["id"]),
                tempo=tempo,
                host_index=host_index,
                dest_root=dest_root,
            )
            row["arrangement_material"] = material_by_name.get(name)
            row["capture_target"] = f"{name} Post Mixer"
            row["fixed_semantic_tap"] = False
            captures.append(row)
            daw.stop_playback()
    finally:
        final_restore = _restore_host_full(daw, host_index, production_before)
        _ensure_idle_taps(daw, preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"})))
        daw.stop_playback()

    ok_rows = [c for c in captures if c.get("ok")]
    main = next((c for c in ok_rows if c.get("main_signal_status") == "HAS_SIGNAL"), None)
    if main is None:
        main = ok_rows[0] if ok_rows else None
    provenance_ok = bool(ok_rows) and all(
        (c.get("restore") or {}).get("ok", False) for c in ok_rows
    ) and bool(final_restore.get("ok"))
    if main is not None:
        provenance_ok = provenance_ok and main.get("main_signal_status") == "HAS_SIGNAL"
    return {
        "ok": bool(ok_rows) and provenance_ok,
        "provenance_ok": provenance_ok,
        "host": HOST_NAME,
        "host_index": host_index,
        "targets": targets,
        "sources": captures,
        "main": None
        if main is None
        else {
            "signal_status": main.get("main_signal_status"),
            "rms": main.get("main_rms"),
            "wav_path": main.get("main_wav_path"),
            "audio_sha256": main.get("main_audio_sha256"),
            "sample_rate": main.get("sample_rate"),
            "duration_s": main.get("duration_s"),
            "pass_id": main.get("pass_id"),
            "from_source": main.get("display_name"),
        },
        "production_host_final_restore": final_restore,
        "fixed_kick_tap_used_as_source": False,
        "isolation_milestone": "ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1",
    }


def _source_evidence_items(
    *,
    captures: list[dict[str, Any]],
    region_label: str,
    project_token: str,
    audible_token: str,
) -> tuple[list[EvidenceItem], list[EntityRef]]:
    items: list[EvidenceItem] = []
    entities: list[EntityRef] = []
    for cap in captures:
        name = str(cap.get("display_name") or "unknown")
        slug = name.lower().replace(" ", "_")
        entities.append(
            EntityRef(entity_id=f"track:{slug}", kind=EntityKind.TRACK, name=name, role="source")
        )
        for key, value, unit in (
            (f"src.{slug}.arrangement_material", cap.get("arrangement_material"), None),
            (f"src.{slug}.signal_status", cap.get("signal_status"), None),
            (f"src.{slug}.rms", cap.get("rms"), None),
            (f"src.{slug}.peak", cap.get("peak"), None),
            (f"src.{slug}.signal_point", cap.get("signal_point") or "POST_MIXER", None),
        ):
            items.append(
                EvidenceItem(
                    evidence_id=key,
                    kind=EvidenceKind.FACT if "rms" not in key and "peak" not in key else EvidenceKind.MEASUREMENT,
                    source_ref=f"asset:{cap.get('audio_sha256') or 'missing'}",
                    region=region_label,
                    view="TRACK_ISOLATED",
                    signal_point="TRACK_POST_MIXER",
                    analysis_version=ANALYSIS_VERSION,
                    name=key.split(".", 2)[-1],
                    value=value,
                    unit=unit,
                    quality=CaptureQuality.LIMITED,
                    limitations=["ALIGNMENT_LIMITED", "HOLDOUT_NO_ANALYZER_RETUNE"],
                    project_token=project_token,
                    audible_token=audible_token,
                )
            )
    return items, entities


def _mixer_evidence_items(
    session: SessionState,
    *,
    region_label: str,
    project_token: str,
    audible_token: str,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    wanted = set(RELEVANT_SOURCES)
    for track in session.tracks:
        if track.name not in wanted and track.role != "master":
            continue
        slug = track.name.lower().replace(" ", "_")
        items.extend(
            [
                EvidenceItem(
                    evidence_id=f"mix.{slug}.volume",
                    kind=EvidenceKind.SESSION_ENTITY,
                    source_ref="session:live",
                    region=region_label,
                    view=None,
                    signal_point=None,
                    analysis_version=ANALYSIS_VERSION,
                    name="mixer_volume",
                    value=float(track.mixer.volume),
                    quality=CaptureQuality.OK,
                    project_token=project_token,
                    audible_token=audible_token,
                ),
                EvidenceItem(
                    evidence_id=f"mix.{slug}.mute",
                    kind=EvidenceKind.SESSION_ENTITY,
                    source_ref="session:live",
                    region=region_label,
                    analysis_version=ANALYSIS_VERSION,
                    name="mixer_mute",
                    value=bool(track.mixer.mute),
                    quality=CaptureQuality.OK,
                    project_token=project_token,
                    audible_token=audible_token,
                ),
                EvidenceItem(
                    evidence_id=f"rt.{slug}.output",
                    kind=EvidenceKind.FACT,
                    source_ref="session:live",
                    region=region_label,
                    analysis_version=ANALYSIS_VERSION,
                    name="routing_output",
                    value=f"{track.routing.output_type}/{track.routing.output_channel}",
                    quality=CaptureQuality.OK,
                    project_token=project_token,
                    audible_token=audible_token,
                ),
                EvidenceItem(
                    evidence_id=f"dev.{slug}.names",
                    kind=EvidenceKind.FACT,
                    source_ref="session:live",
                    region=region_label,
                    analysis_version=ANALYSIS_VERSION,
                    name="device_names",
                    value=[d.name for d in (track.devices or [])],
                    quality=CaptureQuality.OK,
                    project_token=project_token,
                    audible_token=audible_token,
                ),
            ]
        )
    return items


def build_observations_v3(
    *,
    capture: dict[str, Any],
    preflight: dict[str, Any],
    session: SessionState,
    region: dict[str, Any],
    tempo: float,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    region_label = f"{region['start_qn']:g}->{region['end_qn']:g}qn"
    project_token = str(preflight.get("project_token") or session.project_token or "")
    audible_token = str(preflight.get("audible_token") or session.audible_token or "")
    main = capture.get("main") or {}
    main_path = main.get("wav_path")
    if not main_path:
        return {"ok": False, "error": "MAIN_WAV_MISSING"}
    main_asset = _asset_from_wav(
        path=str(main_path),
        capture_id=str(main.get("pass_id") or "main"),
        source="Main",
        start_qn=float(region["start_qn"]),
        end_qn=float(region["end_qn"]),
        tempo=tempo,
        sample_rate=int(main.get("sample_rate") or 44100),
        duration_s=float(main.get("duration_s") or 0.0),
        rms=float(main.get("rms") or 0.0),
        peak=0.0,
        signal_point=SignalPoint.MAIN_FINAL,
        label="Main FINAL",
    )
    digest = str(main.get("audio_sha256") or "")
    fullmix = compute_fullmix_observation(
        Path(str(main_path)),
        region_id=str(region["id"]),
        region_label=region_label,
        audio_sha256=digest,
    )

    def _source_asset(name: str, cap: dict[str, Any]) -> AudioAsset:
        return _asset_from_wav(
            path=str(cap["wav_path"]),
            capture_id=str(cap.get("pass_id") or name),
            source=str(name),
            start_qn=float(region["start_qn"]),
            end_qn=float(region["end_qn"]),
            tempo=tempo,
            sample_rate=int(cap.get("sample_rate") or 44100),
            duration_s=float(cap.get("duration_s") or 0.0),
            rms=float(cap.get("rms") or 0.0),
            peak=float(cap.get("peak") or 0.0),
            signal_point=SignalPoint.TRACK_POST_MIXER,
            label=f"{name} Post Mixer",
        )

    source_assets: dict[str, AudioAsset] = {"master": main_asset}
    by_name = {c.get("display_name"): c for c in (capture.get("sources") or []) if c.get("ok")}
    for name, cap in by_name.items():
        source_assets[str(name)] = _source_asset(str(name), cap)

    drums_ok = (by_name.get(DRUMS_NAME) or {}).get("signal_status") == "HAS_SIGNAL"
    sub_ok = (by_name.get(SUB_NAME) or {}).get("signal_status") == "HAS_SIGNAL"
    rose_ok = (by_name.get(ROSE_NAME) or {}).get("signal_status") == "HAS_SIGNAL"
    lowend_applicable = drums_ok and (sub_ok or rose_ok)
    if drums_ok:
        source_assets["kick"] = source_assets[DRUMS_NAME]
    if sub_ok:
        source_assets["bass"] = source_assets[SUB_NAME]
    elif lowend_applicable and rose_ok:
        source_assets["bass"] = source_assets[ROSE_NAME]

    features: dict[str, Any] | None = None
    pack: EvidencePack
    bass_entity = SUB_NAME if sub_ok else ROSE_NAME
    if lowend_applicable and "kick" in source_assets and "bass" in source_assets:
        features = compute_lowend_features(
            {
                "master": source_assets["master"],
                "kick": source_assets["kick"],
                "bass": source_assets["bass"],
            }
        )
        if not features.get("ok"):
            return {"ok": False, "error": features.get("missing"), "region": region}
        pack = pack_from_lowend_features(
            region_id=str(region["id"]),
            region=region_label,
            features=features,
            views={"kick": source_assets["kick"], "bass": source_assets["bass"]},
            project_token=project_token,
            audible_token=audible_token,
            target_token=None,
            kick_name=DRUMS_NAME,
            bass_name=bass_entity,
        )
        pack = merge_lowend_and_fullmix(pack, fullmix)
        pack.limitations.append(
            ObservationLimitation(
                code="KICK_VIEW_IS_DRUMS_POST_MIXER",
                detail=(
                    "Frozen LowEnd analyzer ran on Drums track Post Mixer, "
                    "not the Kick 808 Deep pad tap (FIXED_TAP_NOT_REPRESENTATIVE)."
                ),
            )
        )
    else:
        pack = EvidencePack(
            pack_id=str(region["id"]),
            analysis_version=ANALYSIS_VERSION,
            prompt_schema_version=SCHEMA_VERSION,
            region=region_label,
            project_token=project_token,
            audible_token=audible_token,
            alignment_claim="LIMITED",
            items=[],
            entities=[EntityRef(entity_id="bus:main", kind=EntityKind.TRACK, name="Main", role="main")],
            limitations=[
                ObservationLimitation(
                    code="LOWEND_NOT_APPLICABLE",
                    detail="Need HAS_SIGNAL Drums Post Mixer plus a bass Post Mixer.",
                )
            ],
            domain="fullmix+sources",
        )
        from copilot.reasoning.from_fullmix import fullmix_items

        pack = pack.model_copy(
            update={"items": list(pack.items) + fullmix_items(
                fullmix,
                project_token=project_token,
                audible_token=audible_token,
            )}
        )

    src_items, src_entities = _source_evidence_items(
        captures=list(capture.get("sources") or []),
        region_label=region_label,
        project_token=project_token,
        audible_token=audible_token,
    )
    mix_items = _mixer_evidence_items(
        session,
        region_label=region_label,
        project_token=project_token,
        audible_token=audible_token,
    )
    pack = pack.model_copy(
        update={
            "items": list(pack.items) + src_items + mix_items,
            "entities": list(pack.entities) + src_entities,
            "limitations": list(pack.limitations)
            + [
                ObservationLimitation(
                    code="FIXED_TAP_NOT_USED",
                    detail="Evidence pack does not treat Kick 808 Deep pad tap as Drums.",
                ),
                ObservationLimitation(
                    code="HOLDOUT_NO_ANALYZER_RETUNE",
                    detail="Frozen analyzers only.",
                ),
            ],
        }
    )

    observations = {}
    for key, asset in source_assets.items():
        if key in {"kick", "bass", "master"} or key in {ROSE_NAME.lower().replace(" ", "_")}:
            try:
                observations[key] = _observation_card(observe_asset(asset, region_label, tempo))
            except Exception as exc:  # noqa: BLE001
                observations[key] = {"error": str(exc)}

    source_class = {
        name: {
            "arrangement_material": (by_name.get(name) or {}).get("arrangement_material"),
            "signal_status": (by_name.get(name) or {}).get("signal_status"),
            "rms": (by_name.get(name) or {}).get("rms"),
        }
        for name in RELEVANT_SOURCES
    }
    return {
        "ok": True,
        "region": region,
        "region_label": region_label,
        "source_class": source_class,
        "inventory_active_names": inventory.get("active_display_names"),
        "lowend_applicable": lowend_applicable,
        "features": None
        if features is None
        else _jsonable(
            {
                "ok": features.get("ok"),
                "kick_rms": features.get("kick_rms"),
                "bass_rms": features.get("bass_rms"),
                "master_rms": features.get("master_rms"),
                "kick_view": DRUMS_NAME,
                "bass_view": bass_entity if lowend_applicable else None,
            }
        ),
        "fullmix": {
            "audio_sha256": fullmix.audio_sha256,
            "energy_event_count": len(fullmix.energy_events),
            "energy_events": [
                {
                    "kind": ev.kind.value if hasattr(ev.kind, "value") else ev.kind,
                    "start_s": ev.start_s,
                    "end_s": ev.end_s,
                    "duration_s": ev.duration_s,
                    "relative_drop_db": ev.relative_drop_db,
                }
                for ev in (fullmix.energy_events or [])[:12]
            ],
            "dynamics": [d.model_dump(mode="json") for d in (fullmix.dynamics or [])],
        },
        "music_observation": observations,
        "evidence_pack": pack.model_dump(mode="json"),
        "listen_main": str(main_path),
        "tokens": {
            "PROJECT_STATE_TOKEN": project_token,
            "AUDIBLE_STATE_TOKEN": audible_token,
        },
        "pack": pack,
        "mixer_state": _mixer_snapshot(session),
    }


def _musical_compare_v3(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    expected_effect: str,
    diagnosis_category: str | None,
    target_name: str | None,
) -> dict[str, Any]:
    """KEEP only if the specific expected musical effect is supported. Ambiguous → ROLLBACK."""
    supporting: list[str] = []
    contradicting: list[str] = []
    limitations = [
        "V3 compares Main FullMix plus isolated source RMS. Parameter change is not success.",
        "No analyzer retune from holdout.",
        "Ambiguous improvement → ROLLBACK.",
    ]
    if diagnosis_category != FindingType.LEVEL_IMBALANCE.value:
        contradicting.append("category_not_level_imbalance")
    if not expected_effect:
        contradicting.append("missing_expected_musical_effect")

    before_src = (before.get("source_class") or {}).get(target_name or "") or {}
    after_src = (after.get("source_class") or {}).get(target_name or "") or {}
    b_rms = before_src.get("rms")
    a_rms = after_src.get("rms")
    if b_rms is None or a_rms is None:
        contradicting.append("missing_target_source_rms")
    else:
        delta = float(a_rms) - float(b_rms)
        supporting.append(f"target_source_rms_delta={delta}")
        # Volume reduction expected: target source quieter, not merely any RMS change.
        if "reduc" in expected_effect.lower() or "lower" in expected_effect.lower() or "quiet" in expected_effect.lower():
            if delta >= -1e-6:
                contradicting.append("target_source_not_quieter")
        else:
            contradicting.append("expected_effect_not_mapped_to_source_rms")

    b_events = int((before.get("fullmix") or {}).get("energy_event_count") or 0)
    a_events = int((after.get("fullmix") or {}).get("energy_event_count") or 0)
    supporting.append(f"fullmix_energy_event_delta={a_events - b_events}")

    verdict = "SUPPORTED"
    if contradicting:
        verdict = "NOT_SUPPORTED"
    return {
        "verdict": verdict,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "limitations": limitations,
        "expected_effect": expected_effect,
        "target_name": target_name,
        "before_target_rms": b_rms,
        "after_target_rms": a_rms,
    }


def _persist(evidence: Path, report: dict[str, Any]) -> Path:
    path = Path(evidence) / ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        k: v
        for k, v in report.items()
        if k not in {"_pack", "_fullmix_obs", "_tools", "preflight_raw"}
    }
    path.write_text(
        json.dumps(clean, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    report["artifact"] = str(path)
    return path


def _write_acceptance(evidence: Path, report: dict[str, Any]) -> Path:
    compact = {
        MILESTONE: report.get(MILESTONE),
        "final_musical_decision": report.get("final_musical_decision"),
        "holdout": report.get("holdout"),
        "selection_status": (report.get("holdout_selection") or {}).get("status"),
        "CONTROLLED_ENGINEERING": CONTROLLED_ENGINEERING,
        "ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1": f"{ISOLATION_STATUS} / FROZEN",
        "FAMI_V1": FAMI_V1_STATUS,
        "AME_V2": AME_V2_STATUS,
        "SOURCE_EVIDENCE_GAP": SOURCE_EVIDENCE_GAP,
        "ASTRA_CALLS": report.get("ASTRA CALLS"),
        "MUSICAL_WRITES": report.get("MUSICAL WRITES"),
        "diagnosis_status": (report.get("diagnosis_summary") or {}).get("status"),
        "actionability": (report.get("actionability") or {}).get("decision"),
        "STOP": report.get("STOP"),
        "full_report": str(Path(evidence) / ARTIFACT),
        "holdout_artifact": str(Path(evidence) / HOLDOUT_ARTIFACT),
        "no_second_holdout": True,
        "no_analyzer_retune": True,
        "no_new_action_types": True,
        "no_source_isolation_modification": True,
    }
    path = Path(evidence) / ACCEPTANCE_ARTIFACT
    path.write_text(json.dumps(compact, indent=2), encoding="utf-8")
    report["acceptance_artifact"] = str(path)
    return path


def _terminal(
    report: dict[str, Any],
    *,
    evidence: Path,
    status: str,
    musical_decision: str,
    daw: AbletonTcpAdapter | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in SUCCESS_MODES and status != "BLOCKED":
        status = "INSUFFICIENT_EVIDENCE"
    if daw is not None:
        try:
            daw.stop_playback()
        except Exception:  # noqa: BLE001
            pass
        try:
            _ensure_idle_taps(
                daw, preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
            )
            final_pf = preflight_session(daw, lab_track_exclusions=frozenset({"AI Test"}))
            hosts = final_pf.get("capture_hosts") or {}
            report["capture_hosts_taps_final"] = {
                CAPTURE_HOST: (hosts.get(CAPTURE_HOST) or {}).get("tap"),
                CAPTURE_BASS: (hosts.get(CAPTURE_BASS) or {}).get("tap"),
                "MASTER": ((final_pf.get("main") or {}).get("tap")),
            }
            pos = daw.get_playback_position()
            report["transport_final"] = {
                "playing": bool(pos.get("is_playing")),
                "qn": float(pos.get("current_song_time") or 0.0),
            }
            report["open_capture_journals_final"] = unresolved_capture_journals()
        except Exception as exc:  # noqa: BLE001
            report["final_state_error"] = str(exc)

    report[MILESTONE] = status
    report["status"] = status
    report["final_musical_decision"] = musical_decision
    report["STOP"] = True
    if extra:
        report.update(extra)
    report["completed_at"] = now_iso()
    if isinstance(report.get("pre_run_restore"), dict):
        pr = dict(report["pre_run_restore"])
        pr.pop("preflight", None)
        report["pre_run_restore"] = pr
    _persist(evidence, report)
    _write_acceptance(evidence, report)
    return report


def run_autonomous_musical_evaluation_v3(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path | None = None,
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    evidence.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        MILESTONE: "STARTED",
        "status": "STARTED",
        "intent_class": PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT.value,
        "created_at": now_iso(),
        "ASTRA CALLS": 0,
        "MUSICAL WRITES": 0,
        "STOP": False,
        "CONTROLLED_ENGINEERING": CONTROLLED_ENGINEERING,
        "ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1": ISOLATION_MILESTONE,
        "FAMI_V1": FAMI_V1_STATUS,
        "AME_V2": AME_V2_STATUS,
        "SOURCE_EVIDENCE_GAP": SOURCE_EVIDENCE_GAP,
        "note": (
            "One new blind holdout. Isolation V1 frozen. "
            "Abstention valid. SET_TRACK_VOLUME only. No analyzer retune. "
            "Do not recycle used regions."
        ),
        "production_action_vocabulary": [PRODUCTION_ACTION],
        "non_volume_tools_refused": sorted(NON_VOLUME_ACTION_TYPES),
    }

    pre_run = pre_run_restore_check(daw)
    report["pre_run_restore"] = pre_run
    if not pre_run.get("ok"):
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "PRE_RUN_RESTORE_FAILED"},
        )

    preflight = pre_run.get("preflight") or preflight_session(
        daw, lab_track_exclusions=frozenset({"AI Test"})
    )
    session0 = daw.snapshot(include_notes=False)
    attach_tokens(session0)
    project_token = session0.project_token or ""
    als_path = _als_path_from_preflight(preflight)
    if not als_path.is_file():
        als_path = Path(WORKING_COPY_CANDIDATE)

    # ----- PHASE 1: select + persist holdout BEFORE DSP / Astra -----
    selection = select_holdout_from_als(als_path)
    report["holdout_selection"] = {
        k: v for k, v in selection.items() if k != "scanned"
    }
    report["holdout_scan_summary"] = [
        {
            "start_qn": r["start_qn"],
            "end_qn": r["end_qn"],
            "useful": r["useful"],
            "forbidden": r["forbidden"],
            "simultaneous_sources": r["simultaneous_sources"],
        }
        for r in (selection.get("scanned") or [])
    ]
    holdout_path = persist_holdout_v3(
        evidence, selection=selection, project_token=str(project_token)
    )
    region = selection.get("region")
    report["holdout"] = {
        "artifact": str(holdout_path),
        "PROJECT_STATE_TOKEN_at_selection": project_token,
        "persisted_at": now_iso(),
        **(region or {"id": None, "start_qn": None, "end_qn": None}),
    }
    if not selection.get("ok") or region is None:
        return _terminal(
            report,
            evidence=evidence,
            status="NO_INDEPENDENT_ACTIVE_HOLDOUT",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={
                "error": "NO_INDEPENDENT_ACTIVE_HOLDOUT",
                "next_evaluation": "another real project/song",
            },
        )

    session = daw.snapshot(include_notes=True)
    attach_tokens(session)
    report["fresh_tokens"] = {
        "PROJECT_STATE_TOKEN": session.project_token or "",
        "AUDIBLE_STATE_TOKEN": session.audible_token or "",
    }
    report["mixer_state"] = _mixer_snapshot(session)
    pos = daw.get_playback_position()
    tempo = float(pos.get("tempo") or preflight.get("tempo") or 120.0)

    inventory = inventory_active_sources(
        session=session,
        als_path=als_path,
        start_qn=float(region["start_qn"]),
        end_qn=float(region["end_qn"]),
    )
    report["active_source_inventory"] = {
        "active_display_names": inventory.get("active_display_names"),
        "active_count": inventory.get("active_count"),
        "method": inventory.get("method"),
        "sources": [
            {
                "display_name": s.get("display_name"),
                "arrangement_material": s.get("arrangement_material"),
                "mute": s.get("mute"),
                "volume": s.get("volume"),
            }
            for s in (inventory.get("sources") or [])
            if s.get("arrangement_material") == "HAS_MATERIAL"
        ],
    }

    try:
        capture = capture_active_source_pack(
            daw,
            session=session,
            preflight=preflight,
            region=region,
            tempo=tempo,
            inventory=inventory,
        )
    except Exception as exc:  # noqa: BLE001
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": f"CAPTURE_FAILED:{exc}"},
        )

    report["capture"] = {
        "ok": capture.get("ok"),
        "provenance_ok": capture.get("provenance_ok"),
        "fixed_kick_tap_used_as_source": False,
        "main": capture.get("main"),
        "targets": capture.get("targets"),
        "sources": [_public_source_row(s) for s in (capture.get("sources") or [])],
        "production_host_final_restore": capture.get("production_host_final_restore"),
    }
    if not capture.get("ok") or not capture.get("provenance_ok"):
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "CAPTURE_PROVENANCE_FAILED"},
        )

    live_for_obs = daw.snapshot(include_notes=False)
    attach_tokens(live_for_obs)
    obs = build_observations_v3(
        capture=capture,
        preflight={
            **preflight,
            "project_token": live_for_obs.project_token or preflight.get("project_token"),
            "audible_token": live_for_obs.audible_token or preflight.get("audible_token"),
        },
        session=live_for_obs,
        region=region,
        tempo=tempo,
        inventory=inventory,
    )
    report["observations"] = {k: v for k, v in obs.items() if k not in {"pack"}}
    if not obs.get("ok"):
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "DSP_OBSERVATION_FAILED", "detail": obs.get("error")},
        )

    provider = configured_http_provider()
    if provider is None:
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "REAL_MODEL_UNAVAILABLE"},
        )

    result = reason(obs["pack"], provider, timeout_s=180.0)
    report["ASTRA CALLS"] = 1
    out = None if result.output is None else result.output.model_dump(mode="json")
    report["diagnosis"] = {
        "accepted": result.accepted,
        "failure": None if result.failure is None else result.failure.value,
        "output": out,
        "diagnosis": None
        if result.diagnosis is None
        else result.diagnosis.model_dump(mode="json"),
        "audit": result.to_dict().get("audit"),
    }

    status = str((out or {}).get("status") or DiagnosisStatus.INSUFFICIENT_EVIDENCE.value)
    category = (out or {}).get("category")
    candidates = list((out or {}).get("candidate_actions") or [])
    supporting = list((out or {}).get("evidence_refs") or [])
    contradicting = list((out or {}).get("contradicting_evidence_refs") or [])
    limitations = list((out or {}).get("limitations") or [])
    hypotheses = list((out or {}).get("hypotheses") or [])

    live = daw.snapshot(include_notes=False)
    attach_tokens(live)
    causal = causal_target_from_candidates(live, candidates)
    report["diagnosis_summary"] = {
        "status": status,
        "category": category,
        "confidence": (out or {}).get("confidence"),
        "limitations": limitations,
        "hypothesis": hypotheses[0] if hypotheses else None,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "candidate_strategy": candidates,
        "cause_status": causal.get("status"),
        "requested_evidence": (out or {}).get("requested_evidence") or [],
    }

    gate = evaluate_action_gate(
        diagnosis_status=status,
        diagnosis_accepted=bool(result.accepted),
        category=None if category is None else str(category),
        candidate_actions=candidates,
        cause_status=causal.get("status"),
    )
    report["actionability"] = gate
    milestone = map_gate_to_milestone(gate, status)

    binding = DiagnosisBinding(
        diagnosis_id=f"{MILESTONE}:{region['id']}",
        diagnosis_revision=3,
        diagnosis_status=status,
        diagnosis_accepted=bool(result.accepted),
        cause_status=causal.get("status"),
        region_id=str(region["id"]),
        artifact=str(evidence / ARTIFACT),
    )

    if not gate.get("proceed_to_write"):
        plan = abstain_from_diagnosis(
            diagnosis=binding,
            project_state_token=str(live.project_token or ""),
            audible_state_token=str(live.audible_token or ""),
            evidence_refs=supporting,
            reason=str(gate.get("milestone_status") or gate.get("reason") or status),
        )
        report["MusicPlan"] = plan.model_dump(mode="json")
        report["causal_target"] = causal if causal.get("track") else None
        final_session = daw.snapshot(include_notes=False)
        attach_tokens(final_session)
        return _terminal(
            report,
            evidence=evidence,
            status=milestone,
            musical_decision="ABSTAIN",
            daw=daw,
            extra={
                "final_project_state": {
                    "PROJECT_STATE_TOKEN": final_session.project_token or "",
                    "AUDIBLE_STATE_TOKEN": final_session.audible_token or "",
                },
                "open_transaction": False,
            },
        )

    track = _resolve_volume_target(live, candidates)
    if track is None:
        return _terminal(
            report,
            evidence=evidence,
            status="INSUFFICIENT_EVIDENCE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "NO_RESOLVABLE_VOLUME_TARGET"},
        )

    report["causal_target"] = {"track": track.name, "index": track.index}
    before_vol = float(track.mixer.volume)
    intended = max(0.05, before_vol - 0.02)
    if intended >= before_vol:
        return _terminal(
            report,
            evidence=evidence,
            status="ACTION_NOT_AVAILABLE",
            musical_decision="ABSTAIN",
            daw=daw,
            extra={"error": "NO_SAFE_VOLUME_HEADROOM"},
        )

    expected_effect = str(
        next(
            (
                c.get("expected_effect")
                for c in candidates
                if str(c.get("action_type")) == PRODUCTION_ACTION
            ),
            "reduce target track contribution to address LEVEL_IMBALANCE",
        )
    )
    action = build_set_track_volume_action(
        track=track,
        project_identity=live.project_identity or "",
        operation=VolumeOperation.SET,
        expected_before=before_vol,
        target_value=intended,
        reason=str(
            next(
                (
                    c.get("reason")
                    for c in candidates
                    if str(c.get("action_type")) == PRODUCTION_ACTION
                ),
                "Autonomous LEVEL_IMBALANCE volume correction",
            )
        ),
        evidence_refs=supporting or [f"{MILESTONE}.holdout"],
        session_incarnation_id=live.session_incarnation_id or "",
    )
    action.expected_effect.description = expected_effect
    action.expected_effect.measurement_to_compare_after = (
        f"isolated {track.name} Post Mixer RMS BEFORE vs AFTER plus Main FullMix"
    )
    action.verification.musical.deferred = False
    action.verification.musical.comparison = "holdout_source_and_fullmix_before_after"

    plan = MusicPlan(
        plan_id=new_plan_id(),
        status=PlanStatus.DRAFT,
        intent_class=PlanIntentClass.AUTONOMOUS_MUSICAL_IMPROVEMENT,
        diagnosis=binding,
        project_state_token=live.project_token or "",
        audible_state_token=live.audible_token or "",
        target_state_tokens={track.name: target_token(track)},
        evidence_refs=supporting or [f"{MILESTONE}.holdout"],
        actions=[action],
        created_at=now_iso(),
        notes=[f"{MILESTONE} — one SET_TRACK_VOLUME max. No second write."],
    )
    report["MusicPlan"] = plan.model_dump(mode="json")
    report["BEFORE_evidence"] = {
        "parameter_volume": before_vol,
        "fullmix": obs.get("fullmix"),
        "source_class": obs.get("source_class"),
        "tokens": obs.get("tokens"),
    }

    tools = build_agent_tools(
        daw, journal_path=evidence / "journals" / f"ame_v3_{uuid4().hex[:10]}.jsonl"
    )
    write_report = _execute_one_volume_write(tools, plan=plan, target_name=track.name)
    report["write_readback"] = write_report
    report["MUSICAL WRITES"] = int(
        (write_report.get("MUSICAL_WRITE_COUNT") or {}).get("forward") or 0
    )

    if not write_report.get("EXECUTED"):
        rb = _rollback_volume(
            tools,
            target_name=track.name,
            rollback_value=float(write_report.get("rollback_value") or before_vol),
        )
        report["rollback"] = rb
        report["MUSICAL WRITES"] += int(rb.get("MUSICAL_WRITE_COUNT_rollback") or 0)
        return _terminal(
            report,
            evidence=evidence,
            status="AUTONOMOUS_CHANGE_ROLLED_BACK",
            musical_decision="ROLLBACK",
            daw=daw,
        )

    session_after = daw.snapshot(include_notes=False)
    attach_tokens(session_after)
    preflight_after = {
        **preflight,
        "project_token": session_after.project_token,
        "audible_token": session_after.audible_token,
        "revision": session_after.revision,
    }
    try:
        after_capture = capture_active_source_pack(
            daw,
            session=session_after,
            preflight=preflight_after,
            region=region,
            tempo=tempo,
            inventory=inventory,
        )
    except Exception as exc:  # noqa: BLE001
        rb = _rollback_volume(tools, target_name=track.name, rollback_value=before_vol)
        report["rollback"] = rb
        report["MUSICAL WRITES"] += int(rb.get("MUSICAL_WRITE_COUNT_rollback") or 0)
        return _terminal(
            report,
            evidence=evidence,
            status="AUTONOMOUS_CHANGE_ROLLED_BACK",
            musical_decision="ROLLBACK",
            daw=daw,
            extra={"error": f"AFTER_CAPTURE_FAILED:{exc}"},
        )

    report["after_capture"] = {
        "ok": after_capture.get("ok"),
        "main": after_capture.get("main"),
        "sources": [_public_source_row(s) for s in (after_capture.get("sources") or [])],
    }
    if not after_capture.get("ok"):
        rb = _rollback_volume(tools, target_name=track.name, rollback_value=before_vol)
        report["rollback"] = rb
        report["MUSICAL WRITES"] += int(rb.get("MUSICAL_WRITE_COUNT_rollback") or 0)
        return _terminal(
            report,
            evidence=evidence,
            status="AUTONOMOUS_CHANGE_ROLLED_BACK",
            musical_decision="ROLLBACK",
            daw=daw,
            extra={"error": "AFTER_CAPTURE_PROVENANCE_FAILED"},
        )

    after_obs = build_observations_v3(
        capture=after_capture,
        preflight=preflight_after,
        session=session_after,
        region=region,
        tempo=tempo,
        inventory=inventory,
    )
    report["AFTER_evidence"] = {k: v for k, v in after_obs.items() if k not in {"pack"}}
    compare = _musical_compare_v3(
        before=obs,
        after=after_obs,
        expected_effect=expected_effect,
        diagnosis_category=None if category is None else str(category),
        target_name=track.name,
    )
    report["musical_verification"] = compare

    if compare.get("verdict") != "SUPPORTED":
        rb = _rollback_volume(tools, target_name=track.name, rollback_value=before_vol)
        report["rollback"] = rb
        report["MUSICAL WRITES"] += int(rb.get("MUSICAL_WRITE_COUNT_rollback") or 0)
        report["adjust_recommendation"] = (
            "ADJUST_RECOMMENDED conceptually only — V3 forbids second write; rolled back."
        )
        return _terminal(
            report,
            evidence=evidence,
            status="AUTONOMOUS_CHANGE_ROLLED_BACK",
            musical_decision="ROLLBACK",
            daw=daw,
        )

    final_session = daw.snapshot(include_notes=False)
    attach_tokens(final_session)
    ft = final_session.track_by_name(track.name)
    return _terminal(
        report,
        evidence=evidence,
        status="AUTONOMOUS_IMPROVEMENT_VERIFIED",
        musical_decision="KEEP",
        daw=daw,
        extra={
            "final_project_state": {
                "PROJECT_STATE_TOKEN": final_session.project_token or "",
                "AUDIBLE_STATE_TOKEN": final_session.audible_token or "",
                "target_volume": None if ft is None else float(ft.mixer.volume),
            },
            "open_transaction": bool(write_report.get("open_transaction")),
        },
    )
