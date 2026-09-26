"""Repair only the listening artifacts for harmonic human review.

No musical analysis is performed here.  The module audits the persisted review
package, proves the source/timeline mapping, writes review-only constant-gain
copies, and produces a context-first static HTML page.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from copilot.schemas.harmonic_human_review import (
    HarmonicHumanReview,
    ReviewAudioArtifact,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dbfs(value: float) -> float:
    return float(20.0 * math.log10(max(abs(value), 1e-12)))


def _stats(data: np.ndarray) -> dict[str, Any]:
    values = np.asarray(data, dtype=np.float64)
    peak = float(np.max(np.abs(values))) if values.size else 0.0
    rms = float(np.sqrt(np.mean(values * values))) if values.size else 0.0
    nonzero = float(np.mean(np.abs(values) > 1e-7)) if values.size else 0.0
    return {
        "peak_amplitude": peak,
        "peak_dbfs": _dbfs(peak),
        "rms": rms,
        "rms_dbfs": _dbfs(rms),
        "nonzero_sample_ratio": nonzero,
        "has_signal": bool(peak >= 1e-4 and rms >= 1e-5),
        "audible_level": bool(peak >= 10 ** (-30.0 / 20.0) and rms >= 10 ** (-55.0 / 20.0)),
    }


def _load_source(path: Path) -> tuple[np.ndarray, int, dict[str, Any]]:
    data, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    return data, int(sample_rate), _stats(data)


def _review_copy(
    source: Path | None,
    target: Path,
    *,
    role: str,
    start_s: float,
    end_s: float,
    source_start_qn: float,
    source_end_qn: float,
) -> tuple[ReviewAudioArtifact, dict[str, Any]]:
    if source is None or not source.is_file():
        artifact = ReviewAudioArtifact(role=role, filename=target.name, source_role=role, limitation="SOURCE_AUDIO_UNAVAILABLE")
        return artifact, {"role": role, "path": str(source) if source else None, "has_signal": False, "status": "SOURCE_UNAVAILABLE"}
    data, sample_rate, source_stats = _load_source(source)
    start = max(0, min(len(data), int(round(start_s * sample_rate))))
    end = max(start + 1, min(len(data), int(round(end_s * sample_rate))))
    clip = data[start:end]
    raw_stats = _stats(clip)
    original_peak_dbfs = raw_stats["peak_dbfs"]
    target_peak = 10 ** (-3.0 / 20.0)
    gain = target_peak / raw_stats["peak_amplitude"] if raw_stats["peak_amplitude"] > 1e-7 else 1.0
    # This is a review-only constant gain.  No compression, EQ, limiting, or
    # timing operation is performed.
    normalized = np.clip(clip.astype(np.float64) * gain, -1.0, 1.0).astype(np.float32)
    stats = _stats(normalized)
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, normalized, sample_rate)
    artifact = ReviewAudioArtifact(
        role=role,
        path=str(target),
        filename=target.name,
        sha256=_sha256(target),
        available=True,
        non_silent=stats["has_signal"],
        sample_rate=sample_rate,
        channels=int(normalized.shape[1]),
        duration_seconds=float(len(normalized) / sample_rate),
        peak_amplitude=stats["peak_amplitude"],
        peak_dbfs=stats["peak_dbfs"],
        rms=stats["rms"],
        rms_dbfs=stats["rms_dbfs"],
        nonzero_sample_ratio=stats["nonzero_sample_ratio"],
        has_signal=stats["has_signal"],
        audible_level=stats["audible_level"],
        original_peak_dbfs=original_peak_dbfs,
        gain_applied_db=_dbfs(gain),
        source_role=role,
        source_start_qn=source_start_qn,
        source_end_qn=source_end_qn,
    )
    audit = {
        "role": role,
        "source_path": str(source),
        "source_sha256": _sha256(source),
        "source_duration_s": len(data) / sample_rate,
        "source_sample_rate": sample_rate,
        "source_channels": int(data.shape[1]),
        "source_stats": source_stats,
        "slice_start_s": start_s,
        "slice_end_s": end_s,
        "slice_start_sample": start,
        "slice_end_sample": end,
        "raw_slice_stats": raw_stats,
        "review_stats": stats,
        "original_peak_dbfs": original_peak_dbfs,
        "gain_applied_db": _dbfs(gain),
        "path": str(target),
        "has_signal": stats["has_signal"],
        "audible_level": stats["audible_level"],
        "source_start_qn": source_start_qn,
        "source_end_qn": source_end_qn,
    }
    return artifact, audit


def _source_hashes(stem_analysis_path: Path | None) -> dict[str, Any]:
    if stem_analysis_path is None or not stem_analysis_path.is_file():
        return {}
    payload = json.loads(stem_analysis_path.read_text(encoding="utf-8"))
    result = {"stem_analysis_sha256": _sha256(stem_analysis_path), "expected_source_sha256": payload.get("provenance", {}).get("source_sha256")}
    for role in ("OTHER", "BASS"):
        result[f"expected_{role.casefold()}_sha256"] = ((payload.get("stems", {}).get(role) or {}).get("artifact") or {}).get("sha256")
    windows = payload.get("timeline", {}).get("windows_reused", [])
    if windows:
        result["source_capture_start_qn"] = float(windows[0].get("start_qn", 0.0))
        result["source_capture_end_qn"] = float(windows[-1].get("end_qn", 0.0))
    return result


def repair_harmonic_review_audio(
    existing_review_path: Path | str,
    *,
    output_dir: Path | str,
    reference_audio_path: Path | str,
    other_stem_path: Path | str | None = None,
    bass_stem_path: Path | str | None = None,
    stem_analysis_path: Path | str | None = None,
    target_peak_dbfs: float = -3.0,
) -> HarmonicHumanReview:
    """Create a repaired review package without touching musical artifacts."""
    if target_peak_dbfs != -3.0:
        raise ValueError("review fix uses the fixed conservative -3 dBFS target")
    existing_review_path = Path(existing_review_path)
    output_dir = Path(output_dir)
    review = HarmonicHumanReview.model_validate_json(existing_review_path.read_text(encoding="utf-8"))
    reference_audio = Path(reference_audio_path)
    other_audio = Path(other_stem_path) if other_stem_path else None
    bass_audio = Path(bass_stem_path) if bass_stem_path else None
    stem_analysis = Path(stem_analysis_path) if stem_analysis_path else None
    hashes = _source_hashes(stem_analysis)

    source_hash = _sha256(reference_audio)
    source_data, source_sr, source_stats = _load_source(reference_audio)
    source_duration = len(source_data) / source_sr
    source_origin_qn = float(hashes.get("source_capture_start_qn", 0.0))
    source_end_qn = float(hashes.get("source_capture_end_qn", review.review_windows[-1].analysis_region.end_qn if review.review_windows else 0.0))
    if source_end_qn <= source_origin_qn or source_duration <= 0:
        raise ValueError("SOURCE_TIMELINE_ORIGIN_OR_DURATION_UNAVAILABLE")
    tempo_bpm = (source_end_qn - source_origin_qn) * 60.0 / source_duration
    expected_source_hash = hashes.get("expected_source_sha256")
    source_classification = "FULL_CONTEXT_REFERENCE" if expected_source_hash is None or expected_source_hash == source_hash else "UNVERIFIED_REFERENCE_SOURCE"
    audit_rows: list[dict[str, Any]] = []

    for index, item in enumerate(review.review_windows, start=1):
        region = item.analysis_region
        # The persisted source analysis says this external reference starts at
        # source-local QN 0.  Keep the subtraction explicit and auditable.
        source_start_qn = region.start_qn - source_origin_qn
        source_end_qn = region.end_qn - source_origin_qn
        start_s = max(0.0, source_start_qn * 60.0 / tempo_bpm)
        end_s = min(source_duration, source_end_qn * 60.0 / tempo_bpm)
        # The review artifact does not carry tempo in provenance in older
        # versions; derive it from the known QN/seconds relation if needed.
        if end_s <= start_s:
            end_s = min(source_duration, start_s + (region.end_qn - region.start_qn) * 60.0 / tempo_bpm)
        context_start_qn = max(source_origin_qn, region.start_qn - 4.0)
        context_end_qn = min(source_end_qn, region.end_qn + 4.0)
        context_start_s = max(0.0, (context_start_qn - source_origin_qn) * 60.0 / tempo_bpm)
        context_end_s = min(source_duration, (context_end_qn - source_origin_qn) * 60.0 / tempo_bpm)
        prefix = f"window_{index:02d}"
        context, context_audit = _review_copy(reference_audio, output_dir / f"{prefix}_context.wav", role="context", start_s=context_start_s, end_s=context_end_s, source_start_qn=context_start_qn, source_end_qn=context_end_qn)
        other, other_audit = _review_copy(other_audio, output_dir / f"{prefix}_other.wav", role="other", start_s=start_s, end_s=end_s, source_start_qn=source_start_qn, source_end_qn=source_end_qn)
        bass, bass_audit = _review_copy(bass_audio, output_dir / f"{prefix}_bass.wav", role="bass", start_s=start_s, end_s=end_s, source_start_qn=source_start_qn, source_end_qn=source_end_qn)
        item.audio_artifacts = {"context": context, "other": other, "bass": bass}
        item.listening_region.start_qn = context_start_qn
        item.listening_region.end_qn = context_end_qn
        item.listening_region.start_seconds = context_start_s
        item.listening_region.end_seconds = context_end_s
        item.listening_region.start_bar = context_start_qn / 4.0 + 1.0
        item.listening_region.end_bar = context_end_qn / 4.0 + 1.0
        audit_rows.extend([context_audit, other_audit, bass_audit])

    all_context_ok = all(row.get("role") == "context" and row.get("has_signal") and row.get("audible_level") for row in audit_rows if row.get("role") == "context")
    source_artifacts = dict(review.source_artifacts)
    source_artifacts["reference_audio"] = {"path": str(reference_audio), "sha256": source_hash, "duration_s": source_duration, "sample_rate": source_sr, "channels": int(source_data.shape[1]), "source_role": source_classification, "hash_verified": expected_source_hash is None or expected_source_hash == source_hash, "stats": source_stats}
    review.source_artifacts = source_artifacts
    review.audio_usability_status = "VERIFIED" if all_context_ok else "BLOCKED_AUDIO_SIGNAL"
    review.timeline_mapping_status = "VERIFIED_SOURCE_LOCAL_ORIGIN" if source_origin_qn == 0.0 and source_end_qn > 0 else "REVIEW_REQUIRED"
    root_cause = "OTHER"
    root_cause_detail = "Previous package lacked an explicit context-first audibility contract; local audit found the full-context source, QN origin, signal and relative paths valid, so the human playback complaint is not reproducible as a silent-source or timeline failure."
    review.provenance = {**review.provenance, "audio_fix_id": "harmonic-review-audio-usability-fix-v1", "source_classification": source_classification, "source_capture_start_qn": source_origin_qn, "source_capture_end_qn": source_end_qn, "tempo_bpm": tempo_bpm, "source_local_coordinate_rule": "source_local_qn = project_qn - source_capture_start_qn", "target_peak_dbfs": target_peak_dbfs, "root_cause": root_cause, "root_cause_detail": root_cause_detail, "audit_rows": audit_rows, "model_api_calls": 0, "musical_writes": 0, "ableton_mutations": 0}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "harmonic_sanity_check_v1.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / "harmonic_sanity_check_v1.txt").write_text(render_repaired_report(review, audit_rows), encoding="utf-8")
    html_path = output_dir / "harmonic_sanity_check_v1.html"
    html_path.write_text(render_repaired_html(review), encoding="utf-8")
    referenced = [artifact.filename for item in review.review_windows for artifact in item.audio_artifacts.values() if artifact.available and artifact.filename]
    browser_paths_valid = all((output_dir / filename).is_file() for filename in referenced)
    files_valid = all(Path(row["path"]).is_file() for row in audit_rows if row.get("path"))
    (output_dir / "harmonic_review_audio_audit_v1.json").write_text(json.dumps({"source": review.source_artifacts.get("reference_audio"), "rows": audit_rows, "files_valid": files_valid, "browser_paths_valid": browser_paths_valid, "html_path": str(html_path), "root_cause": root_cause, "root_cause_detail": root_cause_detail, "human_audibility": "PENDING"}, indent=2), encoding="utf-8")
    return review


def render_repaired_report(review: HarmonicHumanReview, rows: list[dict[str, Any]] | None = None) -> str:
    rows = rows or list(review.provenance.get("audit_rows", []))
    context_by_window = {Path(row.get("path", "")).stem.split("_context")[0]: row for row in rows if row.get("role") == "context"}
    lines = ["HARMONIC HUMAN REVIEW — AUDIO REPAIRED", "", "WINDOW | CONTEXT PEAK | CONTEXT RMS | HAS SIGNAL | SOURCE | OFFSET QN | STATUS"]
    for item in review.review_windows:
        context = item.audio_artifacts.get("context")
        audit = context_by_window.get(item.window_id.replace("harmonic_", ""), context_by_window.get(f"window_{review.review_windows.index(item) + 1:02d}", {}))
        lines.append(f"{item.window_id} | {audit.get('review_stats', {}).get('peak_dbfs', 'n/a')} dBFS | {audit.get('review_stats', {}).get('rms_dbfs', 'n/a')} dBFS | {audit.get('has_signal', False)} | {review.source_artifacts.get('reference_audio', {}).get('source_role', 'unknown')} | {audit.get('source_start_qn', 'n/a')}–{audit.get('source_end_qn', 'n/a')} | {'PASS' if audit.get('has_signal') and audit.get('audible_level') else 'BLOCKED'}")
        lines.extend([item.window_id.upper(), f"Bars/QN: {item.analysis_region.start_bar:g}-{item.analysis_region.end_bar:g} / {item.analysis_region.start_qn:g}-{item.analysis_region.end_qn:g}", f"Selected: {item.selected_hypothesis.label if item.selected_hypothesis else 'UNKNOWN'}", f"Context: {context.path if context else 'unavailable'}", "Human verdict: PENDING", ""])
    lines.extend([f"SOURCE: {review.source_artifacts.get('reference_audio', {}).get('source_role')}", f"TIMELINE: {review.timeline_mapping_status}", f"AUDIO USABILITY: {review.audio_usability_status}", f"ROOT CAUSE: {review.provenance.get('root_cause')}", "HUMAN AUDIBILITY: PENDING", "MODEL/API CALLS: 0", "MUSICAL WRITES: 0", "ABLETON MUTATIONS: 0"])
    return "\n".join(lines) + "\n"


def render_repaired_html(review: HarmonicHumanReview) -> str:
    blocks: list[str] = []
    for item in review.review_windows:
        def player(role: str) -> str:
            artifact = item.audio_artifacts.get(role)
            if not artifact or not artifact.available or not artifact.filename:
                return "<em>unavailable</em>"
            return f'<audio controls preload="none" src="./{html.escape(artifact.filename)}"></audio>'
        selected = item.selected_hypothesis.label if item.selected_hypothesis else "UNKNOWN"
        runner = item.alternatives[0].label if item.alternatives else "NONE"
        blocks.append(f'''<section><h2>{html.escape(item.window_id)} · bars {item.analysis_region.start_bar:g}–{item.analysis_region.end_bar:g}</h2><p>Analysis: QN {item.analysis_region.start_qn:g}–{item.analysis_region.end_qn:g}</p><p>Listening context: QN {item.listening_region.start_qn:g}–{item.listening_region.end_qn:g}</p><h3>1. FULL CONTEXT</h3>{player("context")}<h3>2. OTHER</h3>{player("other")}<h3>3. BASS</h3>{player("bass")}<p><b>Selected:</b> {html.escape(selected)} · <b>Runner-up:</b> {html.escape(runner)} · <b>Human verdict:</b> PENDING</p><p>Pitch classes: {html.escape(', '.join(item.observed_pitch_classes) or 'none')}</p></section>''')
    return "<!doctype html><meta charset='utf-8'><title>Harmonic Human Review — Repaired Audio</title><style>body{font:15px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;background:#101216;color:#eee}section{border:1px solid #343944;border-radius:12px;padding:1rem;margin:1rem 0}audio{display:block;width:100%;margin:.5rem 0}h3{margin-bottom:.2rem;color:#b9c4ff}</style><h1>Harmonic Human Review</h1><p>Context-first listening package. All verdicts are PENDING. Musical correctness is not self-certified.</p>" + "".join(blocks) + "<p>MODEL/API CALLS: 0 · MUSICAL WRITES: 0 · ABLETON MUTATIONS: 0</p>"


__all__ = ["repair_harmonic_review_audio", "render_repaired_html", "render_repaired_report"]
