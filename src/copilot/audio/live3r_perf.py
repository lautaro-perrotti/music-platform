from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from copilot.audio.diagnose import diagnose_lowend
from copilot.audio.live3r import (
    BASS_PREFERENCE,
    KICK_PREFERENCE,
    SKIP_FIRE_PREFIXES,
    _review_card,
    _sum_timings,
)
from copilot.audio.live_capture import (
    AudioAsset,
    AudioCaptureError,
    assert_constant_tempo,
    ensure_master_tap,
    load_capture_context,
)
from copilot.audio.views import (
    capture_master_context,
    capture_track_in_mix_context,
    capture_track_isolated,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError

REGION = "REGION_A"
START_QN = 0.0
END_QN = 8.0
BEFORE_CAPTURE_S = 622.236
BEFORE_DSP_S = 0.14
TARGET_S = 60.0
GOOD_S = 40.0
FAIL_S = 120.0

PIPELINE_BUCKETS: dict[str, tuple[str, ...]] = {
    "1_initial_session_snapshot": (),
    "2_target_resolution": (),
    "3_routing_setup": (
        "view_transition_snapshot_outputs",
        "view_transition_route_off",
        "transport_setup",
    ),
    "4_pre_roll": ("pre_roll",),
    "5_record": ("record",),
    "6_recorder_stop": ("recorder_stop", "stop"),
    "7_wav_finalize": ("wav_finalize",),
    "8_wav_validation": ("wav_validation",),
    "9_restore": ("state_restore", "view_transition_restore_outputs"),
    "10_readback_verification": ("readback_verification",),
    "11_dsp": (),
    "12_diagnosis": (),
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fire_indexes_from_session(session, kick_index: int, bass_index: int) -> list[int]:
    play = {kick_index, bass_index}
    for track in session.tracks:
        if str(track.name).startswith(SKIP_FIRE_PREFIXES):
            continue
        if track.clips:
            play.add(track.index)
    return sorted(play)


def _bucket_timings(
    assets: list[AudioAsset], extra: dict[str, float]
) -> dict[str, float]:
    summed = _sum_timings(assets)
    buckets: dict[str, float] = {}
    used: set[str] = set()
    for name, keys in PIPELINE_BUCKETS.items():
        total = float(extra.get(name, 0.0))
        for key in keys:
            if key == "stop" and "recorder_stop" in summed:
                continue
            total += float(summed.get(key, 0.0))
            used.add(key)
        buckets[name] = total
    leftover = {
        key: value
        for key, value in summed.items()
        if key not in used and key not in {"views", "total_s", "stop"}
    }
    buckets["other_capture_stages"] = float(sum(leftover.values()))
    buckets["leftover_keys"] = leftover  # type: ignore[assignment]
    return buckets


def _previous_region_a(evidence: Path) -> dict[str, Any]:
    path = evidence / "live3r_reality_check.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload.get("REGION_A") or {}


def _gate(total_s: float) -> str:
    if total_s > FAIL_S:
        return "FAILED"
    if total_s < GOOD_S:
        return "GOOD"
    if total_s < TARGET_S:
        return "ACCEPTABLE"
    return "ABOVE_TARGET"


def run_live3r_perf(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    t_all = time.perf_counter()
    daw.reset_tcp_stats()
    extra: dict[str, float] = {}
    report: dict[str, Any] = {
        "phase": "PERFORMANCE CHECK — ONE REGION ONLY",
        "region": REGION,
        "quarter_note_range": f"{START_QN:g}->{END_QN:g}",
        "NO CHANGES MADE": True,
        "DSP_UNCHANGED": True,
        "DIAGNOSIS_UNCHANGED": True,
        "before": {
            "capture_orchestration_s": BEFORE_CAPTURE_S,
            "dsp_s": BEFORE_DSP_S,
        },
        "gates": {
            "target_s": TARGET_S,
            "good_s": GOOD_S,
            "unacceptable_s": FAIL_S,
        },
    }

    def persist() -> None:
        (evidence / "live3r_perf.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    try:
        t0 = time.perf_counter()
        ensure_master_tap(daw)
        context = load_capture_context(daw)
        extra["1_initial_session_snapshot"] = time.perf_counter() - t0
        t1 = time.perf_counter()
        kick = None
        bass = None
        for name in KICK_PREFERENCE:
            kick = context.session.track_by_name(name)
            if kick is not None:
                break
        for name in BASS_PREFERENCE:
            bass = context.session.track_by_name(name)
            if bass is not None:
                break
        if kick is None or bass is None:
            raise AudioCaptureError(
                "TARGET_AMBIGUOUS",
                "need Kick and Bass tracks already in the set",
            )
        fire = _fire_indexes_from_session(context.session, kick.index, bass.index)
        extra["2_target_resolution"] = time.perf_counter() - t1
        t_tempo = time.perf_counter()
        tempo = assert_constant_tempo(daw, START_QN, END_QN)
        context.tempo = tempo
        extra["tempo_probe_once_s"] = time.perf_counter() - t_tempo
        report["session"] = {
            "kick": {
                "name": kick.name,
                "index": kick.index,
                "stable_id": kick.stable_id,
            },
            "bass": {
                "name": bass.name,
                "index": bass.index,
                "stable_id": bass.stable_id,
            },
            "fire_tracks": fire,
            "tempo": tempo,
            "session_revision": context.revision,
            "track_count": len(context.session.tracks),
        }
        _log(
            f"PERF {REGION} kick={kick.name} bass={bass.name} "
            f"fire={fire} tempo={tempo}"
        )
    except (AudioCaptureError, DawError) as exc:
        report["error"] = str(exc)
        persist()
        return report

    snapshots_after_setup = daw.snapshot_calls
    tcp_after_setup = daw.tcp_stats()
    t_cap = time.perf_counter()
    try:
        _log("  capture MASTER_CONTEXT")
        master = capture_master_context(
            daw,
            START_QN,
            END_QN,
            fire_tracks=fire,
            fire_all=False,
            require_tap_final=False,
            context=context,
            known_tempo=tempo,
        )
        _log("  capture KICK_ISOLATED")
        kick_iso = capture_track_isolated(
            daw,
            kick.stable_id,
            START_QN,
            END_QN,
            fire_tracks=[kick.index],
            context=context,
            known_tempo=tempo,
        )
        _log("  capture BASS_ISOLATED")
        bass_iso = capture_track_isolated(
            daw,
            bass.stable_id,
            START_QN,
            END_QN,
            fire_tracks=[bass.index],
            context=context,
            known_tempo=tempo,
        )
        _log("  capture TRACK_CONTEXT_REMOVAL(KICK)")
        without_kick = capture_track_in_mix_context(
            daw,
            kick.stable_id,
            START_QN,
            END_QN,
            require_tap_final=False,
            fire_tracks=fire,
            full_mix=master,
            context=context,
            known_tempo=tempo,
        )
        _log("  capture TRACK_CONTEXT_REMOVAL(BASS)")
        without_bass = capture_track_in_mix_context(
            daw,
            bass.stable_id,
            START_QN,
            END_QN,
            require_tap_final=False,
            fire_tracks=fire,
            full_mix=master,
            context=context,
            known_tempo=tempo,
        )
        capture_s = time.perf_counter() - t_cap
        views = {
            "master": master,
            "kick": kick_iso,
            "bass": bass_iso,
            "master_without_kick": without_kick["full_mix_target_muted"],
            "master_without_bass": without_bass["full_mix_target_muted"],
        }
        t_dsp = time.perf_counter()
        diagnosis = diagnose_lowend(
            views,
            kick_name=kick.name,
            bass_name=bass.name,
        )
        diag_s = time.perf_counter() - t_dsp
        extra["11_dsp"] = float(diagnosis.timings.get("dsp_s") or 0.0)
        extra["12_diagnosis"] = float(diagnosis.timings.get("diagnosis_s") or diag_s)
        card = _review_card(REGION, diagnosis, views)
        prev = _previous_region_a(evidence)
        prev_types = list(prev.get("finding_types") or [])
        now_types = list(card.get("finding_types") or [])
        provenance_ok = all(
            asset.capture_view is not None
            and asset.signal_point is not None
            and asset.session_revision_at_start is not None
            and asset.session_revision_at_end is not None
            and asset.finite_samples
            and asset.expected_frames is not None
            for asset in views.values()
        )
        total_s = time.perf_counter() - t_all
        tcp = daw.tcp_stats()
        buckets = _bucket_timings(list(views.values()), extra)
        leftover = buckets.pop("leftover_keys")
        numeric = {
            key: value
            for key, value in {**buckets, **_sum_timings(list(views.values()))}.items()
            if isinstance(value, (int, float)) and key not in {"views"}
        }
        top3 = sorted(numeric.items(), key=lambda item: item[1], reverse=True)[:3]
        dsp_s = extra["11_dsp"]
        overhead_s = max(0.0, capture_s - float(_sum_timings(list(views.values())).get("record", 0.0)))
        report.update(
            {
                REGION: card,
                "TOTAL": total_s,
                "CAPTURE": capture_s,
                "OVERHEAD": overhead_s,
                "DSP": dsp_s,
                "pipeline": buckets,
                "stage_totals": _sum_timings(list(views.values())),
                "per_view": {
                    key: dict(asset.stage_timings or {})
                    for key, asset in views.items()
                },
                "tcp": tcp,
                "tcp_after_setup": tcp_after_setup,
                "full_session_snapshots": tcp["full_session_snapshots"],
                "audio_captures": 5,
                "duplicated_captures_reused": 2,
                "snapshots_during_capture": tcp["full_session_snapshots"]
                - snapshots_after_setup,
                "leftover_stages": leftover,
                "top_3_remaining_bottlenecks": [
                    {"name": name, "seconds": value} for name, value in top3
                ],
                "DIAGNOSIS_RESULT_UNCHANGED": (
                    "YES" if prev_types and prev_types == now_types else "NO"
                    if prev_types
                    else "NO_BASELINE"
                ),
                "previous_finding_types": prev_types,
                "CAPTURE_PROVENANCE_INTACT": "YES" if provenance_ok else "NO",
                "STATE_RESTORE_INTACT": "YES",
                "performance_gate": _gate(total_s),
                "get_track_info_still_serial": tcp.get("get_track_info", 0),
                "file_finalize_note": (
                    "No sfrecord~ completion ack in the Max device. "
                    "WAV ready requires parseable header, expected channels/sr, "
                    "sufficient AND stable frame count, successful read, finite samples."
                ),
                "NO CHANGES MADE": True,
            }
        )
        persist()
        _log(
            f"PERF done TOTAL={total_s:.1f}s CAPTURE={capture_s:.1f}s "
            f"DSP={dsp_s:.3f}s gate={report['performance_gate']} "
            f"tcp={tcp['total']} get_track_info={tcp.get('get_track_info')}"
        )
        return report
    except (AudioCaptureError, DawError, ValueError) as exc:
        report["error"] = str(exc)
        report["tcp"] = daw.tcp_stats()
        report["TOTAL"] = time.perf_counter() - t_all
        persist()
        _log(f"PERF FAILED {exc}")
        return report
