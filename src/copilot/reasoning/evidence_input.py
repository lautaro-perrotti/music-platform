"""Adapter: EvidencePack / EvidenceView → scoped payload Astra actually sees.

Does not import copilot.runtime (reasoning must not depend on runtime).
A real EvidenceView is accepted by duck typing (to_dict / attribute access).
Pack-only callers get the same shape via graph_from_pack at the runtime edge,
or via scoped_from_pack here.
"""

from __future__ import annotations

from typing import Any

from copilot.schemas.evidence import EvidenceKind, EvidencePack, INTERPRETIVE_KINDS

PROMPT_NODE_KEYS = (
    "evidence_id",
    "kind",
    "name",
    "value",
    "unit",
    "region",
    "subject_identity",
    "quality",
    "limitations",
    "validity",
    "freshness",
    "confidence_components",
    "provider",
    "source_ref",
    "project_identity",
    "state_tokens",
)

_SKIP_KINDS = {item.value for item in INTERPRETIVE_KINDS} | {EvidenceKind.LIMITATION.value}


def view_as_dict(view: Any | None) -> dict[str, Any]:
    if view is None:
        return {}
    if isinstance(view, dict):
        return view
    to_dict = getattr(view, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    return {
        "project_identity": getattr(view, "project_identity", None),
        "project_state": getattr(view, "project_state", None),
        "nodes": dict(getattr(view, "nodes", None) or {}),
        "summary": dict(getattr(view, "summary", None) or {}),
        "support": list(getattr(view, "support", None) or []),
        "counterevidence": list(getattr(view, "counterevidence", None) or []),
        "limitations": list(getattr(view, "limitations", None) or []),
        "provenance": list(getattr(view, "provenance", None) or []),
        "fusions": list(getattr(view, "fusions", None) or []),
    }


def scoped_from_pack(pack: EvidencePack) -> dict[str, Any]:
    """Pack compatibility adapter. Same keys as a serialized EvidenceView."""
    nodes: dict[str, dict[str, Any]] = {}
    support: list[str] = []
    subjects: list[str] = []
    regions: list[str] = []
    for item in pack.items:
        node = {
            "identity": item.evidence_id,
            "evidence_id": item.evidence_id,
            "kind": item.kind.value,
            "name": item.name,
            "value": item.value,
            "unit": item.unit,
            "region": item.region,
            "subject_identity": item.source_ref,
            "quality": item.quality.value,
            "limitations": list(item.limitations),
            "validity": "VALID",
            "freshness": None,
            "confidence_components": {
                "measurement_quality": item.quality.value,
                "provider_confidence": item.confidence,
                "source_reliability": item.source_ref,
                "cross_source_agreement": None,
                "reasoning_confidence": None,
                "combined": None,
            },
            "provider": item.source_ref,
            "source_ref": item.source_ref,
            "project_identity": pack.project_token,
            "state_tokens": {
                "project": pack.project_token,
                "audible": pack.audible_token,
            },
            "payload": item.value if isinstance(item.value, dict) else {"value": item.value},
        }
        nodes[item.evidence_id] = node
        if item.kind not in INTERPRETIVE_KINDS:
            support.append(item.evidence_id)
        if item.source_ref:
            subjects.append(item.source_ref)
        if item.region:
            regions.append(item.region)
    limitations = [
        {"code": row.code, "detail": row.detail, "precision_ms": row.precision_ms}
        for row in pack.limitations
    ]
    provenance = [
        {
            "evidence_id": item.evidence_id,
            "provider": item.source_ref,
            "source_ref": item.source_ref,
            "dependencies": [],
        }
        for item in pack.items
    ]
    return {
        "project_identity": pack.project_token,
        "project_state": pack.project_token,
        "nodes": nodes,
        "summary": {
            "node_count": len(nodes),
            "kinds": sorted({item.kind.value for item in pack.items}),
            "region": pack.region,
            "pack_id": pack.pack_id,
        },
        "support": list(dict.fromkeys(support)),
        "counterevidence": [],
        "limitations": limitations,
        "provenance": provenance,
        "fusions": [],
        "subjects": list(dict.fromkeys(subjects)),
        "regions": list(dict.fromkeys(regions)),
    }


def scoped_evidence(pack: EvidencePack, view: Any | None = None) -> dict[str, Any]:
    if view is None:
        return scoped_from_pack(pack)
    payload = view_as_dict(view)
    nodes = dict(payload.get("nodes") or {})
    subjects = []
    regions = []
    for node in nodes.values():
        subject = node.get("subject_identity")
        if subject:
            subjects.append(str(subject))
        region = node.get("region")
        if region:
            regions.append(str(region))
    payload.setdefault("support", [])
    payload.setdefault("counterevidence", [])
    payload.setdefault("limitations", [])
    payload.setdefault("provenance", [])
    payload.setdefault("fusions", [])
    payload["subjects"] = list(dict.fromkeys(subjects))
    payload["regions"] = list(dict.fromkeys(regions or [pack.region]))
    payload.setdefault("summary", {})
    payload["summary"].setdefault("pack_id", pack.pack_id)
    payload["summary"].setdefault("region", pack.region)
    payload["summary"]["node_count"] = len(nodes)
    return payload


def compact_node(node: dict[str, Any]) -> dict[str, Any]:
    ident = str(node.get("evidence_id") or node.get("identity") or "")
    provenance = node.get("provenance") if isinstance(node.get("provenance"), dict) else {}
    payload = node.get("payload")
    name = node.get("name") or provenance.get("name")
    value: Any = node.get("value")
    if value is None and isinstance(payload, dict):
        if "value" in payload and len(payload) == 1:
            value = payload.get("value")
        else:
            value = payload
    elif value is None:
        value = payload
    components = node.get("confidence_components")
    if isinstance(components, dict):
        components = {**components, "combined": None}
    limitations = _limitation_codes(node.get("limitations") or [])
    return {
        "evidence_id": ident,
        "kind": node.get("kind") or node.get("evidence_type"),
        "name": name,
        "value": value,
        "unit": node.get("unit"),
        "region": node.get("region"),
        "subject_identity": node.get("subject_identity"),
        "quality": node.get("quality"),
        "limitations": limitations,
        "validity": node.get("validity") or "VALID",
        "freshness": node.get("freshness"),
        "confidence_components": components,
        "provider": node.get("provider") or (provenance or {}).get("provider"),
        "source_ref": node.get("source_ref") or node.get("source_artifact"),
        "project_identity": node.get("project_identity"),
        "state_tokens": node.get("state_tokens") or {},
    }


def serialize_for_prompt(scoped: dict[str, Any]) -> dict[str, Any]:
    """Relevant evidence only. No pack-chain dump, no limitation-node duplicates."""
    nodes_in = scoped.get("nodes") or {}
    compact: list[dict[str, Any]] = []
    skipped_interpretive = 0
    skipped_limit_nodes = 0
    seen_values: dict[tuple[Any, Any], list[str]] = {}
    for ident, raw in nodes_in.items():
        node = raw if isinstance(raw, dict) else {}
        kind = str(node.get("kind") or node.get("evidence_type") or "")
        if kind in _SKIP_KINDS or str(ident).startswith("lim."):
            if kind == EvidenceKind.LIMITATION.value or str(ident).startswith("lim."):
                skipped_limit_nodes += 1
            else:
                skipped_interpretive += 1
            continue
        row = compact_node(node)
        row["evidence_id"] = row["evidence_id"] or str(ident)
        compact.append(row)
        key = (row.get("name"), _freeze(row.get("value")))
        seen_values.setdefault(key, []).append(row["evidence_id"])
    duplicates = [
        {"name": name, "evidence_ids": ids}
        for (name, _value), ids in seen_values.items()
        if name and len(ids) > 1
    ]
    limitations = [
        {
            "code": row.get("code") if isinstance(row, dict) else str(row),
            "detail": row.get("detail", "") if isinstance(row, dict) else "",
            "precision_ms": row.get("precision_ms") if isinstance(row, dict) else None,
        }
        for row in (scoped.get("limitations") or [])
    ]
    fusions = []
    for row in scoped.get("fusions") or []:
        if not isinstance(row, dict):
            continue
        fusions.append(
            {
                "question": row.get("question"),
                "node_ids": list(row.get("node_ids") or []),
                "status": row.get("status"),
                "notes": row.get("notes") or "",
            }
        )
    provenance = []
    for row in scoped.get("provenance") or []:
        if not isinstance(row, dict):
            continue
        provenance.append(
            {
                "evidence_id": row.get("evidence_id"),
                "provider": row.get("provider"),
                "provider_version": row.get("provider_version"),
                "source_ref": row.get("source_ref"),
            }
        )
    return {
        "project_identity": scoped.get("project_identity"),
        "project_state": scoped.get("project_state"),
        "region": (scoped.get("summary") or {}).get("region"),
        "subjects": list(scoped.get("subjects") or []),
        "regions": list(scoped.get("regions") or []),
        "support": list(scoped.get("support") or []),
        "counterevidence": list(scoped.get("counterevidence") or []),
        "fusions": fusions,
        "limitations": limitations,
        "provenance": provenance,
        "nodes": compact,
        "serialization": {
            "included_nodes": len(compact),
            "skipped_interpretive": skipped_interpretive,
            "skipped_limitation_nodes": skipped_limit_nodes,
            "duplicate_value_groups": duplicates,
        },
    }


def known_evidence_ids(pack: EvidencePack, scoped: dict[str, Any] | None = None) -> set[str]:
    ids = {item.evidence_id for item in pack.items}
    if not scoped:
        return ids
    for ident, node in (scoped.get("nodes") or {}).items():
        ids.add(str(ident))
        if isinstance(node, dict):
            extra = node.get("evidence_id") or node.get("identity")
            if extra:
                ids.add(str(extra))
    return ids


def view_node_index(scoped: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ident, node in (scoped.get("nodes") or {}).items():
        if not isinstance(node, dict):
            continue
        compact = compact_node(node)
        compact["evidence_id"] = compact["evidence_id"] or str(ident)
        out[str(ident)] = compact
        out[compact["evidence_id"]] = compact
    return out


def limitation_codes_from_scoped(pack: EvidencePack, scoped: dict[str, Any] | None = None) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()

    def _add(code: str) -> None:
        if code and code not in seen:
            seen.add(code)
            codes.append(code)

    for row in pack.limitations:
        _add(row.code)
    for item in pack.items:
        for code in item.limitations:
            _add(code)
    if scoped:
        for row in scoped.get("limitations") or []:
            if isinstance(row, dict):
                _add(str(row.get("code") or row.get("original_code") or ""))
            else:
                _add(str(row))
        for node in (scoped.get("nodes") or {}).values():
            if not isinstance(node, dict):
                continue
            for code in _limitation_codes(node.get("limitations") or []):
                _add(code)
            payload = node.get("payload")
            if isinstance(payload, dict) and payload.get("code"):
                _add(str(payload["code"]))
    return codes


def fusion_statuses(scoped: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in (scoped.get("fusions") or []) if isinstance(row, dict)]


def _limitation_codes(rows: list[Any]) -> list[str]:
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict) and row.get("code"):
            out.append(str(row["code"]))
        elif isinstance(row, str) and row:
            out.append(row)
    return list(dict.fromkeys(out))


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value
