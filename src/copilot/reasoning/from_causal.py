"""Pack typed causal observations into EvidencePack items. No judgment."""

from __future__ import annotations

from typing import Any

from copilot.reasoning.schema import ANALYSIS_VERSION, SCHEMA_VERSION
from copilot.schemas.evidence import (
    CaptureQuality,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)

DOMAIN = "fullmix+lowend+causal"
CAUSAL_ANALYSIS = "causal-obs-1"


def merge_causal_into_pack(
    pack: EvidencePack,
    causal: dict[str, Any],
) -> EvidencePack:
    obtained = causal.get("evidence_obtained") or {}
    region = pack.region
    project_token = pack.project_token
    audible_token = pack.audible_token
    target_token = pack.target_token
    items = list(pack.items)
    items.extend(
        _causal_items(
            obtained,
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
            source_ref=f"causal:{causal.get('artifact') or 'inline'}",
        )
    )
    limitations = list(pack.limitations)
    limitations.append(
        ObservationLimitation(
            code="CAUSAL_EVIDENCE_V0",
            detail=(
                "Causal observations are arrangement/MIDI/automation/routing facts. "
                "They do not imply musical fault or required action."
            ),
        )
    )
    for block in obtained.values():
        if not isinstance(block, dict):
            continue
        for note in block.get("limitations") or []:
            limitations.append(
                ObservationLimitation(
                    code="CAUSAL_LIMITATION",
                    detail=str(note),
                )
            )
    if causal.get("not_obtained"):
        for miss in causal["not_obtained"]:
            limitations.append(
                ObservationLimitation(
                    code="CAUSAL_NOT_OBTAINED",
                    detail=f"{miss.get('kind')}: {miss.get('reason')}",
                )
            )
    return EvidencePack(
        pack_id=pack.pack_id,
        analysis_version=f"{ANALYSIS_VERSION}+{CAUSAL_ANALYSIS}",
        prompt_schema_version=SCHEMA_VERSION,
        region=pack.region,
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        target_token=pack.target_token,
        alignment_claim=pack.alignment_claim,
        alignment_envelope_ms=pack.alignment_envelope_ms,
        items=items,
        entities=list(pack.entities),
        limitations=limitations,
        domain=DOMAIN,
    )


def _causal_items(
    obtained: dict[str, Any],
    *,
    region: str,
    project_token: str,
    audible_token: str,
    target_token: str | None,
    source_ref: str,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    arr = obtained.get("ArrangementObservation") or {}
    if arr:
        items.extend(
            [
                _fact(
                    "cx.arr.drums_class",
                    "arrangement_drums_class",
                    arr.get("drums_class"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _fact(
                    "cx.arr.sub_sub_bass_class",
                    "arrangement_sub_sub_bass_class",
                    arr.get("sub_sub_bass_class"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _fact(
                    "cx.arr.rose_bass_class",
                    "arrangement_rose_bass_class",
                    arr.get("rose_bass_class"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _fact(
                    "cx.arr.tempo_bpm",
                    "arrangement_tempo_bpm",
                    arr.get("tempo_bpm"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                    unit="bpm",
                ),
                _fact(
                    "cx.arr.measured_event_qn",
                    "fullmix_event_mapped_qn",
                    arr.get("measured_event_qn"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
            ]
        )
    clips = obtained.get("ClipActivityObservation") or {}
    if clips:
        items.extend(
            [
                _fact(
                    "cx.clip.overlapping_count",
                    "arrangement_overlapping_clip_count",
                    clips.get("overlapping_clip_count"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                    unit="count",
                ),
                _fact(
                    "cx.clip.tracks_active",
                    "arrangement_tracks_active",
                    clips.get("tracks_active"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _fact(
                    "cx.clip.boundaries",
                    "arrangement_clip_boundaries",
                    clips.get("clips"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
            ]
        )
    midi = obtained.get("MidiActivityObservation") or {}
    if midi:
        items.append(
            _fact(
                "cx.midi.tracks",
                "midi_note_activity_by_track",
                [
                    {
                        "track": t.get("track"),
                        "note_count_in_region": t.get("note_count_in_region"),
                        "note_bins_1qn": t.get("note_bins_1qn"),
                    }
                    for t in (midi.get("tracks") or [])
                ],
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            )
        )
        # Flatten bin counts for numeric grounding.
        for t in midi.get("tracks") or []:
            track = str(t.get("track") or "unknown").replace(" ", "_").lower()
            items.append(
                _meas(
                    f"cx.midi.{track}.note_count",
                    "midi_note_count_in_region",
                    int(t.get("note_count_in_region") or 0),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                    unit="count",
                )
            )
    auto = obtained.get("AutomationObservation") or {}
    if auto:
        items.append(
            _fact(
                "cx.auto.tracks_with_events",
                "automation_float_events_by_track",
                auto.get("tracks_with_float_events"),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            )
        )
    routing = obtained.get("RoutingObservation") or {}
    if routing:
        items.append(
            _fact(
                "cx.routing.live_snapshot",
                "routing",
                {
                    "ok": routing.get("ok"),
                    "tracks": routing.get("tracks"),
                    "master": routing.get("master"),
                    "error": routing.get("error"),
                },
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            )
        )
    near = obtained.get("midi_density_near_fullmix_events") or []
    if near:
        items.append(
            _fact(
                "cx.midi.near_events",
                "midi_density_near_fullmix_events",
                near,
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            )
        )
    return items


def _fact(
    evidence_id: str,
    name: str,
    value: Any,
    region: str,
    project_token: str,
    audible_token: str,
    target_token: str | None,
    source_ref: str,
    unit: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=EvidenceKind.FACT,
        source_ref=source_ref,
        region=region,
        analysis_version=CAUSAL_ANALYSIS,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.OK,
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
    )


def _meas(
    evidence_id: str,
    name: str,
    value: Any,
    region: str,
    project_token: str,
    audible_token: str,
    target_token: str | None,
    source_ref: str,
    unit: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=EvidenceKind.MEASUREMENT,
        source_ref=source_ref,
        region=region,
        analysis_version=CAUSAL_ANALYSIS,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.OK,
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
    )
