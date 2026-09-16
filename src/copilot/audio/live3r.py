from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from copilot.audio.diagnose import diagnose_lowend
from copilot.audio.live_capture import (
    AudioAsset,
    AudioCaptureError,
    ensure_master_tap,
)
from copilot.audio.views import (
    capture_master_context,
    capture_track_in_mix_context,
    capture_track_isolated,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError

# Not LIVE-3 diagnosis clips. No CLEAN/TEMPORAL/SPECTRAL labels.
KICK_PREFERENCE = ("LIVE22 Kick", "LIVE3 Kick")
BASS_PREFERENCE = ("LIVE22 Bass", "LIVE3 Bass")
SKIP_FIRE_PREFIXES = ("LIVE3 ",)
REGIONS: dict[str, tuple[float, float]] = {
    "REGION_A": (0.0, 8.0),
    "REGION_B": (8.0, 16.0),
    "REGION_C": (0.0, 16.0),
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _pick_track(session, names: tuple[str, ...]):
    for name in names:
        track = session.track_by_name(name)
        if track is not None:
            return track
    return None


def _fire_indexes(daw: AbletonTcpAdapter, kick_index: int, bass_index: int) -> list[int]:
    count = int(daw.health().get("track_count") or 0)
    play = {kick_index, bass_index}
    for index in range(count):
        info = daw.get_track_info(index)
        name = str(info.get("name") or "")
        if name.startswith(SKIP_FIRE_PREFIXES):
            continue
        slots = info.get("clip_slots") or []
        if slots and slots[0].get("has_clip"):
            play.add(index)
    return sorted(play)


def _sum_timings(assets: list[AudioAsset]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for asset in assets:
        for key, value in (asset.stage_timings or {}).items():
            totals[key] = totals.get(key, 0.0) + float(value)
    totals["views"] = float(len(assets))
    totals["total_s"] = float(sum(totals.get(k, 0.0) for k in totals if k != "views"))
    return totals


def _capture_region(
    daw: AbletonTcpAdapter,
    *,
    kick,
    bass,
    start_qn: float,
    end_qn: float,
    fire: list[int],
) -> dict[str, AudioAsset]:
    t0 = time.perf_counter()
    _log(f"  capture MASTER_CONTEXT {start_qn:g}->{end_qn:g}qn")
    master = capture_master_context(
        daw,
        start_qn,
        end_qn,
        fire_tracks=fire,
        fire_all=False,
        require_tap_final=False,
    )
    _log("  capture KICK_ISOLATED")
    kick_iso = capture_track_isolated(
        daw, kick.stable_id, start_qn, end_qn, fire_tracks=[kick.index]
    )
    _log("  capture BASS_ISOLATED")
    bass_iso = capture_track_isolated(
        daw, bass.stable_id, start_qn, end_qn, fire_tracks=[bass.index]
    )
    _log("  capture TRACK_CONTEXT_REMOVAL(KICK)")
    without_kick = capture_track_in_mix_context(
        daw,
        kick.stable_id,
        start_qn,
        end_qn,
        require_tap_final=False,
        fire_tracks=fire,
        full_mix=master,
    )
    _log("  capture TRACK_CONTEXT_REMOVAL(BASS)")
    without_bass = capture_track_in_mix_context(
        daw,
        bass.stable_id,
        start_qn,
        end_qn,
        require_tap_final=False,
        fire_tracks=fire,
        full_mix=master,
    )
    captured = {
        "master": master,
        "kick": kick_iso,
        "bass": bass_iso,
        "master_without_kick": without_kick["full_mix_target_muted"],
        "master_without_bass": without_bass["full_mix_target_muted"],
    }
    captured["_capture_s"] = time.perf_counter() - t0  # type: ignore[assignment]
    captured["_full_mix"] = without_kick["full_mix"]  # type: ignore[assignment]
    return captured  # type: ignore[return-value]


def _review_card(label: str, diagnosis, captured: dict[str, AudioAsset]) -> dict[str, Any]:
    findings = [item.model_dump() for item in diagnosis.findings[:4]]
    action = None
    for cand in diagnosis.candidate_actions:
        if cand.action_type != "NO_CHANGE":
            action = cand.model_dump()
            break
        action = cand.model_dump()
    return {
        "region": label,
        "quarter_note_range": diagnosis.region,
        "diagnosis": diagnosis.primary_hypothesis.statement
        if diagnosis.primary_hypothesis
        else diagnosis.findings[0].type.value
        if diagnosis.findings
        else "INSUFFICIENT_EVIDENCE",
        "finding_types": [item.type.value for item in diagnosis.findings],
        "confidence": diagnosis.confidence.value,
        "findings": findings,
        "candidate_action": action,
        "feature_sources": diagnosis.feature_sources,
        "limitations": diagnosis.limitations,
        "user_facing": diagnosis.user_facing,
        "wavs": {
            key: {
                "capture_id": asset.capture_id,
                "path": asset.file_path,
                "view": asset.capture_view.value if asset.capture_view else None,
                "signal_point": asset.signal_point.value if asset.signal_point else None,
            }
            for key, asset in captured.items()
            if isinstance(asset, AudioAsset)
        },
        "NO CHANGES MADE": True,
    }


def _copy_wavs(card: dict[str, Any], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for key, info in (card.get("wavs") or {}).items():
        src = Path(str(info["path"]))
        if src.is_file():
            copied = dest / f"{key}_{src.name}"
            shutil.copy2(src, copied)
            info["review_copy"] = str(copied)


def run_live3r(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    t_all = time.perf_counter()
    out_dir = evidence / "live3r"
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "phase": "LIVE-3R",
        "LOW_END_DIAGNOSIS_FIXTURE_BASELINE": "VERIFIED",
        "REAL_SESSION_LOW_END_DIAGNOSIS": "AWAITING_HUMAN_REVIEW",
        "PRODUCTION_MUSIC_DIAGNOSIS": "PARTIAL",
        "NO CHANGES MADE": True,
    }

    def persist() -> None:
        (evidence / "live3r_reality_check.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    try:
        ensure_master_tap(daw)
        session = daw.snapshot(include_notes=False)
        kick = _pick_track(session, KICK_PREFERENCE)
        bass = _pick_track(session, BASS_PREFERENCE)
        if kick is None or bass is None:
            raise AudioCaptureError(
                "TARGET_AMBIGUOUS",
                "need Kick and Bass tracks already in the set; did not create any",
            )
        fire = _fire_indexes(daw, kick.index, bass.index)
        report["session"] = {
            "note": (
                "Used the currently loaded Live set. Did not write MIDI, "
                "did not load LIVE-3 fixture labels, did not construct CLEAN/TEMPORAL/SPECTRAL."
            ),
            "kick": {"name": kick.name, "index": kick.index, "stable_id": kick.stable_id},
            "bass": {"name": bass.name, "index": bass.index, "stable_id": bass.stable_id},
            "fire_tracks": fire,
        }
        _log(f"LIVE-3R targets kick={kick.name} bass={bass.name} fire={fire}")
    except (AudioCaptureError, DawError) as exc:
        report["error"] = str(exc)
        persist()
        return report

    cards: dict[str, Any] = {}
    timing_rows: list[dict[str, Any]] = []

    for label, (start_qn, end_qn) in REGIONS.items():
        _log(f"LIVE-3R {label} {start_qn:g}->{end_qn:g} quarter_note")
        try:
            captured = _capture_region(
                daw,
                kick=kick,
                bass=bass,
                start_qn=start_qn,
                end_qn=end_qn,
                fire=fire,
            )
            capture_s = float(captured.pop("_capture_s"))  # type: ignore[arg-type]
            captured.pop("_full_mix", None)
            views = {k: v for k, v in captured.items() if isinstance(v, AudioAsset)}
            t_diag = time.perf_counter()
            diagnosis = diagnose_lowend(
                views,
                kick_name=kick.name,
                bass_name=bass.name,
            )
            diag_s = time.perf_counter() - t_diag
            card = _review_card(label, diagnosis, views)
            card["timings"] = {
                "capture_s": capture_s,
                "dsp_s": diagnosis.timings.get("dsp_s"),
                "diagnosis_s": diagnosis.timings.get("diagnosis_s") or diag_s,
                "stage_totals": _sum_timings(list(views.values())),
                "per_view": {
                    key: dict(asset.stage_timings or {})
                    for key, asset in views.items()
                },
            }
            _copy_wavs(card, out_dir / label)
            cards[label] = card
            timing_rows.append(
                {
                    "region": label,
                    "capture_s": capture_s,
                    "diagnosis_s": diag_s,
                    "stages": card["timings"]["stage_totals"],
                }
            )
            _log(f"  {label} types={card['finding_types']} conf={card['confidence']}")
            _log("--- user-facing ---")
            _log(diagnosis.user_facing)
            _log("-------------------")
            report[label] = card
            persist()
        except (AudioCaptureError, DawError, ValueError) as exc:
            report[label] = {"error": str(exc), "NO CHANGES MADE": True}
            _log(f"  {label} FAILED {exc}")
            persist()

    report["human_review"] = cards
    report["capture_timing_breakdown"] = {
        "per_region": timing_rows,
        "total_s": time.perf_counter() - t_all,
        "note": (
            "Not a new architecture. Stages are summed across views. "
            "record ≈ musical duration; remaining time is TCP/orchestration."
        ),
    }
    report["REAL_SESSION_LOW_END_DIAGNOSIS"] = "AWAITING_HUMAN_REVIEW"
    persist()
    (out_dir / "REVIEW.md").write_text(
        _review_markdown(report), encoding="utf-8"
    )
    _log("LIVE-3R done — AWAITING_HUMAN_REVIEW")
    return report


def _review_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# LIVE-3R human review",
        "",
        "NO CHANGES MADE.",
        "",
        "REAL_SESSION_LOW_END_DIAGNOSIS = AWAITING_HUMAN_REVIEW",
        "",
    ]
    for label in REGIONS:
        card = report.get(label) or {}
        if "error" in card:
            lines.extend([f"## {label}", "", str(card["error"]), ""])
            continue
        lines.extend(
            [
                f"## {label}",
                "",
                f"Region: {card.get('quarter_note_range')}",
                f"Diagnosis: {card.get('diagnosis')}",
                f"Confidence: {card.get('confidence')}",
                f"Types: {card.get('finding_types')}",
                f"Candidate: {((card.get('candidate_action') or {}).get('action_type'))}",
                "",
                "```",
                str(card.get("user_facing") or ""),
                "```",
                "",
                f"Limitations: {card.get('limitations')}",
                f"kick_event_source: {((card.get('feature_sources') or {}).get('kick_events') or {}).get('view')}",
                "",
                "NO CHANGES MADE.",
                "",
            ]
        )
    return "\n".join(lines)
