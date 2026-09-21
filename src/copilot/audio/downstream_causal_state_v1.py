"""DOWNSTREAM_CAUSAL_STATE_V1 — read-only downstream causal evidence.

Move from MIDI -> source Post Mixer to:

    MIDI -> source -> Post Mixer -> group -> downstream processing -> Main

Only READ operations. No musical writes. Builds a causal event table and a
child EvidencePack, then replays Astra exactly once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.human_eval.store import now_iso
from copilot.reasoning.pipeline import reason
from copilot.reasoning.session_astra import ASTRA_TIMEOUT_S
from copilot.reasoning.provider import configured_http_provider
from copilot.schemas.evidence import (
    CaptureQuality,
    EntityKind,
    EntityRef,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
)
from copilot.schemas.session import SessionState

MILESTONE = "DOWNSTREAM_CAUSAL_STATE_V1"
ARTIFACT = "downstream_causal_state_v1.json"
STATUS = "IMPLEMENTED"
ANALYSIS_VERSION = "downstream-causal-v1"
SCHEMA_VERSION = "1.0"
MAX_PATH_HOPS = 12
AUDIBLE_ROUTING_TYPES = ("Master", "Group Track", "Track", "Sends Only")


def resolve_output_path(
    daw: AbletonTcpAdapter,
    session: SessionState,
    start_track: Any,
) -> list[dict[str, Any]]:
    """Walk audible output routing: track -> group -> ... -> Master.

    Resolves group hops by the group's display name (``output_routing_channel``)
    against the session track list. Stops at Master or at an unresolvable hop.
    """
    hops: list[dict[str, Any]] = []
    seen: set[int] = set()
    current = start_track
    while current is not None and len(hops) < MAX_PATH_HOPS:
        key = int(getattr(current, "index", -1))
        if key in seen:
            break
        seen.add(key)
        try:
            info = daw.get_track_output_routing(key)
        except Exception as exc:  # noqa: BLE001
            hops.append(
                {
                    "track_index": key,
                    "name": getattr(current, "name", None),
                    "role": getattr(current, "role", None),
                    "output_routing_type": None,
                    "output_routing_channel": None,
                    "unresolved": str(exc),
                }
            )
            break
        rtype = str(info.get("output_routing_type") or "")
        channel = str(info.get("output_routing_channel") or "")
        hops.append(
            {
                "track_index": key,
                "name": getattr(current, "name", None),
                "role": getattr(current, "role", None),
                "output_routing_type": rtype,
                "output_routing_channel": channel,
            }
        )
        if rtype in ("Master", "Main"):
            break
        # The group/track name may arrive in the channel OR in the routing
        # type (e.g. output_routing_type = "BASS GROUP", channel = "").
        nxt = None
        for candidate in (channel, rtype):
            if not candidate:
                continue
            nxt = session.track_by_name(candidate)
            if nxt is not None:
                break
        if nxt is None:
            break
        current = nxt
    return hops


def read_track_devices(
    daw: AbletonTcpAdapter, track: Any
) -> list[dict[str, Any]]:
    """Read device names + parameter snapshots for one track (read-only)."""
    out: list[dict[str, Any]] = []
    try:
        info = daw.get_track_info(int(track.index))
    except Exception:  # noqa: BLE001
        return out
    for device in info.get("devices") or []:
        dindex = int(device.get("index"))
        try:
            params = daw.get_device_parameters(int(track.index), dindex)
        except Exception:  # noqa: BLE001
            params = {}
        out.append(
            {
                "device_index": dindex,
                "name": device.get("name"),
                "class_name": device.get("class_name") or device.get("device_class"),
                "parameters": [
                    {
                        "index": p.get("index"),
                        "name": p.get("name"),
                        "value": p.get("value"),
                        "min": p.get("min"),
                        "max": p.get("max"),
                    }
                    for p in (params.get("parameters") or [])
                ],
            }
        )
    return out


def build_causal_event_table(
    daw: AbletonTcpAdapter,
    session: SessionState,
    sources: list[Any],
) -> dict[str, Any]:
    """Assemble per-source routing paths + device state. Read-only."""
    rows: list[dict[str, Any]] = []
    for track in sources:
        path = resolve_output_path(daw, session, track)
        path_tracks = [hop for hop in path if hop.get("track_index") is not None]
        devices = {
            str(hop["track_index"]): read_track_devices(daw, _track_by_index(session, hop["track_index"]))
            for hop in path_tracks
            if _track_by_index(session, hop["track_index"]) is not None
        }
        rows.append(
            {
                "source": {
                    "index": int(track.index),
                    "name": track.name,
                    "role": getattr(track, "role", None),
                },
                "output_path": path,
                "reaches_main": any(
                    hop.get("output_routing_type") in ("Master", "Main") for hop in path
                ),
                "devices": devices,
            }
        )
    return {"sources": rows}


def _track_by_index(session: SessionState, index: int) -> Any:
    for track in session.tracks:
        if int(track.index) == int(index):
            return track
    return None


def build_child_pack(
    *,
    region_label: str,
    project_token: str,
    audible_token: str,
    target_token: str | None,
    causal_table: dict[str, Any],
    parent_pack_id: str | None,
) -> EvidencePack:
    """Build a NEW child EvidencePack. The parent pack is never mutated."""
    items: list[EvidenceItem] = []
    entities: list[EntityRef] = []
    for row in causal_table.get("sources") or []:
        src = row["source"]
        src_id = f"dwn.src.{src['index']}"
        entities.append(
            EntityRef(
                entity_id=src_id,
                kind=EntityKind.TRACK,
                name=src["name"],
                role=str(src.get("role") or ""),
            )
        )
        path = row.get("output_path") or []
        items.append(
            EvidenceItem(
                evidence_id=f"dwn.path.{src['index']}",
                kind=EvidenceKind.FACT,
                source_ref=src_id,
                region=region_label,
                analysis_version=ANALYSIS_VERSION,
                name="downstream_output_path",
                value=path,
                quality=CaptureQuality.OK,
                limitations=["automation_not_read", "sidechain_path_not_resolved"],
            )
        )
        items.append(
            EvidenceItem(
                evidence_id=f"dwn.main.{src['index']}",
                kind=EvidenceKind.FACT,
                source_ref=src_id,
                region=region_label,
                analysis_version=ANALYSIS_VERSION,
                name="reaches_main",
                value=bool(row.get("reaches_main")),
                quality=CaptureQuality.OK,
            )
        )
        for tk, devs in (row.get("devices") or {}).items():
            items.append(
                EvidenceItem(
                    evidence_id=f"dwn.devices.{src['index']}.{tk}",
                    kind=EvidenceKind.SESSION_ENTITY,
                    source_ref=src_id,
                    region=region_label,
                    analysis_version=ANALYSIS_VERSION,
                    name="device_state",
                    value=devs,
                    quality=CaptureQuality.OK,
                )
            )
    return EvidencePack(
        pack_id=f"pack_{uuid4().hex[:12]}",
        analysis_version=ANALYSIS_VERSION,
        prompt_schema_version=SCHEMA_VERSION,
        region=region_label,
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
        items=items,
        entities=entities,
        limitations=[],
        domain="producer",
    )


def run_downstream_causal_state_v1(
    daw: AbletonTcpAdapter,
    *,
    evidence: Path,
    parent_pack_id: str | None = None,
    region_id: str = "DOWNSTREAM",
    start_qn: float | None = None,
    end_qn: float | None = None,
    source_names: list[str] | None = None,
) -> dict[str, Any]:
    """Read routing + devices -> causal table -> child pack -> Astra once."""
    session = daw.snapshot(include_notes=False)
    from copilot.audio.cross_project_bootstrap_v1 import retain_tokens

    retain_tokens(session)
    region_label = (
        f"{region_id}:{start_qn or 0}-{end_qn or 0}" if start_qn is not None else region_id
    )
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

    causal_table = build_causal_event_table(daw, session, sources)
    child = build_child_pack(
        region_label=region_label,
        project_token=session.project_token or session.project_identity or "",
        audible_token=getattr(session, "audible_token", "") or "",
        target_token=None,
        causal_table=causal_table,
        parent_pack_id=parent_pack_id,
    )
    payload: dict[str, Any] = {
        "milestone": MILESTONE,
        "status": "READ_ONLY_EVIDENCE",
        "DOWNSTREAM_CAUSAL_STATE_V1": "READ_ONLY_EVIDENCE",
        "ts": now_iso(),
        "region": region_label,
        "parent_pack_id": parent_pack_id,
        "child_pack_id": child.pack_id,
        "source_count": len(sources),
        "source_names": [s.name for s in sources],
        "causal_table": causal_table,
        "child_pack": child.model_dump(mode="json"),
        "astra": None,
        "MUSICAL WRITES": 0,
        "NO WRITE": True,
        "AUTOMATION": "NOT_READ",
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
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
