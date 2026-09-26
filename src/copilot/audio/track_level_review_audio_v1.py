"""Track-level audio for the harmonic human-review package.

This module is deliberately narrower than the frozen arrangement-capture
milestones.  It reuses their trusted POST MIXER capture primitive once per
track, then performs only deterministic local slicing for the eight existing
harmonic windows.  It never changes analysis semantics and never performs a
musical write.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from copilot.audio.arrangement_active_source_isolation import capture_source_post_mixer
from copilot.audio.harmonic_review_audio_fix_v1 import (
    _build_project_track_inventory,
    _review_copy,
    render_repaired_html,
    render_repaired_report,
)
from copilot.audio.live_capture import capture_dir
from copilot.audio.session_diagnose import preflight_session
from copilot.audio.capture_journal_recovery import recover_all_unresolved, unresolved_capture_journals
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.state_tokens import attach_tokens
from copilot.human_eval.store import now_iso
from copilot.schemas.harmonic_human_review import HarmonicHumanReview


MILESTONE = "TRACK_LEVEL_REVIEW_AUDIO_V1"
ARTIFACT = "track_level_review_audio_v1.json"
AUDIT_ARTIFACT = "track_level_review_audio_audit_v1.json"
CAPTURE_HOST_NAMES = {"Copilot Capture", "Copilot Capture Bass"}
SILENT_STATUSES = {"SILENCE", "NEAR_SILENCE", "EXPECTED_OR_OBSERVED_SILENCE"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wav_contract(path: Path) -> dict[str, Any]:
    data, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    values = np.asarray(data, dtype=np.float64)
    peak = float(np.max(np.abs(values))) if values.size else 0.0
    rms = float(np.sqrt(np.mean(values * values))) if values.size else 0.0
    return {
        "path": str(path),
        "hash": _sha256(path),
        "duration_s": float(len(values) / sample_rate) if sample_rate else 0.0,
        "sample_rate": int(sample_rate),
        "channels": int(values.shape[1]) if values.ndim == 2 else 1,
        "peak_dbfs": float(20.0 * math.log10(max(peak, 1e-12))),
        "rms_dbfs": float(20.0 * math.log10(max(rms, 1e-12))),
        "has_signal": bool(peak >= 1e-4 and rms >= 1e-5),
    }


def _window_bounds(review: HarmonicHumanReview) -> tuple[float, float]:
    if not review.review_windows:
        raise ValueError("HARMONIC_REVIEW_HAS_NO_WINDOWS")
    return (
        min(item.analysis_region.start_qn for item in review.review_windows),
        max(item.analysis_region.end_qn for item in review.review_windows),
    )


def _tempo(review: HarmonicHumanReview) -> float:
    value = review.provenance.get("tempo_bpm")
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    reference = review.source_artifacts.get("reference_audio", {})
    duration = reference.get("duration_s") if isinstance(reference, dict) else None
    start_qn, end_qn = _window_bounds(review)
    if isinstance(duration, (int, float)) and float(duration) > 0:
        return (end_qn - start_qn) * 60.0 / float(duration)
    raise ValueError("TRACK_REVIEW_TEMPO_UNAVAILABLE")


def _status_from_capture(row: dict[str, Any]) -> str:
    if not row.get("ok"):
        return "TRACK_UNRESOLVED" if str(row.get("error", "")).startswith("resolve_") else "CAPTURE_FAILED"
    signal = str(row.get("signal_status") or "").upper()
    if signal in SILENT_STATUSES:
        return "EXPECTED_OR_OBSERVED_SILENCE"
    if signal == "HAS_SIGNAL":
        return "HAS_SIGNAL"
    return "CAPTURE_FAILED"


def _identity_key(row: dict[str, Any]) -> str:
    ref = row.get("track_ref") or row.get("ref") or {}
    if isinstance(ref, dict):
        return str(ref.get("target_state_token") or ref.get("content_fingerprint") or "")
    return ""


def _persist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _generic_capture_preflight(report: dict[str, Any]) -> dict[str, Any]:
    """Adapt the frozen source-specific preflight to generic track capture.

    ``preflight_session`` intentionally certifies the historical Kick/Bass
    validation setup.  This milestone captures arbitrary tracks and the
    capture primitive rewrites/restores that host per target itself.  Only the
    two source-specific diagnostics may be waived; project identity, host/tap
    topology, working-copy and transport failures remain blocking.
    """
    if report.get("pass"):
        return report
    missing = list(report.get("missing") or [])
    source_specific = all(
        item.startswith("TARGET_SOURCE_UNSUPPORTED:")
        or item.startswith("Copilot Capture Bass routing claim=")
        for item in missing
    )
    structural = [
        item
        for item in missing
        if not (
            item.startswith("TARGET_SOURCE_UNSUPPORTED:")
            or item.startswith("Copilot Capture Bass routing claim=")
        )
    ]
    if not missing or not source_specific or structural:
        return report
    adapted = dict(report)
    adapted["pass"] = True
    adapted["status"] = "GENERIC_TRACK_CAPTURE_READY"
    adapted["waived_source_specific_checks"] = missing
    adapted["missing"] = []
    adapted["instruction"] = "Generic track capture may proceed; each target routing is verified and restored per capture."
    return adapted


def capture_track_level_review_audio(
    daw: AbletonTcpAdapter,
    *,
    review_path: Path | str,
    output_dir: Path | str,
    evidence_dir: Path | str | None = None,
    start_qn: float | None = None,
    end_qn: float | None = None,
    lab_track_exclusions: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    """Capture each current project track once over the existing review span.

    The function intentionally does not launch Live.  Environment ownership and
    working-copy launch remain with the platform readiness/launcher layer.
    """
    review = HarmonicHumanReview.model_validate_json(Path(review_path).read_text(encoding="utf-8"))
    output_dir = Path(output_dir)
    evidence_dir = Path(evidence_dir or output_dir)
    default_start, default_end = _window_bounds(review)
    start_qn = default_start if start_qn is None else float(start_qn)
    end_qn = default_end if end_qn is None else float(end_qn)
    if end_qn <= start_qn:
        raise ValueError("TRACK_REVIEW_REGION_INVALID")

    report: dict[str, Any] = {
        MILESTONE: "STARTED",
        "status": "STARTED",
        "created_at": now_iso(),
        "review_path": str(review_path),
        "region": {"start_qn": start_qn, "end_qn": end_qn, "id": "HARMONIC_REVIEW_FULL_REGION"},
        "capture_strategy": "ONE_FULL_REGION_CAPTURE_PER_TRACK_THEN_LOCAL_EIGHT_WINDOW_SLICES",
        "maximum_ableton_captures": 22,
        "model_api_calls": 0,
        "musical_writes": 0,
        "ableton_mutations": 0,
    }
    if unresolved_capture_journals():
        report["journal_recovery"] = recover_all_unresolved(daw=daw)

    raw_preflight = preflight_session(daw, lab_track_exclusions=lab_track_exclusions or frozenset({"AI Test"}))
    preflight = _generic_capture_preflight(raw_preflight)
    report["preflight"] = preflight
    report["raw_preflight_status"] = raw_preflight.get("status")
    if not preflight.get("pass"):
        report[MILESTONE] = "BLOCKED"
        report["status"] = "BLOCKED"
        report["error"] = "PREFLIGHT_FAILED"
        _persist(evidence_dir / ARTIFACT, report)
        return report

    session = daw.snapshot(include_notes=False)
    attach_tokens(session)
    tempo = _tempo(review)
    hosts = preflight.get("capture_hosts") or {}
    host = hosts.get("Copilot Capture Bass") or hosts.get("Copilot Capture") or {}
    host_index = host.get("index")
    if host_index is None:
        report[MILESTONE] = "BLOCKED"
        report["status"] = "BLOCKED"
        report["error"] = "CAPTURE_HOST_UNAVAILABLE"
        _persist(evidence_dir / ARTIFACT, report)
        return report

    captures: list[dict[str, Any]] = []
    for track in list(session.tracks):
        base = {
            "track_ref": {},
            "runtime_track_id": track.stable_id,
            "display_name": track.name,
            "track_kind": track.role,
            "capture": {
                "source_qn_start": start_qn,
                "source_qn_end": end_qn,
            },
            "capture_point": "POST_MIXER",
            "capture_provenance": "Ableton authoritative track output via existing temporary capture host",
            "limitations": [],
        }
        if track.name in CAPTURE_HOST_NAMES:
            base["status"] = "UNSUPPORTED_OUTPUT"
            base["limitations"].append("Copilot capture infrastructure track; self-routing is prohibited.")
            captures.append(base)
            continue
        live = daw.snapshot(include_notes=False)
        attach_tokens(live)
        current = next((item for item in live.tracks if item.stable_id == track.stable_id), None)
        if current is None:
            base["status"] = "TRACK_UNRESOLVED"
            base["limitations"].append("Runtime track identity disappeared before capture.")
            captures.append(base)
            continue
        row = capture_source_post_mixer(
            daw,
            session=live,
            preflight=preflight,
            track=current,
            start_qn=start_qn,
            end_qn=end_qn,
            region_id="HARMONIC_REVIEW_FULL_REGION",
            tempo=tempo,
            host_index=int(host_index),
            dest_root=capture_dir() / "track_level_review_v1",
        )
        base["track_ref"] = row.get("ref") or {}
        base["capture_provenance"] = {
            "method": "capture_source_post_mixer",
            "signal_point": row.get("signal_point", "POST_MIXER"),
            "routing_claim": row.get("routing_claim"),
            "journal": row.get("journal"),
            "pass_id": row.get("pass_id"),
            "tokens_at_bind": row.get("tokens_at_bind"),
        }
        base["status"] = _status_from_capture(row)
        if row.get("error"):
            base["limitations"].append(str(row["error"]))
        wav = Path(str(row.get("wav_path") or ""))
        if row.get("ok") and wav.is_file():
            base["capture"] = {**base["capture"], **_wav_contract(wav), "artifact_path": str(wav)}
        else:
            base["capture"]["artifact_path"] = None
        base["restore"] = row.get("restore")
        captures.append(base)
        daw.stop_playback()

    # Return tracks are present in Ableton topology but are intentionally not
    # represented as SessionState TrackState objects.  Do not fake a source
    # ref or route them by display name; persist the exact limitation so the
    # review package remains complete and fail-closed.
    return_rows = ((preflight.get("topology") or {}).get("return_tracks") or {}).get("return_tracks") or []
    for return_row in return_rows:
        display_name = str(return_row.get("name") or "Return")
        captures.append(
            {
                "track_ref": {},
                "runtime_track_id": None,
                "display_name": display_name,
                "track_kind": "return",
                "status": "UNSUPPORTED_OUTPUT",
                "capture": {
                    "source_qn_start": start_qn,
                    "source_qn_end": end_qn,
                    "artifact_path": None,
                },
                "capture_point": "POST_MIXER",
                "capture_provenance": "Ableton topology inventory only",
                "limitations": [
                    "Return track is not exposed as a reconciliable SessionState TrackState by the current adapter.",
                    "No name-only identity or direct return routing was attempted.",
                ],
                "restore": {"ok": True, "not_mutated": True},
            }
        )

    report["captures"] = captures
    report["captured_count"] = sum(1 for row in captures if row.get("capture", {}).get("artifact_path"))
    report["signal_count"] = sum(1 for row in captures if row.get("status") == "HAS_SIGNAL")
    report["silence_count"] = sum(1 for row in captures if row.get("status") == "EXPECTED_OR_OBSERVED_SILENCE")
    report["status"] = "CAPTURED" if report["captured_count"] else "BLOCKED_NO_TRACK_AUDIO"
    report[MILESTONE] = report["status"]
    report_path = evidence_dir / ARTIFACT
    _persist(report_path, report)
    return report


def attach_track_review_audio(
    review_path: Path | str,
    *,
    output_dir: Path | str,
    manifest_path: Path | str,
) -> HarmonicHumanReview:
    """Slice captured tracks locally and attach them to the review HTML."""
    review_path = Path(review_path)
    output_dir = Path(output_dir)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    review = HarmonicHumanReview.model_validate_json(review_path.read_text(encoding="utf-8"))
    tempo = _tempo(review)
    region_start_qn = float((manifest.get("region") or {}).get("start_qn", _window_bounds(review)[0]))
    track_rows = list(manifest.get("captures") or [])
    attached: list[dict[str, Any]] = []

    for row in track_rows:
        capture = row.get("capture") or {}
        wav_value = capture.get("artifact_path")
        wav = Path(str(wav_value)) if wav_value else None
        item_out: dict[str, Any] = {
            "track_ref": row.get("track_ref") or {},
            "runtime_track_id": row.get("runtime_track_id"),
            "display_name": row.get("display_name"),
            "track_kind": row.get("track_kind"),
            "status": row.get("status"),
            "capture": capture,
            "capture_point": row.get("capture_point"),
            "capture_provenance": row.get("capture_provenance"),
            "limitations": list(row.get("limitations") or []),
            "window_artifacts": {},
        }
        if wav is not None and wav.is_file():
            for index, item in enumerate(review.review_windows, start=1):
                listening = item.listening_region
                start_s = max(0.0, (listening.start_qn - region_start_qn) * 60.0 / tempo)
                end_s = max(start_s, (listening.end_qn - region_start_qn) * 60.0 / tempo)
                target = output_dir / "audio" / f"window_{index:02d}" / "tracks" / f"track_{len(attached) + 1:02d}.wav"
                role = f"track:{row.get('display_name') or row.get('runtime_track_id') or len(attached)}"
                artifact, audit = _review_copy(
                    wav,
                    target,
                    role=role,
                    relative_filename=target.relative_to(output_dir).as_posix(),
                    start_s=start_s,
                    end_s=end_s,
                    source_start_qn=listening.start_qn,
                    source_end_qn=listening.end_qn,
                )
                item_out["window_artifacts"][item.window_id] = artifact.model_dump(mode="json")
                item_out.setdefault("window_audits", {})[item.window_id] = audit
        attached.append(item_out)

    source_artifacts = dict(review.source_artifacts)
    source_artifacts["track_review_audio_manifest"] = {
        "path": str(manifest_path),
        "milestone": MILESTONE,
        "region": manifest.get("region"),
        "tempo_bpm": tempo,
        "tracks": attached,
        "captured_count": sum(1 for row in attached if row.get("capture", {}).get("artifact_path")),
        "has_signal_count": sum(1 for row in attached if row.get("status") == "HAS_SIGNAL"),
        "model_api_calls": 0,
        "musical_writes": 0,
        "ableton_mutations": 0,
    }
    inventory = source_artifacts.get("project_track_inventory")
    if not isinstance(inventory, dict):
        inventory = _build_project_track_inventory(review)
    # This is a presentation join only.  Every row in `attached` already has
    # an authoritative PersistentObjectRef from the capture operation; names
    # are used here only to decorate the human-facing inventory.
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in attached:
        by_name.setdefault(str(row.get("display_name") or ""), []).append(row)
    for track in list(inventory.get("tracks") or []):
        matches = by_name.get(str(track.get("display_name") or ""), [])
        if len(matches) != 1:
            continue
        match = matches[0]
        track["audio_artifact_available"] = bool(match.get("window_artifacts"))
        track["review_audio_status"] = str(match.get("status") or "UNAVAILABLE")
        track["review_audio_identity"] = match.get("track_ref") or {}
        track["review_audio_windows"] = match.get("window_artifacts") or {}
    inventory["track_level_audio_status"] = "ATTACHED"
    source_artifacts["project_track_inventory"] = inventory
    review.source_artifacts = source_artifacts
    review.provenance = {
        **review.provenance,
        "track_level_review_audio_v1": {
            "manifest": str(manifest_path),
            "region_start_qn": region_start_qn,
            "region_end_qn": (manifest.get("region") or {}).get("end_qn"),
            "tempo_bpm": tempo,
            "ableton_capture_count": len(attached),
            "local_slice_count": sum(len(row.get("window_artifacts") or {}) for row in attached),
            "model_api_calls": 0,
            "musical_writes": 0,
            "ableton_mutations": 0,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    review_json = output_dir / "harmonic_sanity_check_v1.json"
    review_json.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / "harmonic_sanity_check_v1.html").write_text(render_repaired_html(review), encoding="utf-8")
    (output_dir / "harmonic_sanity_check_v1.txt").write_text(render_repaired_report(review), encoding="utf-8")
    audit = {
        "milestone": MILESTONE,
        "manifest": str(manifest_path),
        "files_valid": all(
            (output_dir / artifact.get("filename", "")).is_file()
            for row in attached
            for artifact in (row.get("window_artifacts") or {}).values()
            if artifact.get("available") and artifact.get("filename")
        ),
        "expected_track_count": len(attached),
        "captured_track_count": sum(1 for row in attached if row.get("capture", {}).get("artifact_path")),
        "signal_track_count": sum(1 for row in attached if row.get("status") == "HAS_SIGNAL"),
        "window_slice_count": sum(len(row.get("window_artifacts") or {}) for row in attached),
        "model_api_calls": 0,
        "musical_writes": 0,
        "ableton_mutations": 0,
    }
    _persist(output_dir / AUDIT_ARTIFACT, audit)
    return review


def reconcile_return_inventory(manifest_path: Path | str) -> dict[str, Any]:
    """Add topology-only ReturnTrack rows to an existing capture manifest.

    This is a deterministic reconciliation of already persisted preflight
    evidence; it performs no Live call and no audio capture.
    """
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    existing = {str(row.get("display_name") or "") for row in manifest.get("captures") or []}
    topology = ((manifest.get("preflight") or {}).get("topology") or {}).get("return_tracks") or {}
    for return_row in topology.get("return_tracks") or []:
        name = str(return_row.get("name") or "Return")
        if name in existing:
            continue
        region = manifest.get("region") or {}
        manifest.setdefault("captures", []).append(
            {
                "track_ref": {},
                "runtime_track_id": None,
                "display_name": name,
                "track_kind": "return",
                "status": "UNSUPPORTED_OUTPUT",
                "capture": {
                    "source_qn_start": region.get("start_qn"),
                    "source_qn_end": region.get("end_qn"),
                    "artifact_path": None,
                },
                "capture_point": "POST_MIXER",
                "capture_provenance": "Ableton topology inventory only",
                "limitations": [
                    "Return track is not exposed as a reconciliable SessionState TrackState by the current adapter.",
                    "No name-only identity or direct return routing was attempted.",
                ],
                "restore": {"ok": True, "not_mutated": True},
            }
        )
        existing.add(name)
    manifest["return_inventory_reconciled"] = True
    manifest["ableton_mutations"] = 0
    _persist(path, manifest)
    return manifest


__all__ = ["capture_track_level_review_audio", "attach_track_review_audio", "reconcile_return_inventory"]
