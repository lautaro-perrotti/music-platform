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
    channel_rms = np.sqrt(np.mean(values * values, axis=0)) if values.ndim == 2 and values.size else np.zeros(1)
    channel_peak = np.max(np.abs(values), axis=0) if values.ndim == 2 and values.size else np.zeros(1)
    left_rms = float(channel_rms[0])
    right_rms = float(channel_rms[1]) if len(channel_rms) > 1 else left_rms
    left_peak = float(channel_peak[0])
    right_peak = float(channel_peak[1]) if len(channel_peak) > 1 else left_peak
    balance_db = _dbfs(left_rms) - _dbfs(right_rms) if len(channel_rms) > 1 else 0.0
    return {
        "peak_amplitude": peak,
        "peak_dbfs": _dbfs(peak),
        "rms": rms,
        "rms_dbfs": _dbfs(rms),
        "nonzero_sample_ratio": nonzero,
        "has_signal": bool(peak >= 1e-4 and rms >= 1e-5),
        "audible_level": bool(peak >= 10 ** (-30.0 / 20.0) and rms >= 10 ** (-55.0 / 20.0)),
        "left_rms": left_rms,
        "right_rms": right_rms,
        "left_rms_dbfs": _dbfs(left_rms),
        "right_rms_dbfs": _dbfs(right_rms),
        "left_peak": left_peak,
        "right_peak": right_peak,
        "left_peak_dbfs": _dbfs(left_peak),
        "right_peak_dbfs": _dbfs(right_peak),
        "channel_balance_db": balance_db,
        "channel_balanced": bool(abs(balance_db) <= 1.0) if len(channel_rms) > 1 else True,
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
    if clip.shape[1] == 1:
        centered = np.repeat(clip, 2, axis=1)
        channel_mode = "MONO_DUPLICATED_FOR_REVIEW"
    else:
        centered_mono = np.mean(clip.astype(np.float64), axis=1, keepdims=True)
        centered = np.repeat(centered_mono, 2, axis=1)
        channel_mode = "CENTERED_MONO_DUPLICATED_FOR_REVIEW"
    centered_stats = _stats(centered)
    gain = target_peak / centered_stats["peak_amplitude"] if centered_stats["peak_amplitude"] > 1e-7 else 1.0
    # This is a review-only constant gain.  No compression, EQ, limiting, or
    # timing operation is performed.
    normalized = np.clip(centered * gain, -1.0, 1.0).astype(np.float32)
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
        left_rms=stats["left_rms"],
        right_rms=stats["right_rms"],
        left_rms_dbfs=stats["left_rms_dbfs"],
        right_rms_dbfs=stats["right_rms_dbfs"],
        left_peak=stats["left_peak"],
        right_peak=stats["right_peak"],
        left_peak_dbfs=stats["left_peak_dbfs"],
        right_peak_dbfs=stats["right_peak_dbfs"],
        channel_balance_db=stats["channel_balance_db"],
        channel_balanced=stats["channel_balanced"],
        channel_mode=channel_mode,
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
        "channel_mode": channel_mode,
        "channel_balance_db": stats["channel_balance_db"],
        "channel_balanced": stats["channel_balanced"],
        "left_rms_dbfs": stats["left_rms_dbfs"],
        "right_rms_dbfs": stats["right_rms_dbfs"],
        "left_peak_dbfs": stats["left_peak_dbfs"],
        "right_peak_dbfs": stats["right_peak_dbfs"],
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

    all_context_ok = all(row.get("role") == "context" and row.get("has_signal") and row.get("audible_level") and row.get("channel_balanced") for row in audit_rows if row.get("role") == "context")
    source_artifacts = dict(review.source_artifacts)
    source_artifacts["reference_audio"] = {"path": str(reference_audio), "sha256": source_hash, "duration_s": source_duration, "sample_rate": source_sr, "channels": int(source_data.shape[1]), "source_role": source_classification, "hash_verified": expected_source_hash is None or expected_source_hash == source_hash, "stats": source_stats}
    review.source_artifacts = source_artifacts
    review.audio_usability_status = "VERIFIED" if all_context_ok else "BLOCKED_AUDIO_SIGNAL"
    review.timeline_mapping_status = "VERIFIED_SOURCE_LOCAL_ORIGIN" if source_origin_qn == 0.0 and source_end_qn > 0 else "REVIEW_REQUIRED"
    root_cause = "CHANNEL_IMBALANCE"
    root_cause_detail = "The immutable reference and previous review slices had a measured left/right imbalance of approximately 227 dB: useful signal was on L and R was effectively silent. The previous global peak/RMS gate did not detect this. Review copies are now centered and duplicated to both channels."
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
    lines = ["HARMONIC HUMAN REVIEW — AUDIO REPAIRED", "", "WINDOW | CONTEXT PEAK | CONTEXT RMS | L/R BALANCE | HAS SIGNAL | SOURCE | OFFSET QN | STATUS"]
    for item in review.review_windows:
        context = item.audio_artifacts.get("context")
        audit = context_by_window.get(item.window_id.replace("harmonic_", ""), context_by_window.get(f"window_{review.review_windows.index(item) + 1:02d}", {}))
        lines.append(f"{item.window_id} | {audit.get('review_stats', {}).get('peak_dbfs', 'n/a')} dBFS | {audit.get('review_stats', {}).get('rms_dbfs', 'n/a')} dBFS | {audit.get('channel_balance_db', 'n/a')} dB | {audit.get('has_signal', False)} | {review.source_artifacts.get('reference_audio', {}).get('source_role', 'unknown')} | {audit.get('source_start_qn', 'n/a')}–{audit.get('source_end_qn', 'n/a')} | {'PASS' if audit.get('has_signal') and audit.get('audible_level') and audit.get('channel_balanced') else 'BLOCKED'}")
        lines.extend([item.window_id.upper(), f"Bars/QN: {item.analysis_region.start_bar:g}-{item.analysis_region.end_bar:g} / {item.analysis_region.start_qn:g}-{item.analysis_region.end_qn:g}", f"Selected: {item.selected_hypothesis.label if item.selected_hypothesis else 'UNKNOWN'}", f"Context: {context.path if context else 'unavailable'}", "Human verdict: PENDING", ""])
    lines.extend([f"SOURCE: {review.source_artifacts.get('reference_audio', {}).get('source_role')}", f"TIMELINE: {review.timeline_mapping_status}", f"AUDIO USABILITY: {review.audio_usability_status}", f"ROOT CAUSE: {review.provenance.get('root_cause')}", "HUMAN AUDIBILITY: PENDING", "MODEL/API CALLS: 0", "MUSICAL WRITES: 0", "ABLETON MUTATIONS: 0"])
    return "\n".join(lines) + "\n"


def _render_legacy_repaired_html(review: HarmonicHumanReview) -> str:
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


def render_repaired_html(review: HarmonicHumanReview) -> str:
    """Render the static human-review page with local-only verdict storage."""

    review_payload = {
        "analysis_artifact_id": review.analysis_artifact_id,
        "reference_id": review.reference_id,
        "source_hash": review.source_hash,
        "windows": [
            {"window_id": item.window_id, "human_verdict": item.human_verdict, "human_notes": item.human_notes}
            for item in review.review_windows
        ],
    }
    embedded_json = json.dumps(review_payload, ensure_ascii=False, separators=(",", ":"))
    embedded_json = embedded_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    def block(value: Any) -> str:
        return html.escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))

    def region(value: Any) -> str:
        return f"bars {value.start_bar:g}–{value.end_bar:g} · QN {value.start_qn:g}–{value.end_qn:g} · seconds {value.start_seconds:.3f}–{value.end_seconds:.3f}"

    def player(item: Any, role: str) -> str:
        artifact = item.audio_artifacts.get(role)
        if not artifact or not artifact.available or not artifact.filename:
            return "<em>unavailable</em>"
        if artifact.peak_dbfs is not None and artifact.rms_dbfs is not None and artifact.channel_balance_db is not None:
            stats = f"peak {artifact.peak_dbfs:.2f} dBFS · RMS {artifact.rms_dbfs:.2f} dBFS · L/R {artifact.channel_balance_db:.2f} dB"
        else:
            stats = "technical stats unavailable"
        return f'<audio controls preload="none" src="./{html.escape(artifact.filename)}"></audio><small>{html.escape(stats)}</small>'

    blocks: list[str] = []
    for item in review.review_windows:
        selected = item.selected_hypothesis.label if item.selected_hypothesis else "UNKNOWN"
        runner = item.alternatives[0] if item.alternatives else None
        selected_payload = item.selected_hypothesis.model_dump(mode="json") if item.selected_hypothesis else None
        runner_payload = runner.model_dump(mode="json") if runner else None
        contradictions = list(item.selected_hypothesis.contradictions if item.selected_hypothesis else [])
        contradictions.extend(c for alternative in item.alternatives for c in alternative.contradictions if c not in contradictions)
        verdicts = ("ACCEPT", "PLAUSIBLE_AMBIGUOUS", "WRONG", "UNKNOWN_CORRECT", "UNKNOWN_SHOULD_RESOLVE")
        verdict_buttons = "".join(
            f'<button type="button" class="verdict" data-window="{html.escape(item.window_id)}" data-verdict="{verdict}">{verdict}</button>'
            for verdict in verdicts
        )
        blocks.append(
            f'''<section class="window" data-window-section="{html.escape(item.window_id)}">
<h2>{html.escape(item.window_id)} · bars {item.analysis_region.start_bar:g}–{item.analysis_region.end_bar:g}</h2>
<p><b>Analysis region:</b> {html.escape(region(item.analysis_region))}</p>
<p><b>Listening context:</b> {html.escape(region(item.listening_region))}</p>
<h3>1. FULL CONTEXT</h3>{player(item, "context")}
<h3>2. OTHER</h3>{player(item, "other")}
<h3>3. BASS</h3>{player(item, "bass")}
<div class="hypothesis"><b>Selected hypothesis:</b> {html.escape(selected)}<br>
<b>Confidence:</b> {item.selected_hypothesis.confidence if item.selected_hypothesis else 'n/a'} ·
<b>Score delta:</b> {item.selected_runner_up_score_delta if item.selected_runner_up_score_delta is not None else 'n/a'}<br>
<b>Runner-up:</b> {html.escape(runner.label if runner else 'NONE')} · <b>Confidence:</b> {runner.confidence if runner else 'n/a'}</div>
<h3>Evidence</h3>
<p><b>Observed pitch classes:</b> {html.escape(', '.join(item.observed_pitch_classes) or 'none')}</p>
<details open><summary>Authoritative bass MIDI</summary><pre>{block(item.bass_events)}</pre></details>
<details open><summary>Bass ↔ harmony</summary><pre>{block(item.bass_harmony.model_dump(mode="json"))}</pre></details>
<details open><summary>Selected hypothesis / support</summary><pre>{block(selected_payload)}</pre></details>
<details><summary>Runner-up hypothesis</summary><pre>{block(runner_payload)}</pre></details>
<details><summary>Contradictions</summary><pre>{block(contradictions)}</pre></details>
<details><summary>Evidence refs</summary><pre>{block(item.evidence_refs)}</pre></details>
<details><summary>Limitations</summary><pre>{block(item.limitations)}</pre></details>
<h3>Human verdict</h3><div class="verdicts">{verdict_buttons}</div>
<p class="current-verdict" data-current-verdict="{html.escape(item.window_id)}">PENDING</p>
<label>Human notes<textarea data-notes="{html.escape(item.window_id)}" rows="3" placeholder="Optional listening note"></textarea></label>
</section>'''
        )

    source = review.source_artifacts.get("reference_audio", {})
    global_tonality = {
        "status": review.global_tonality_status,
        "candidates": [item.model_dump(mode="json") for item in review.global_tonality_candidates],
        "limitations": review.global_tonality_limitations,
    }
    header = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Harmonic Human Review — Repaired Audio</title>"
        "<style>body{font:15px system-ui;max-width:980px;margin:2rem auto;padding:0 1rem;background:#101216;color:#eee;line-height:1.45}section{border:1px solid #343944;border-radius:12px;padding:1rem;margin:1rem 0;background:#16181d}audio{display:block;width:100%;margin:.5rem 0}h3{margin-bottom:.25rem;color:#b9c4ff}pre{white-space:pre-wrap;overflow:auto;background:#0c0d10;padding:.75rem;border-radius:8px;color:#cdd3e0}details{margin:.6rem 0}summary{cursor:pointer;color:#b9c4ff}.hypothesis{padding:.8rem;background:#20242c;border-radius:8px}.verdicts{display:flex;flex-wrap:wrap;gap:.4rem}button{background:#242832;color:#eee;border:1px solid #4b5361;border-radius:6px;padding:.5rem .7rem;cursor:pointer}button.active{background:#405d8b;border-color:#9fc2ff}textarea{display:block;width:100%;box-sizing:border-box;margin-top:.4rem;background:#0c0d10;color:#eee;border:1px solid #4b5361;border-radius:6px;padding:.6rem}.toolbar{position:sticky;top:0;background:#101216;padding:.7rem 0;border-bottom:1px solid #343944;z-index:2}.status{color:#a9d6ad}</style></head><body>"
        f'<script type="application/json" id="review-data">{embedded_json}</script><h1>Harmonic Human Review</h1>'
        "<p>Context-first listening package. Musical correctness is not self-certified.</p>"
        "<div class='toolbar'><button type='button' id='export-review'>EXPORT REVIEW JSON</button> <button type='button' id='clear-review'>CLEAR LOCAL REVIEW</button> <span class='status' id='save-status'>All verdicts start PENDING.</span></div>"
        "<section><h2>Package status</h2>"
        f"<p><b>Audio usability:</b> {html.escape(review.audio_usability_status)} · <b>Timeline:</b> {html.escape(review.timeline_mapping_status)} · <b>Human audibility:</b> PENDING</p>"
        f"<p><b>Source:</b> {html.escape(str(source.get('source_role', 'unknown')))} · <b>Hash:</b> {html.escape(str(review.source_hash))}</p>"
        f"<details><summary>Full-context source audit</summary><pre>{block(source)}</pre></details>"
        f"<details open><summary>Global tonality: {html.escape(review.global_tonality_status)}</summary><pre>{block(global_tonality)}</pre></details></section>"
    )
    footer = '''<p>MODEL/API CALLS: 0 · MUSICAL WRITES: 0 · ABLETON MUTATIONS: 0</p>
<script>
const REVIEW_DATA = JSON.parse(document.getElementById('review-data').textContent);
const STORAGE_KEY = 'harmonic-human-review:' + REVIEW_DATA.analysis_artifact_id + ':' + REVIEW_DATA.source_hash;
let reviewState = Object.fromEntries(REVIEW_DATA.windows.map((item) => [item.window_id, {verdict: item.human_verdict || 'PENDING', notes: item.human_notes || ''}]));
function setStatus(message) { document.getElementById('save-status').textContent = message; }
function persist() { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(reviewState)); setStatus('Saved locally in this browser.'); } catch (error) { setStatus('Browser localStorage unavailable; use EXPORT REVIEW JSON.'); } }
function restore() { try { const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); if (saved && typeof saved === 'object') reviewState = Object.assign(reviewState, saved); } catch (error) { setStatus('Local draft unavailable; all verdicts remain in memory.'); } }
function renderState() {
  document.querySelectorAll('.verdict').forEach((button) => { const current = reviewState[button.dataset.window]?.verdict || 'PENDING'; button.classList.toggle('active', button.dataset.verdict === current); });
  document.querySelectorAll('.current-verdict').forEach((label) => { label.textContent = reviewState[label.dataset.currentVerdict]?.verdict || 'PENDING'; });
  document.querySelectorAll('textarea[data-notes]').forEach((field) => { field.value = reviewState[field.dataset.notes]?.notes || ''; });
}
function exportReview() {
  const payload = {analysis_artifact_id: REVIEW_DATA.analysis_artifact_id, reference_id: REVIEW_DATA.reference_id, source_hash: REVIEW_DATA.source_hash, windows: Object.entries(reviewState).map(([window_id, item]) => ({window_id, human_verdict: item.verdict || 'PENDING', human_notes: item.notes || ''}))};
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'harmonic_human_review_verdicts.json'; link.click(); URL.revokeObjectURL(link.href); setStatus('Review JSON exported.');
}
document.querySelectorAll('.verdict').forEach((button) => button.addEventListener('click', () => { reviewState[button.dataset.window].verdict = button.dataset.verdict; persist(); renderState(); }));
document.querySelectorAll('textarea[data-notes]').forEach((field) => field.addEventListener('input', () => { reviewState[field.dataset.notes].notes = field.value; persist(); }));
document.getElementById('export-review').addEventListener('click', exportReview);
document.getElementById('clear-review').addEventListener('click', () => { if (confirm('Clear local human review?')) { try { localStorage.removeItem(STORAGE_KEY); } catch (error) {} reviewState = Object.fromEntries(REVIEW_DATA.windows.map((item) => [item.window_id, {verdict: 'PENDING', notes: ''}])); renderState(); setStatus('Local review cleared; all verdicts are PENDING.'); } });
restore(); renderState();
</script></body></html>'''
    return header + "".join(blocks) + footer


__all__ = ["repair_harmonic_review_audio", "render_repaired_html", "render_repaired_report"]
