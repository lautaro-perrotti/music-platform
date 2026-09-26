"""Prepare a human-inspectable review package for harmonic hypotheses."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any

import soundfile as sf

from copilot.schemas.harmonic_human_review import (
    BassRelationshipSummary,
    HarmonicAlternative,
    HarmonicHumanReview,
    HarmonicHumanReviewWindow,
    ReviewAudioArtifact,
    ReviewRegion,
)
from copilot.schemas.harmonic_understanding import HarmonicUnderstanding
from copilot.schemas.musical_understanding import MusicalUnderstanding


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discover_master(stem_analysis_path: Path | None) -> Path | None:
    if stem_analysis_path is None:
        return None
    candidates = sorted((stem_analysis_path.parent / "immutable_source").glob("*.wav"))
    return candidates[0] if candidates else None


def _artifact_source(path: Path | None, expected_hash: str | None = None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"path": str(path) if path else None, "available": False, "hash_verified": None}
    actual = _sha256(path)
    return {"path": str(path), "available": True, "sha256": actual, "expected_sha256": expected_hash, "hash_verified": expected_hash is None or actual == expected_hash}


def _slice_audio(source: Path | None, target: Path, *, start_s: float, end_s: float, role: str) -> ReviewAudioArtifact:
    if source is None or not source.is_file():
        return ReviewAudioArtifact(role=role, filename=target.name, limitation="SOURCE_AUDIO_UNAVAILABLE")
    data, sample_rate = sf.read(source, always_2d=True, dtype="float32")
    start = max(0, min(len(data), int(round(start_s * sample_rate))))
    end = max(start + 1, min(len(data), int(round(end_s * sample_rate))))
    clip = data[start:end]
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, clip, sample_rate)
    return ReviewAudioArtifact(
        role=role,
        path=str(target),
        filename=target.name,
        sha256=_sha256(target),
        available=True,
        non_silent=bool(clip.size and float((clip.astype("float64") ** 2).mean()) > 1e-10),
        sample_rate=int(sample_rate),
        channels=int(clip.shape[1]),
        duration_seconds=float(len(clip) / sample_rate),
    )


def _region(window: Any, tempo_bpm: float, *, pad_bars: float = 0.0) -> ReviewRegion:
    seconds_per_qn = 60.0 / tempo_bpm
    return ReviewRegion(
        start_qn=max(0.0, float(window.start_qn) - pad_bars * 4.0),
        end_qn=float(window.end_qn) + pad_bars * 4.0,
        start_bar=max(1.0, float(window.start_bar) - pad_bars),
        end_bar=float(window.end_bar) + pad_bars,
        start_seconds=max(0.0, (float(window.start_qn) - pad_bars * 4.0) * seconds_per_qn),
        end_seconds=(float(window.end_qn) + pad_bars * 4.0) * seconds_per_qn,
    )


def _alternative(candidate: Any, observed: list[str], evidence: str) -> HarmonicAlternative:
    expected = list(candidate.pitch_classes)
    return HarmonicAlternative(
        label=candidate.label,
        root=candidate.root,
        quality=candidate.quality,
        score=float(candidate.score),
        confidence=float(candidate.confidence),
        observed_tones=[pitch for pitch in expected if pitch in observed],
        missing_or_inferred=[pitch for pitch in expected if pitch not in observed],
        contradictions=[pitch for pitch in observed if pitch not in expected],
        evidence_refs=[evidence, *list(candidate.evidence_refs)],
    )


def build_harmonic_human_review(
    harmonic_path: Path | str,
    understanding_path: Path | str,
    *,
    output_dir: Path | str,
    reference_audio_path: Path | str | None = None,
    other_stem_path: Path | str | None = None,
    bass_stem_path: Path | str | None = None,
    stem_analysis_path: Path | str | None = None,
    context_bars: float = 1.0,
) -> HarmonicHumanReview:
    harmonic_path = Path(harmonic_path)
    understanding_path = Path(understanding_path)
    output_dir = Path(output_dir)
    harmonic = HarmonicUnderstanding.model_validate_json(harmonic_path.read_text(encoding="utf-8"))
    understanding = MusicalUnderstanding.model_validate_json(understanding_path.read_text(encoding="utf-8"))
    stem_analysis = Path(stem_analysis_path) if stem_analysis_path else None
    reference_audio = Path(reference_audio_path) if reference_audio_path else _discover_master(stem_analysis)
    other_audio = Path(other_stem_path) if other_stem_path else None
    bass_audio = Path(bass_stem_path) if bass_stem_path else None

    source_artifacts = {
        "harmonic_understanding": _artifact_source(harmonic_path),
        "musical_understanding": _artifact_source(understanding_path),
        "reference_audio": _artifact_source(reference_audio),
        "other_stem": _artifact_source(other_audio),
        "bass_stem": _artifact_source(bass_audio),
    }
    source_hash = source_artifacts["reference_audio"].get("sha256") or harmonic.provenance.get("understanding_sha256", "")
    if stem_analysis and stem_analysis.is_file():
        stem_payload = json.loads(stem_analysis.read_text(encoding="utf-8"))
        source_artifacts["stem_analysis"] = _artifact_source(stem_analysis)
        expected_source = stem_payload.get("provenance", {}).get("source_sha256")
        source_artifacts["reference_audio"]["expected_sha256"] = expected_source
        source_artifacts["reference_audio"]["hash_verified"] = expected_source is None or source_artifacts["reference_audio"].get("sha256") == expected_source
        for role, path in (("OTHER", other_audio), ("BASS", bass_audio)):
            expected = ((stem_payload.get("stems", {}).get(role) or {}).get("artifact") or {}).get("sha256")
            key = role.casefold() + "_stem"
            source_artifacts[key]["expected_sha256"] = expected
            source_artifacts[key]["hash_verified"] = expected is None or source_artifacts[key].get("sha256") == expected

    review_windows: list[HarmonicHumanReviewWindow] = []
    for index, window in enumerate(harmonic.windows, start=1):
        analysis_region = _region(window, harmonic.tempo_bpm)
        listening_region = _region(window, harmonic.tempo_bpm, pad_bars=context_bars)
        prefix = f"window_{index:02d}"
        audio_artifacts = {
            "master": _slice_audio(reference_audio, output_dir / f"{prefix}_master.wav", start_s=analysis_region.start_seconds, end_s=analysis_region.end_seconds, role="master"),
            "other": _slice_audio(other_audio, output_dir / f"{prefix}_other.wav", start_s=analysis_region.start_seconds, end_s=analysis_region.end_seconds, role="other"),
            "bass": _slice_audio(bass_audio, output_dir / f"{prefix}_bass.wav", start_s=analysis_region.start_seconds, end_s=analysis_region.end_seconds, role="bass"),
            "master_context": _slice_audio(reference_audio, output_dir / f"{prefix}_master_context.wav", start_s=listening_region.start_seconds, end_s=listening_region.end_seconds, role="master_context"),
        }
        events = [event for event in understanding.bass.pitch_events if window.start_qn <= float(event.onset_qn if event.onset_qn is not None else event.grid.onset_qn) < window.end_qn]
        observed = [pitch for pitch, value in window.pitch_class_energy.items() if float(value) >= 0.08]
        evidence = f"{harmonic.reference_id}:{window.window_id}"
        candidates = [_alternative(candidate, observed, evidence) for candidate in window.hypotheses]
        selected = _alternative(window.selected, observed, evidence) if window.selected else None
        alternatives = [candidate for candidate in candidates if not selected or candidate.label != selected.label][:5]
        runner_up = alternatives[0] if alternatives else None
        relationships = [item for item in harmonic.bass_harmony_relationships if item.window_id == window.window_id]
        counts: dict[str, int] = {}
        relation_events: list[dict[str, Any]] = []
        for item in relationships:
            role = "UNRESOLVED" if item.role == "UNKNOWN" else item.role
            counts[role] = counts.get(role, 0) + 1
            relation_events.append({"event_id": item.event_id, "pitch_class": item.bass_pitch_class, "chord": item.chord_label, "classification": role, "confidence": item.confidence})
        limitations = list(window.limitations)
        if window.selected is None:
            limitations.append("NO_CHORD_CROSSED_ACCEPTANCE_MARGIN")
        review_windows.append(HarmonicHumanReviewWindow(
            window_id=window.window_id,
            analysis_region=analysis_region,
            listening_region=listening_region,
            audio_artifacts=audio_artifacts,
            selected_hypothesis=selected,
            alternatives=alternatives,
            selected_runner_up_score_delta=(selected.score - runner_up.score) if selected and runner_up else None,
            observed_pitch_classes=observed,
            bass_events=[{"event_id": event.event_id, "onset_qn": event.onset_qn, "midi_note": event.midi_note, "pitch_class": event.pitch_class, "duration_qn": event.duration_qn, "confidence": event.confidence} for event in events],
            bass_harmony=BassRelationshipSummary(counts=counts, event_count=len(relation_events), events=relation_events),
            evidence_refs=list(window.evidence_refs),
            limitations=limitations,
            human_review_note="STRONGLY_AMBIGUOUS" if runner_up and selected and selected.score - runner_up.score < 0.05 else None,
        ))

    review = HarmonicHumanReview(
        analysis_artifact_id=f"{harmonic.schema_version}:{_sha256(harmonic_path)}",
        reference_id=harmonic.reference_id,
        source_hash=source_hash,
        source_artifacts=source_artifacts,
        review_windows=review_windows,
        global_tonality_status="INSUFFICIENT_EVIDENCE" if harmonic.selected_tonality is None else "SUPPORTED",
        global_tonality_candidates=list(harmonic.tonal_hypotheses),
        global_tonality_limitations=[item for item in harmonic.limitations if "TONALITY" in item or "tonal" in item.casefold()] + ["HUMAN_REVIEW_REQUIRED_NO_TONALITY_CERTIFICATION"],
        provenance={"harmonic_path": str(harmonic_path), "understanding_path": str(understanding_path), "context_bars": context_bars, "model_api_calls": 0, "musical_writes": 0, "ableton_mutations": 0},
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "harmonic_sanity_check_v1.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / "harmonic_sanity_check_v1.txt").write_text(render_harmonic_human_review_report(review), encoding="utf-8")
    (output_dir / "harmonic_sanity_check_v1.html").write_text(render_harmonic_human_review_html(review), encoding="utf-8")
    return review


def render_harmonic_human_review_report(review: HarmonicHumanReview) -> str:
    lines = ["HARMONIC HUMAN REVIEW", ""]
    for item in review.review_windows:
        selected = item.selected_hypothesis.label if item.selected_hypothesis else "UNKNOWN"
        runner = item.alternatives[0].label if item.alternatives else "NONE"
        master = item.audio_artifacts["master"].path if item.audio_artifacts.get("master") else "unavailable"
        lines.extend([item.window_id.upper(), f"Bars: {item.analysis_region.start_bar:g}-{item.analysis_region.end_bar:g} | QN: {item.analysis_region.start_qn:g}-{item.analysis_region.end_qn:g}", f"Selected: {selected}", f"Runner-up: {runner}", f"Confidence: {item.selected_hypothesis.confidence if item.selected_hypothesis else 'n/a'}", f"Observed pitch classes: {', '.join(item.observed_pitch_classes) or 'none'}", f"Bass relationship counts: {item.bass_harmony.counts}", f"Master: {master}", "Human verdict: PENDING", ""])
    lines.extend([f"GLOBAL TONALITY: {review.global_tonality_status}", "", "WAITING FOR HUMAN VERDICTS", "MODEL/API CALLS: 0", "MUSICAL WRITES: 0", "ABLETON MUTATIONS: 0"])
    return "\n".join(lines) + "\n"


def render_harmonic_human_review_html(review: HarmonicHumanReview) -> str:
    blocks: list[str] = []
    for item in review.review_windows:
        def audio(role: str) -> str:
            artifact = item.audio_artifacts.get(role)
            if not artifact or not artifact.available or not artifact.filename:
                return "<em>unavailable</em>"
            return f'<audio controls preload="none" src="{html.escape(artifact.filename)}"></audio>'
        selected = item.selected_hypothesis.label if item.selected_hypothesis else "UNKNOWN"
        alternatives = ", ".join(f"{alt.label} ({alt.score:.3f})" for alt in item.alternatives) or "none"
        evidence = html.escape(json.dumps(item.model_dump(mode="json"), indent=2, ensure_ascii=False))
        blocks.append(f'''<section><h2>{html.escape(item.window_id)} · bars {item.analysis_region.start_bar:g}–{item.analysis_region.end_bar:g}</h2><p><b>Selected:</b> {html.escape(selected)} · <b>Verdict:</b> PENDING</p><p><b>Alternatives:</b> {html.escape(alternatives)}</p><p><b>Pitch classes:</b> {html.escape(', '.join(item.observed_pitch_classes) or 'none')}</p><p><b>Bass relationships:</b> {html.escape(json.dumps(item.bass_harmony.counts, sort_keys=True))}</p><div class="audio"><label>Master {audio('master')}</label><label>Other {audio('other')}</label><label>Bass {audio('bass')}</label></div><details><summary>Evidence</summary><pre>{evidence}</pre></details></section>''')
    return "<!doctype html><meta charset='utf-8'><title>Harmonic Human Review</title><style>body{font:15px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem;background:#101216;color:#eee}section{border:1px solid #343944;border-radius:12px;padding:1rem;margin:1rem 0}audio{display:block;margin:.4rem 0}.audio{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem}pre{white-space:pre-wrap;font-size:12px}</style><h1>Harmonic Human Review</h1><p>All verdicts are PENDING. This page does not certify musical correctness.</p>" + "".join(blocks) + f"<h2>Global tonality: {html.escape(review.global_tonality_status)}</h2><p>MODEL/API CALLS: 0 · MUSICAL WRITES: 0 · ABLETON MUTATIONS: 0</p>"


__all__ = ["build_harmonic_human_review", "render_harmonic_human_review_report", "render_harmonic_human_review_html"]
