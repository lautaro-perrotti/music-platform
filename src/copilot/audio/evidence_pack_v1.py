"""Canonical evidence-pack builder.

Packs only justified, already-computed evidence. No hidden analyzer calls.
No track-name identity. LIMITED ±52 ms stays LIMITED. Silence is valid.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.lowend_features import ANALYZER_ID as LOWEND_ID, analyzer_fingerprint
from copilot.human_eval.store import now_iso
from copilot.reasoning.schema import ANALYSIS_VERSION, SCHEMA_VERSION
from copilot.schemas.evidence import (
    CaptureQuality,
    EntityKind,
    EntityRef,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)

MILESTONE = "EVIDENCE_PACK_V1"
ARTIFACT = "evidence_pack_v1.json"
ALIGNMENT_MS = 52.0
SOURCE_BUDGET = 4


def _item(
    evidence_id: str,
    name: str,
    value: Any,
    *,
    region: str,
    project_token: str,
    audible_token: str,
    source_ref: str,
    kind: EvidenceKind = EvidenceKind.FACT,
    view: str | None = None,
    quality: CaptureQuality = CaptureQuality.OK,
    limitations: list[str] | None = None,
    unit: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        source_ref=source_ref,
        region=region,
        view=view,
        analysis_version=ANALYSIS_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=quality,
        limitations=list(limitations or []),
        project_token=project_token,
        audible_token=audible_token,
    )


def build_evidence_pack(
    *,
    project_token: str,
    audible_token: str,
    region_id: str,
    region: dict[str, Any],
    project_identity: str = "",
    main_capture: dict[str, Any] | None = None,
    source_captures: list[dict[str, Any]] | None = None,
    fullmix: dict[str, Any] | None = None,
    lowend: dict[str, Any] | None = None,
    arrangement: dict[str, Any] | None = None,
    midi: dict[str, Any] | None = None,
    routing: dict[str, Any] | None = None,
    automation_presence: dict[str, Any] | None = None,
    mixer: dict[str, Any] | None = None,
    capture_quality: str | None = None,
    alignment_claim: str = "LIMITED",
    alignment_envelope_ms: float = ALIGNMENT_MS,
    inclusion_reasons: list[dict[str, Any]] | None = None,
    extra_limitations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble a typed pack. Callers must compute analyzers explicitly."""
    if alignment_envelope_ms > 0:
        alignment_claim = "LIMITED"
    items: list[EvidenceItem] = []
    entities: list[EntityRef] = []
    limitations: list[ObservationLimitation] = [
        ObservationLimitation(
            code="ALIGNMENT_LIMITED",
            detail=f"musical alignment remains LIMITED ±{alignment_envelope_ms:g} ms",
            precision_ms=alignment_envelope_ms,
            capability_ms=[alignment_envelope_ms],
        )
    ]
    reasons = list(inclusion_reasons or [])
    region_label = f"{region_id}:{region.get('start_qn')}-{region.get('end_qn')}"

    items.append(
        _item(
            "ev.project.identity",
            "project_identity",
            project_identity or project_token,
            region=region_label,
            project_token=project_token,
            audible_token=audible_token,
            source_ref="session.project_identity",
            kind=EvidenceKind.STATE_TOKEN,
        )
    )
    items.append(
        _item(
            "ev.region",
            "region",
            {"id": region_id, **region},
            region=region_label,
            project_token=project_token,
            audible_token=audible_token,
            source_ref="region",
        )
    )
    reasons.append({"include": "project_identity", "why": "session tokens are required provenance"})
    reasons.append({"include": "region", "why": "all measurements are region-scoped"})

    if main_capture:
        quality = _quality(main_capture.get("quality") or capture_quality)
        items.append(
            _item(
                "ev.main.capture",
                "main_capture",
                _capture_card(main_capture),
                region=region_label,
                project_token=project_token,
                audible_token=audible_token,
                source_ref=str(main_capture.get("source_ref") or "main"),
                kind=EvidenceKind.MEASUREMENT,
                view="MASTER_CONTEXT",
                quality=quality,
                limitations=["silence_is_valid"]
                if str(main_capture.get("signal_class") or "") in {"SILENCE", "NEAR_SILENCE"}
                else None,
            )
        )
        reasons.append(
            {
                "include": "main_capture",
                "why": "Main is the mix-context view for the frozen region",
            }
        )

    for index, capture in enumerate((source_captures or [])[:SOURCE_BUDGET]):
        ref = capture.get("ref") or {}
        quality = _quality(capture.get("quality") or capture_quality)
        signal = str(capture.get("signal_class") or capture.get("signal_status") or "")
        items.append(
            _item(
                f"ev.source.{index}.capture",
                "source_capture",
                _capture_card(capture),
                region=region_label,
                project_token=project_token,
                audible_token=audible_token,
                source_ref=str(ref.get("content_fingerprint") or ref.get("name") or f"source:{index}"),
                kind=EvidenceKind.MEASUREMENT,
                view="TRACK_ISOLATED",
                quality=quality,
                limitations=["silence_is_valid"]
                if signal in {"SILENCE", "NEAR_SILENCE", "RECORDED_SILENCE"}
                else None,
            )
        )
        if ref:
            entities.append(
                EntityRef(
                    entity_id=str(ref.get("content_fingerprint") or f"source:{index}"),
                    kind=EntityKind.TRACK,
                    name="",
                    role=str(ref.get("role") or "") or None,
                )
            )
        reasons.append(
            {
                "include": f"source_capture:{index}",
                "why": capture.get("why_included")
                or "arrangement-active Post Mixer source within budget",
                "ref": ref.get("content_fingerprint"),
            }
        )

    if fullmix:
        items.append(
            _item(
                "ev.fullmix",
                "fullmix_observation",
                {
                    "analyzer_id": fullmix.get("analyzer_id"),
                    "event_count": _lenish(fullmix.get("events") or fullmix.get("energy_events")),
                    "ok": fullmix.get("ok", True),
                },
                region=region_label,
                project_token=project_token,
                audible_token=audible_token,
                source_ref="fullmix-obs-1",
                kind=EvidenceKind.MEASUREMENT,
                view="MASTER_CONTEXT",
            )
        )
        reasons.append({"include": "fullmix", "why": "caller supplied frozen fullmix-obs-1 output"})

    if lowend:
        if lowend.get("ok"):
            items.append(
                _item(
                    "ev.lowend",
                    "lowend_observation",
                    {
                        "analyzer_id": lowend.get("analyzer_id") or LOWEND_ID,
                        "analyzer_sha256": analyzer_fingerprint(),
                        "kick_events": (lowend.get("temporal") or {}).get("kick_events"),
                        "overlap_events": (lowend.get("temporal") or {}).get("events_with_overlap"),
                    },
                    region=region_label,
                    project_token=project_token,
                    audible_token=audible_token,
                    source_ref="lowend-obs-1",
                    kind=EvidenceKind.MEASUREMENT,
                    view="TRACK_ISOLATED",
                )
            )
            reasons.append(
                {
                    "include": "lowend",
                    "why": "caller supplied two isolated source views plus Main",
                }
            )
        else:
            limitations.append(
                ObservationLimitation(
                    code="LOWEND_NOT_JUSTIFIED",
                    detail=str(lowend.get("missing") or "lowend features not ok"),
                )
            )
            reasons.append(
                {
                    "include": "lowend_limitation",
                    "why": "analyzer not run or missing required isolated views",
                }
            )

    if arrangement:
        items.append(
            _item(
                "ev.arrangement",
                "arrangement_state",
                {
                    "active_count": arrangement.get("active_count"),
                    "eligible_count": arrangement.get("eligible_count"),
                    "limitation": arrangement.get("limitation"),
                },
                region=region_label,
                project_token=project_token,
                audible_token=audible_token,
                source_ref="arrangement_clips",
            )
        )
        reasons.append({"include": "arrangement", "why": "HAS_MATERIAL inventory for the region"})

    if midi is None:
        limitations.append(
            ObservationLimitation(
                code="MIDI_UNREAD",
                detail="MIDI notes were not requested for this pack",
            )
        )
    else:
        items.append(
            _item(
                "ev.midi",
                "midi_evidence",
                midi,
                region=region_label,
                project_token=project_token,
                audible_token=audible_token,
                source_ref="midi",
            )
        )
        reasons.append({"include": "midi", "why": "caller supplied MIDI facts"})

    if routing:
        items.append(
            _item(
                "ev.routing",
                "routing",
                routing,
                region=region_label,
                project_token=project_token,
                audible_token=audible_token,
                source_ref="routing",
            )
        )
        reasons.append({"include": "routing", "why": "OFF_MIX_GRAPH / Main-final claims"})

    if automation_presence is not None:
        items.append(
            _item(
                "ev.automation",
                "automation_presence",
                automation_presence,
                region=region_label,
                project_token=project_token,
                audible_token=audible_token,
                source_ref="automation",
            )
        )
        reasons.append({"include": "automation", "why": "presence only, not interpretation"})

    if mixer:
        items.append(
            _item(
                "ev.mixer",
                "device_mixer_state",
                mixer,
                region=region_label,
                project_token=project_token,
                audible_token=audible_token,
                source_ref="mixer",
                kind=EvidenceKind.SESSION_ENTITY,
            )
        )
        reasons.append({"include": "mixer", "why": "session mixer/device snapshot for targets"})

    for extra in extra_limitations or []:
        limitations.append(ObservationLimitation(**extra) if isinstance(extra, dict) else extra)

    if alignment_claim != "LIMITED":
        alignment_claim = "LIMITED"

    pack = EvidencePack(
        pack_id=f"pack_{uuid4().hex[:12]}",
        analysis_version=ANALYSIS_VERSION,
        prompt_schema_version=SCHEMA_VERSION,
        region=region_label,
        project_token=project_token,
        audible_token=audible_token,
        alignment_claim=alignment_claim,
        alignment_envelope_ms=alignment_envelope_ms,
        items=items,
        entities=entities,
        limitations=limitations,
        domain="producer",
    )
    ids = [item.evidence_id for item in pack.items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicated evidence_id in pack")
    return {
        "milestone": MILESTONE,
        "ts": now_iso(),
        "pack": pack.model_dump(mode="json"),
        "inclusion_reasons": reasons,
        "source_budget": SOURCE_BUDGET,
        "capture_quality": capture_quality or "UNKNOWN",
        "alignment_claim": alignment_claim,
        "alignment_envelope_ms": alignment_envelope_ms,
        "NO HIDDEN ANALYZER CALLS": True,
    }


def persist_evidence_pack(built: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(built, indent=2, default=str), encoding="utf-8")
    return path


def _capture_card(capture: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": capture.get("ok"),
        "signal_class": capture.get("signal_class") or capture.get("signal_status"),
        "rms": capture.get("rms"),
        "peak": capture.get("peak"),
        "audio_sha256": capture.get("audio_sha256"),
        "path": capture.get("path") or capture.get("wav"),
        "ref": (capture.get("ref") or {}).get("content_fingerprint"),
    }


def _quality(raw: str | None) -> CaptureQuality:
    if raw in {item.value for item in CaptureQuality}:
        return CaptureQuality(raw)
    return CaptureQuality.UNKNOWN


def _lenish(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, list):
        return len(value)
    return None
