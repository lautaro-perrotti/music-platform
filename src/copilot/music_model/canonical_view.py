"""Build a thin canonical view over existing musical artifacts.

No analyzer runs here.  No measurements are recomputed.  Existing artifacts
remain the authority; this module only projects them into one stable,
provenance-preserving product handoff.
"""

from __future__ import annotations

from typing import Any

from copilot.schemas.bass_musical_model import BassMusicalModel
from copilot.schemas.canonical_music_model import (
    CanonicalDomainView,
    CanonicalMusicModelView,
    CanonicalSourceRef,
    CanonicalTimeline,
)
from copilot.schemas.harmonic_understanding import HarmonicUnderstanding
from copilot.schemas.music_analysis import MusicAnalysisPack
from copilot.schemas.reference_analysis import ReferenceAnalysisPack


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value or {})


def _source_ref(name: str, artifact: Any) -> CanonicalSourceRef | None:
    if artifact is None:
        return None
    payload = _dump(artifact)
    if payload.get("no_write") is False:
        raise ValueError(f"CANONICAL_SOURCE_NOT_READ_ONLY:{name}")
    provenance = dict(payload.get("provenance") or {})
    artifact_id = (
        payload.get("artifact_id")
        or payload.get("reference_id")
        or payload.get("source_analysis_id")
        or provenance.get("analysis_id")
    )
    evidence_refs = _collect_evidence_refs(payload)
    limitations = list(payload.get("limitations") or payload.get("global_limitations") or [])
    return CanonicalSourceRef(
        artifact_name=name,
        schema_version=payload.get("schema_version"),
        artifact_id=str(artifact_id) if artifact_id is not None else None,
        evidence_refs=[str(item) for item in evidence_refs],
        limitations=[str(item) for item in limitations],
        provenance=provenance,
    )


