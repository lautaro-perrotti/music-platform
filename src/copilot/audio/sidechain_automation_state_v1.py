"""SIDECHAIN_AUTOMATION_STATE_V1 — read-only sidechain + automation evidence.

Builds on DOWNSTREAM_CAUSAL_STATE_V1 (routing paths + full device params).
Adds, for every verified source and its downstream buses:

- available input/output routing types (reveals candidate sidechain/audio-from
  sources, e.g. the kick track feeding a ducking device),
- session automation-record state and clip automation envelopes (best effort).

Only READ operations. Builds a child EvidencePack and replays Astra exactly
once. Does not write to Ableton.

KNOWN LIMITATION (honest): Ableton LOM does not expose arrangement-view
automation lanes nor the sidechain *source* of a third-party AU/VST plugin as a
direct readable property. We provide trigger-mode parameters and the available
routing list as the grounded evidence Astra can reason over.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.downstream_causal_state_v1 import (
    _track_by_index,
    build_causal_event_table,
    resolve_output_path,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.human_eval.store import now_iso
from copilot.reasoning.pipeline import reason
from copilot.reasoning.provider import configured_http_provider
from copilot.reasoning.session_astra import ASTRA_TIMEOUT_S
from copilot.schemas.evidence import (
    CaptureQuality,
    EntityKind,
    EntityRef,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)
from copilot.schemas.session import SessionState

MILESTONE = "SIDECHAIN_AUTOMATION_STATE_V1"
ARTIFACT = "sidechain_automation_state_v1.json"
ANALYSIS_VERSION = "sidechain-automation-v1"
SCHEMA_VERSION = "1.0"


def read_available_routing(daw: AbletonTcpAdapter, track_index: int) -> dict[str, Any]:
    out: dict[str, Any] = {"track_index": track_index}
    try:
        out["input_types"] = daw.get_track_available_input_types(track_index)
    except Exception as exc:  # noqa: BLE001
        out["input_types_error"] = str(exc)
    try:
        out["output_types"] = daw.get_track_available_output_types(track_index)
    except Exception as exc:  # noqa: BLE001
        out["output_types_error"] = str(exc)
    return out


def read_automation_state(
    daw: AbletonTcpAdapter,
    session: SessionState,
    sources: list[Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {"session_automation_record": None, "clip_automation": []}
    try:
        rec = daw.get_session_automation_record()
        out["session_automation_record"] = rec.get("session_automation_record")
    except Exception as exc:  # noqa: BLE001
        out["session_automation_record_error"] = str(exc)

    for t in sources[:4]:
        for ci in range(1):
            for pname in ("Volume", "Pan", "Trigger level"):
                try:
                    env = daw.get_clip_automation(int(t.index), ci, pname)
                    if env.get("has_automation"):
                        out["clip_automation"].append(
                            {"track_index": int(t.index), "clip_index": ci, "parameter": pname, "envelope": env}
                        )
                except Exception:  # noqa: BLE001
                    pass
    return out


def resolve_sources(session: SessionState, source_names: list[str] | None) -> list[Any]:
    sources: list[Any] = []
    if source_names:
        for name in source_names:
            t = session.track_by_name(name)
            if t is not None:
                sources.append(t)
    if not sources:
        for track in session.tracks:
            if getattr(track, "role", None) in {"audio", "midi"} and track.clips:
                sources.append(track)
                if len(sources) >= 6:
                    break
    return sources


def build_child_pack(
    *,
    region_label: str,
    project_token: str,
    audible_token: str,
    causal_table: dict[str, Any],
    automation: dict[str, Any],
) -> EvidencePack:
    items: list[EvidenceItem] = []
    entities: list[EntityRef] = []
    for row in causal_table.get("sources") or []:
        src = row["source"]
        src_id = f"sc.src.{src['index']}"
        entities.append(
            EntityRef(entity_id=src_id, kind=EntityKind.TRACK, name=src["name"], role=str(src.get("role") or ""))
        )
        for tk, avail in (row.get("available_routing") or {}).items():
            items.append(
                EvidenceItem(
                    evidence_id=f"sc.avail.{src['index']}.{tk}",
                    kind=EvidenceKind.FACT,
                    source_ref=src_id,
                    region=region_label,
                    analysis_version=ANALYSIS_VERSION,
                    name="available_routing",
                    value=avail,
                    quality=CaptureQuality.OK,
                )
            )
        # sidechain candidate hint: available input types of the source track
        sc_hint = None
        avail = (row.get("available_routing") or {}).get(str(src["index"]))
        if avail:
            types = (avail.get("input_types") or {}).get("types") or []
            sc_hint = {
                "track": src["index"],
                "candidate_sidechain_sources": [t.get("display_name") for t in types],
            }
            items.append(
                EvidenceItem(
                    evidence_id=f"sc.sidechain_hint.{src['index']}",
                    kind=EvidenceKind.FACT,
                    source_ref=src_id,
                    region=region_label,
                    analysis_version=ANALYSIS_VERSION,
                    name="sidechain_source_candidates",
                    value=sc_hint,
                    quality=CaptureQuality.OK,
                    limitations=["sidechain_source_not_directly_readable"],
                )
            )

    items.append(
        EvidenceItem(
            evidence_id="sc.automation",
            kind=EvidenceKind.FACT,
            source_ref="",
            region=region_label,
            analysis_version=ANALYSIS_VERSION,
            name="automation_state",
            value=automation,
            quality=CaptureQuality.OK,
            limitations=["arrangement_automation_lanes_not_in_lom"],
        )
    )

    return EvidencePack(
        pack_id=f"pack_{uuid4().hex[:12]}",
        analysis_version=ANALYSIS_VERSION,
        prompt_schema_version=SCHEMA_VERSION,
        region=region_label,
        project_token=project_token,
        audible_token=audible_token,
        target_token=None,
        items=items,
        entities=entities,
        limitations=[
            ObservationLimitation(code="arrangement_automation_lanes_not_in_lom", detail="Arrangement-view automation lanes are not exposed by Ableton LOM."),
            ObservationLimitation(code="sidechain_source_not_directly_readable", detail="Sidechain source of third-party AU/VST plugins is not directly readable via LOM."),
        ],
        domain="producer",
    )


def run_sidechain_automation_state(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path,
    region_id: str = "SIDECHAIN",
    start_qn: float | None = None,
    end_qn: float | None = None,
    source_names: list[str] | None = None,
) -> dict[str, Any]:
    session = daw.snapshot(include_notes=False)
    from copilot.audio.cross_project_bootstrap_v1 import retain_tokens

    retain_tokens(session)
    region_label = f"{region_id}:{start_qn or 0}-{end_qn or 0}" if start_qn is not None else region_id
    sources = resolve_sources(session, source_names)

    causal_table = build_causal_event_table(daw, session, sources)

    # enrich each row with available routing for every path track
    for row in causal_table.get("sources") or []:
        row["available_routing"] = {}
        for hop in row.get("output_path") or []:
            ti = hop.get("track_index")
            if ti is None:
                continue
            trk = _track_by_index(session, ti)
            if trk is None:
                continue
            row["available_routing"][str(ti)] = read_available_routing(daw, int(ti))

    automation = read_automation_state(daw, session, sources)

    child = build_child_pack(
        region_label=region_label,
        project_token=session.project_token or session.project_identity or "",
        audible_token=getattr(session, "audible_token", "") or "",
        causal_table=causal_table,
        automation=automation,
    )

    payload: dict[str, Any] = {
        "milestone": MILESTONE,
        "status": "READ_ONLY_EVIDENCE",
        "ts": now_iso(),
        "region": region_label,
        "child_pack_id": child.pack_id,
        "source_count": len(sources),
        "source_names": [s.name for s in sources],
        "causal_table": causal_table,
        "automation": automation,
        "astra": None,
        "MUSICAL WRITES": 0,
        "NO WRITE": True,
        "AUTOMATION": "READ",
    }

    provider = configured_http_provider()
    if provider is None:
        payload["status"] = "BLOCKED"
        payload["blocker"] = "ASTRA_NOT_CONFIGURED"
        _persist(payload, evidence)
        return payload
    try:
        result = reason(child, provider, timeout_s=ASTRA_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        payload["status"] = "BLOCKED"
        payload["blocker"] = f"ASTRA_ERROR:{exc}"
        _persist(payload, evidence)
        return payload
    payload["astra"] = result.to_dict()
    payload["astra_status"] = (
        (result.diagnosis.status.value if result.diagnosis else None)
        or (result.failure.value if result.failure else None)
    )
    payload["status"] = "VERIFIED" if result.accepted else "BLOCKED"
    _persist(payload, evidence)
    return payload


def _persist(report: dict[str, Any], evidence: Path) -> Path:
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path
