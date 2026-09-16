"""Pack CausalTrace facts into EvidencePack items for Astra."""

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

TRACE_VERSION = "causal-trace-1"
DOMAIN = "fullmix+lowend+causal-trace"


def merge_causal_trace_into_pack(pack: EvidencePack, trace_payload: dict[str, Any]) -> EvidencePack:
    trace = trace_payload.get("trace") or {}
    primary_id = trace.get("primary_gap_event_id")
    region = pack.region
    project_token = pack.project_token
    audible_token = pack.audible_token
    target_token = pack.target_token
    source_ref = f"causal_trace:{trace_payload.get('artifact') or 'inline'}"
    items = list(pack.items)

    primary = next(
        (e for e in (trace.get("events") or []) if e.get("event_id") == primary_id),
        None,
    )
    if primary:
        items.extend(
            [
                _fact(
                    "ct.event.id",
                    "causal_trace_primary_event_id",
                    primary_id,
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _fact(
                    "ct.event.kind",
                    "causal_trace_primary_event_kind",
                    primary.get("kind"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _meas(
                    "ct.event.audio_start_s",
                    "causal_trace_event_audio_start_s",
                    primary.get("event_audio_start_s"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                    unit="s",
                ),
                _meas(
                    "ct.event.audio_end_s",
                    "causal_trace_event_audio_end_s",
                    primary.get("event_audio_end_s"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                    unit="s",
                ),
                _meas(
                    "ct.event.start_qn",
                    "causal_trace_event_start_qn",
                    primary.get("event_start_qn"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                    unit="qn",
                ),
                _meas(
                    "ct.event.end_qn",
                    "causal_trace_event_end_qn",
                    primary.get("event_end_qn"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                    unit="qn",
                ),
            ]
        )

    covering = [c for c in (trace.get("track_coverage") or []) if c.get("has_material_during_event")]
    items.append(
        _fact(
            "ct.clip.covering",
            "causal_trace_clip_coverage_during_event",
            [
                {
                    "track": c.get("track"),
                    "clip": c.get("clip_name"),
                    "clip_start_qn": c.get("clip_start_qn"),
                    "clip_end_qn": c.get("clip_end_qn"),
                    "overlap_qn": c.get("event_interval_overlap_qn"),
                    "source": c.get("source"),
                }
                for c in covering
            ],
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )

    midi_rows = []
    for m in trace.get("midi_coverage") or []:
        midi_rows.append(
            {
                "track": m.get("track"),
                "status": m.get("status"),
                "active_count": len(m.get("notes_active_at_event") or []),
                "onset_count": len(m.get("note_onsets_inside_event") or []),
                "end_count": len(m.get("note_ends_inside_event") or []),
                "notes_active_at_event": m.get("notes_active_at_event"),
                "nearest_note_before": m.get("nearest_note_before"),
                "nearest_note_after": m.get("nearest_note_after"),
                "source": m.get("source"),
            }
        )
        track_key = str(m.get("track") or "unknown").replace(" ", "_").lower()
        items.append(
            _meas(
                f"ct.midi.{track_key}.active_count",
                "causal_trace_midi_active_count",
                len(m.get("notes_active_at_event") or []),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
                unit="count",
            )
        )
    items.append(
        _fact(
            "ct.midi.coverage",
            "causal_trace_midi_coverage",
            midi_rows,
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )

    items.append(
        _fact(
            "ct.auto.coverage",
            "causal_trace_automation_coverage",
            trace.get("automation_coverage"),
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )
    items.append(
        _fact(
            "ct.ruled_out",
            "causal_trace_ruled_out",
            trace.get("ruled_out"),
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )
    items.append(
        _fact(
            "ct.supported_candidates",
            "causal_trace_supported_cause_candidates",
            trace.get("supported_cause_candidates"),
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )
    items.append(
        _fact(
            "ct.next_evidence",
            "causal_trace_next_evidence",
            trace.get("next_evidence"),
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )
    items.append(
        _fact(
            "ct.source_primary",
            "causal_trace_source_primary",
            trace.get("source_primary"),
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )

    limitations = list(pack.limitations)
    limitations.append(
        ObservationLimitation(
            code="CAUSAL_TRACE_V1",
            detail=(
                f"{TRACE_VERSION} exact timeline facts for {primary_id}. "
                "OBSERVED≠CAUSAL≠JUDGMENT."
            ),
        )
    )
    for note in trace.get("limitations") or []:
        limitations.append(ObservationLimitation(code="CAUSAL_TRACE_LIMITATION", detail=str(note)))
    for note in (trace_payload.get("live_reconciliation") or {}).get("note", "").split("\n"):
        if note.strip():
            limitations.append(
                ObservationLimitation(code="CAUSAL_TRACE_LIVE_RECON", detail=note.strip())
            )

    return EvidencePack(
        pack_id=pack.pack_id,
        analysis_version=f"{ANALYSIS_VERSION}+{TRACE_VERSION}",
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
        analysis_version=TRACE_VERSION,
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
        analysis_version=TRACE_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.OK,
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
    )
