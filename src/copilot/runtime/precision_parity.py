"""PRECISION_PARITY — compare AnalyzeProject semantic evidence, not timings."""

from __future__ import annotations

from typing import Any

TIMING_KEYS = {
    "wall_time",
    "rpc_wall_time",
    "rpc_count",
    "duration_s",
    "total_s",
    "started_wall",
    "ts",
    "operation_id",
    "span_id",
    "tree",
    "metrics",
    "performance",
    "rpc",
    "rpc_by_method",
    "rpc_trace",
    "duplicate_reads_eliminated",
    "bulk_calls",
    "fresh_reads_forced",
    "post_mutation_verifications",
    "eliminated_by_method",
    "parallel_savings_s",
}


def extract_precision(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload") or {}
    canonical = payload.get("canonical_report") or {}
    evidence = result.get("evidence_summary") or {}
    diagnosis = result.get("diagnosis") or canonical.get("diagnosis") or {}
    gate = result.get("gate") or canonical.get("gate") or {}
    terminal = result.get("terminal") or canonical.get("terminal") or {}
    region = evidence.get("region") or canonical.get("region")
    pack = evidence.get("pack_id") or canonical.get("evidence_pack_id")
    view = evidence.get("evidence_view") or {}
    nodes = (view.get("nodes") if isinstance(view, dict) else None) or {}
    kinds = sorted(
        {
            str(node.get("kind") or "")
            for node in (nodes.values() if isinstance(nodes, dict) else [])
            if isinstance(node, dict)
        }
    )
    return {
        "project_identity": result.get("project_identity"),
        "status": result.get("status") or canonical.get("status"),
        "reason": result.get("reason") or canonical.get("status"),
        "region": _region_identity(region),
        "capture_count": evidence.get("captures"),
        "diagnosis_status": _diagnosis_status(diagnosis),
        "gate_status": gate.get("status") or gate.get("MUSICPLAN_GATE"),
        "terminal_ok": terminal.get("ok") if isinstance(terminal, dict) else None,
        "musical_writes": result.get("musical_writes", 0),
        "no_write": canonical.get("NO WRITE", True),
        "evidence_kinds": kinds,
        "evidence_node_count": len(nodes) if isinstance(nodes, dict) else 0,
        "limitations": list(result.get("limitations") or []),
        "pack_present": bool(pack),
    }


def coverage_counts(result: dict[str, Any]) -> dict[str, int]:
    payload = result.get("payload") or {}
    canonical = payload.get("canonical_report") or {}
    report = canonical if isinstance(canonical, dict) else {}
    pack = {}
    built = report.get("diagnosis") or result.get("diagnosis") or {}
    items = []
    if isinstance(built, dict):
        items = list(built.get("evidence_refs") or built.get("evidence") or [])
    view = (result.get("evidence_summary") or {}).get("evidence_view") or {}
    nodes = view.get("nodes") if isinstance(view, dict) else {}
    node_kinds = [
        str(node.get("kind") or "")
        for node in (nodes.values() if isinstance(nodes, dict) else [])
        if isinstance(node, dict)
    ]
    return {
        "evidence_field_count": len(items) + len(nodes or {}),
        "source_coverage": sum(1 for kind in node_kinds if "source" in kind.lower()),
        "routing_coverage": sum(1 for kind in node_kinds if "rout" in kind.lower()),
        "device_coverage": sum(1 for kind in node_kinds if "device" in kind.lower()),
        "capture_coverage": int((result.get("evidence_summary") or {}).get("captures") or 0),
        "state_verification": 1 if (result.get("terminal") or {}).get("ok") else 0,
    }


def compare_precision(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    left = extract_precision(before)
    right = extract_precision(after)
    diffs: dict[str, Any] = {}
    for key in left:
        if left[key] != right[key]:
            diffs[key] = {"before": left[key], "after": right[key]}
    before_cov = coverage_counts(before)
    after_cov = coverage_counts(after)
    coverage_ok = all(after_cov[key] >= before_cov[key] for key in before_cov)
    return {
        "ok": not diffs and coverage_ok,
        "diffs": diffs,
        "coverage_before": before_cov,
        "coverage_after": after_cov,
        "before": left,
        "after": right,
    }


def _region_identity(region: Any) -> Any:
    if isinstance(region, dict):
        return {
            "id": region.get("id") or region.get("region_id"),
            "start_qn": region.get("start_qn"),
            "end_qn": region.get("end_qn"),
        }
    return region


def _diagnosis_status(diagnosis: Any) -> Any:
    if isinstance(diagnosis, dict):
        return diagnosis.get("status") or diagnosis.get("diagnosis_status")
    return diagnosis
