"""Pack Strum device causal trace into EvidencePack."""

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

OBS_VERSION = "strum-device-trace-1"
DOMAIN = "fullmix+lowend+causal-trace+source-audio+strum-device"


def merge_strum_device_into_pack(
    pack: EvidencePack,
    payload: dict[str, Any],
) -> EvidencePack:
    region = pack.region
    project_token = pack.project_token
    audible_token = pack.audible_token
    target_token = pack.target_token
    source_ref = f"strum_device:{payload.get('artifact') or 'inline'}"
    items = list(pack.items)
    snap = payload.get("state_snapshot") or {}
    cause = payload.get("cause_result") or {}

    items.extend(
        [
            _fact(
                "sd.track",
                "strum_device_track",
                payload.get("track"),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
            _fact(
                "sd.event.qn_range",
                "strum_device_event_qn_range",
                payload.get("event_qn_range"),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
            _fact(
                "sd.state_tokens",
                "strum_device_state_tokens",
                snap,
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
            _fact(
                "sd.cause_status",
                "strum_device_cause_status",
                cause.get("status"),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
            _fact(
                "sd.cause_detail",
                "strum_device_cause_detail",
                cause.get("detail"),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
            _fact(
                "sd.cause_leads",
                "strum_device_cause_leads",
                cause.get("leads"),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
            _fact(
                "sd.ruled_out",
                "strum_device_ruled_out",
                cause.get("ruled_out"),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
            _fact(
                "sd.automation_candidates",
                "strum_device_automation_candidates",
                payload.get("automation_candidates"),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
            _fact(
                "sd.live_mixer",
                "strum_device_live_mixer",
                payload.get("live_mixer"),
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
            _fact(
                "sd.live_devices",
                "strum_device_live_devices",
                [
                    {
                        "name": d.get("name"),
                        "class_name": d.get("class_name"),
                        "parameters": [
                            {
                                "parameter_identity": p.get("parameter_identity"),
                                "value_live_now": p.get("value_live_now"),
                                "amplitude_causality": p.get("amplitude_causality"),
                            }
                            for p in (d.get("parameters") or [])
                        ],
                    }
                    for d in (payload.get("live_devices") or [])
                ],
                region,
                project_token,
                audible_token,
                target_token,
                source_ref,
            ),
        ]
    )

    limitations = list(pack.limitations)
    limitations.append(
        ObservationLimitation(
            code="STRUM_DEVICE_TRACE_V1",
            detail=(
                f"{OBS_VERSION}: Strum-only device/automation facts for Main gap. "
                "Do not treat UNSUPPORTED macros as proven volume controls."
            ),
        )
    )
    for note in payload.get("limitations") or []:
        limitations.append(ObservationLimitation(code="STRUM_DEVICE_LIMITATION", detail=str(note)))

    return EvidencePack(
        pack_id=pack.pack_id,
        analysis_version=f"{ANALYSIS_VERSION}+{OBS_VERSION}",
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
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=EvidenceKind.FACT,
        source_ref=source_ref,
        region=region,
        analysis_version=OBS_VERSION,
        name=name,
        value=value,
        quality=CaptureQuality.OK,
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
    )