def _collect_evidence_refs(value: Any) -> list[str]:
    """Collect existing refs without creating or reinterpreting evidence."""

    found: list[str] = []
    if isinstance(value, dict):
        refs = value.get("evidence_refs")
        if isinstance(refs, list):
            found.extend(str(item) for item in refs)
        for child in value.values():
            found.extend(_collect_evidence_refs(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_evidence_refs(child))
    return list(dict.fromkeys(found))


def _analysis_timeline(pack: MusicAnalysisPack) -> CanonicalTimeline:
    raw = dict(pack.timeline or {})
    windows = list(pack.windows or [])
    start_qn = raw.get("project_qn_start")
    end_qn = raw.get("project_qn_end")
    if start_qn is None and windows:
        start_qn = min(float(window.start_beat) for window in windows)
    if end_qn is None and windows:
        end_qn = max(float(window.end_beat) for window in windows)
    # Window bounds are existing analysis coordinates, not a newly inferred
    # project range.  Keep them only when the pack itself supplies them.
    return CanonicalTimeline(
        tempo_bpm=pack.tempo_bpm,
        project_qn_start=float(start_qn) if start_qn is not None else None,
        project_qn_end=float(end_qn) if end_qn is not None else None,
        local_seconds_start=(float(raw["local_seconds_start"]) if raw.get("local_seconds_start") is not None else None),
        local_seconds_end=(float(raw["local_seconds_end"]) if raw.get("local_seconds_end") is not None else None),
        reference_region=dict(raw.get("reference_region") or {}),
        bar_mapping=dict(raw.get("bar_mapping") or {}),
        limitations=list(raw.get("limitations") or []),
    )


def _domain_refs(*refs: CanonicalSourceRef | None) -> list[str]:
    return [ref.artifact_name for ref in refs if ref is not None]


def build_canonical_music_model_view(
    pack: MusicAnalysisPack,
    *,
    reference_analysis: ReferenceAnalysisPack | None = None,
    bass_model: BassMusicalModel | None = None,
    harmonic_understanding: HarmonicUnderstanding | None = None,
    stem_analysis: Any | None = None,
) -> CanonicalMusicModelView:
    """Project current artifacts into one read-only producer-facing view.

    The broad ``MusicAnalysisPack`` is required as the current shared
    ingestion/evidence boundary.  Richer symbolic artifacts are optional and
    are referenced only when already available; this function never derives
    notes, chords, groove, or section labels.
    """

    analysis_ref = _source_ref("MusicAnalysisPack", pack)
    reference_ref = _source_ref("ReferenceAnalysisPack", reference_analysis)
    stem_ref = _source_ref("StemReferenceAnalysis", stem_analysis)
    bass_ref = _source_ref("BassMusicalModel", bass_model)
    harmony_ref = _source_ref("HarmonicUnderstanding", harmonic_understanding)
    sources = {
        ref.artifact_name: ref
        for ref in (analysis_ref, reference_ref, stem_ref, bass_ref, harmony_ref)
        if ref is not None
    }

    structure = CanonicalDomainView(
        status="SUPPORTED" if pack.structural_regions or pack.sections else "INSUFFICIENT_EVIDENCE",
        value={
            "regions": [item.model_dump(mode="json") for item in pack.structural_regions],
            "sections": [item.model_dump(mode="json") for item in pack.sections],
            "section_hypotheses": [item.model_dump(mode="json") for item in pack.section_hypotheses],
            "transitions": [item.model_dump(mode="json") for item in pack.transitions],
        },
        source_artifacts=_domain_refs(analysis_ref, reference_ref),
        evidence_refs=list(pack.evidence_refs),
        limitations=list(pack.limitations),
        conflicts=list(pack.contradictions),
    )

    rhythm_value = {
        "tempo_bpm": pack.tempo_bpm,
        "window_bars": pack.window_bars,
        "groove_windows": [
            {
                "index": window.index,
                "start_beat": window.start_beat,
                "end_beat": window.end_beat,
                "groove": window.groove.model_dump(mode="json"),
            }
            for window in pack.windows
        ],
    }
    rhythm_has_evidence = any(window.groove.model_dump(exclude_defaults=True) for window in pack.windows)
    rhythm = CanonicalDomainView(
        status="SUPPORTED" if rhythm_has_evidence else "INSUFFICIENT_EVIDENCE",
        value=rhythm_value,
        source_artifacts=_domain_refs(analysis_ref),
        evidence_refs=list(pack.evidence_refs),
        limitations=list(pack.limitations),
    )

    if bass_model is None:
        bass = CanonicalDomainView(
            status="NOT_AVAILABLE",
            limitations=["BASS_MUSICAL_MODEL_NOT_ATTACHED_TO_THIS_VIEW"],
            source_artifacts=_domain_refs(analysis_ref),
        )
    else:
        bass_payload = bass_model.model_dump(mode="json")
        bass = CanonicalDomainView(
            status="SUPPORTED" if bass_model.event_count else "INSUFFICIENT_EVIDENCE",
            value={
                "event_count": bass_model.event_count,
                "pitch_material": bass_payload["pitch_material"],
                "interval_language": bass_payload["interval_language"],
                "rhythmic_cells": bass_payload["rhythmic_cells"],
                "motifs": bass_payload["motifs"],
                "phrases": bass_payload["phrases"],
                "tonal_hypotheses": bass_payload["tonal_hypotheses"],
                "selected_tonality": bass_payload["selected_tonality"],
            },
            source_artifacts=_domain_refs(bass_ref),
            evidence_refs=_collect_evidence_refs(bass_payload),
            limitations=list(bass_model.limitations),
        )

    if harmonic_understanding is None:
        harmony = CanonicalDomainView(
            status="EVIDENCE_ONLY" if any(window.harmony.model_dump(exclude_defaults=True) for window in pack.windows) else "NOT_AVAILABLE",
            value={
                "windows": [
                    {
                        "index": window.index,
                        "start_beat": window.start_beat,
                        "end_beat": window.end_beat,
                        "harmony": window.harmony.model_dump(mode="json"),
                    }
                    for window in pack.windows
                ]
            },
            source_artifacts=_domain_refs(analysis_ref),
            evidence_refs=list(pack.evidence_refs),
            limitations=["RICH_HARMONIC_UNDERSTANDING_NOT_ATTACHED_TO_THIS_VIEW"],
        )
    else:
        harmony_payload = harmonic_understanding.model_dump(mode="json")
        harmony = CanonicalDomainView(
            status=("SUPPORTED" if harmonic_understanding.selected_tonality or any(window.selected for window in harmonic_understanding.windows) else "INSUFFICIENT_EVIDENCE"),
            value={
                "windows": harmony_payload["windows"],
                "harmonic_rhythm": harmony_payload["harmonic_rhythm"],
                "tonal_hypotheses": harmony_payload["tonal_hypotheses"],
                "selected_tonality": harmony_payload["selected_tonality"],
                "bass_harmony_relationships": harmony_payload["bass_harmony_relationships"],
            },
            source_artifacts=_domain_refs(harmony_ref),
            evidence_refs=_collect_evidence_refs(harmony_payload),
            limitations=list(harmonic_understanding.limitations),
        )

    limitations = list(dict.fromkeys(
        list(pack.limitations)
        + list(_analysis_timeline(pack).limitations)
        + [item for source in sources.values() for item in source.limitations]
    ))
    contradictions = list(pack.contradictions)
    return CanonicalMusicModelView(
        reference_id=pack.reference_id,
        project_id=pack.project_id,
        identity={
            "reference_id": pack.reference_id,
            "project_id": pack.project_id,
            "reference_state_token": pack.tokens.reference_state_token,
            "target_state_token": pack.tokens.target_state_token,
        },
        timeline=_analysis_timeline(pack),
        structure=structure,
        rhythm=rhythm,
        bass=bass,
        harmony=harmony,
        sources=sources,
        limitations=limitations,
        contradictions=contradictions,
    )


__all__ = ["build_canonical_music_model_view"]
