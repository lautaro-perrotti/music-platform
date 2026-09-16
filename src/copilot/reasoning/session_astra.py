from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from copilot.audio.file_hash import sha256_file
from copilot.audio.fullmix import compute_fullmix_observation, run_session_fullmix
from copilot.human_eval.gate import assert_run_locked
from copilot.human_eval.store import EvalStore, now_iso
from copilot.reasoning.from_causal import merge_causal_into_pack
from copilot.reasoning.from_fullmix import merge_lowend_and_fullmix
from copilot.reasoning.grounding import validate_reasoning
from copilot.reasoning.musicplan_gate import evaluate_musicplan_gate
from copilot.reasoning.pipeline import reason
from copilot.reasoning.provider import ReasoningProvider, configured_http_provider
from copilot.reasoning.schema import PROMPT_VERSION, SCHEMA_VERSION, ReasoningOutput
from copilot.schemas.evidence import EvidencePack

ASTRA_TIMEOUT_S = 180.0


def load_frozen_packs(evidence: Path, source_run: str = "session_run1") -> list[EvidencePack]:
    report_path = Path(evidence) / f"{source_run}.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"session report missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = list(report.get("DSP OBSERVATIONS") or [])
    if not rows:
        raise ValueError("DSP OBSERVATIONS missing; cannot call Astra on frozen evidence")
    packs: list[EvidencePack] = []
    for row in rows:
        raw = row.get("evidence_pack")
        if not raw:
            raise ValueError(f"evidence_pack missing for {row.get('region')}")
        pack = EvidencePack.model_validate(raw)
        packs.append(pack)
    return packs


def load_merged_packs(
    evidence: Path,
    source_run: str = "session_run1",
    *,
    include_fullmix: bool = True,
) -> tuple[list[EvidencePack], list[dict[str, Any]]]:
    evidence = Path(evidence)
    lowend_packs = load_frozen_packs(evidence, source_run)
    if not include_fullmix:
        return lowend_packs, []
    report = json.loads((evidence / f"{source_run}.json").read_text(encoding="utf-8"))
    listen = {str(row["region"]): row for row in report.get("HUMAN LISTEN MAIN") or []}
    fullmix_rows: list[dict[str, Any]] = []
    merged: list[EvidencePack] = []
    for pack in lowend_packs:
        listen_row = listen.get(pack.pack_id)
        if listen_row is None:
            raise FileNotFoundError(f"HUMAN LISTEN MAIN missing for {pack.pack_id}")
        path = Path(str(listen_row["path"]))
        digest = sha256_file(path) or ""
        obs = compute_fullmix_observation(
            path,
            region_id=pack.pack_id,
            region_label=pack.region,
            audio_sha256=digest,
        )
        fullmix_rows.append(obs.model_dump(mode="json"))
        merged.append(merge_lowend_and_fullmix(pack, obs))
    return merged, fullmix_rows


def _human_by_region(run_id: str, *, store: EvalStore) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for case in store.load_cases(run_id):
        if not case.region or case.response is None:
            continue
        out[case.region] = {
            "case_id": case.case_id,
            "status": case.status.value,
            "overall_feel": None if case.response.overall_feel is None else case.response.overall_feel.value,
            "would_change": None if case.response.would_change is None else case.response.would_change.value,
            "groove_feels_right": None
            if case.response.groove_feels_right is None
            else case.response.groove_feels_right.value,
            "low_end_feels_right": None
            if case.response.low_end_feels_right is None
            else case.response.low_end_feels_right.value,
            "desired_actions": [item.value for item in case.response.desired_actions],
            "notes": case.response.notes,
            "confidence": None if case.response.confidence is None else case.response.confidence.value,
            "human_label_hash": case.human_label_hash,
            "audio_sha256": case.audio_sha256,
        }
    return out


def _evidence_hashes(packs: list[EvidencePack]) -> dict[str, str]:
    import hashlib

    out = {}
    for pack in packs:
        blob = json.dumps(pack.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        out[pack.pack_id] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return out


def run_session_astra(
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
    store: EvalStore | None = None,
    include_fullmix: bool = False,
    diagnosis_revision: int = 1,
    artifact_name: str | None = None,
) -> dict[str, Any]:
    """Call Astra on frozen RUN 1 packs. No Ableton. No MusicPlan. No recapture."""
    evidence = Path(evidence or "logs")
    store = store or EvalStore()
    assert_run_locked(source_run, store=store)
    provider = provider or configured_http_provider()
    if provider is None:
        return {
            "status": "REAL_MODEL_UNAVAILABLE",
            "reason": "No COPILOT_REASONING_API_KEY or OPENAI_API_KEY",
            "ASTRA CALLS": 0,
            "MUSICAL WRITES": 0,
            "MusicPlan": None,
            "diagnosis_revision": diagnosis_revision,
        }
    if include_fullmix:
        # Ensure fullmix artifact exists for provenance even if cache-hit per region.
        run_session_fullmix(evidence=evidence, source_run=source_run)
        packs, fullmix_rows = load_merged_packs(evidence, source_run, include_fullmix=True)
    else:
        packs, fullmix_rows = load_frozen_packs(evidence, source_run), []
    humans = _human_by_region(source_run, store=store)
    results: list[dict[str, Any]] = []
    calls = 0
    for pack in packs:
        print(f"ASTRA r{diagnosis_revision} {pack.pack_id} {pack.region}", flush=True)
        result = reason(pack, provider, timeout_s=timeout_s)
        calls += 1
        print(
            f"ASTRA r{diagnosis_revision} {pack.pack_id} accepted={result.accepted} "
            f"failure={None if result.failure is None else result.failure.value}",
            flush=True,
        )
        human = humans.get(pack.pack_id) or {}
        results.append(
            {
                "region_id": pack.pack_id,
                "region": pack.region,
                "accepted": result.accepted,
                "failure": None if result.failure is None else result.failure.value,
                "diagnosis": None if result.diagnosis is None else result.diagnosis.model_dump(mode="json"),
                "output": None if result.output is None else result.output.model_dump(mode="json"),
                "audit": result.to_dict()["audit"],
                "human": human,
                "human_in_prompt": False,
                "pack_domain": pack.domain,
                "pack_analysis_version": pack.analysis_version,
            }
        )
    run = store.load_run(source_run)
    if run is not None:
        run.astra_calls = int(run.astra_calls or 0) + calls
        store.save_run(run)
    payload = {
        "status": "ASTRA COMPLETE",
        "source_run": source_run,
        "diagnosis_revision": diagnosis_revision,
        "called_at": now_iso(),
        "provider": provider.identity,
        "provider_version": provider.version,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "include_fullmix": include_fullmix,
        "input_evidence_hashes": _evidence_hashes(packs),
        "ASTRA CALLS": calls,
        "MUSICAL WRITES": 0,
        "MusicPlan": None,
        "CandidateActions_executable": False,
        "human_in_prompt": False,
        "NO LIVE-4": True,
        "fullmix_regions": [
            {
                "region_id": row.get("region_id"),
                "audio_sha256": row.get("audio_sha256"),
                "energy_event_count": len(row.get("energy_events") or []),
                "energy_events": [
                    {
                        "kind": ev.get("kind"),
                        "start_s": ev.get("start_s"),
                        "end_s": ev.get("end_s"),
                        "duration_s": ev.get("duration_s"),
                        "relative_drop_db": ev.get("relative_drop_db"),
                        "event_similarity_count": ev.get("event_similarity_count"),
                        "approx_period_s": ev.get("approx_period_s"),
                        "repetition_strength": ev.get("repetition_strength"),
                    }
                    for ev in (row.get("energy_events") or [])
                ],
            }
            for row in fullmix_rows
        ],
        "regions": results,
    }
    name = artifact_name or (
        f"{source_run}_astra_r{diagnosis_revision}.json"
        if diagnosis_revision > 1
        else f"{source_run}_astra.json"
    )
    out = evidence / name
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload


def compare_astra_revisions(
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
    old_name: str = "session_run1_astra.json",
    new_name: str = "session_run1_astra_r2.json",
    fullmix_name: str = "session_run1_fullmix.json",
) -> dict[str, Any]:
    evidence = Path(evidence or "logs")
    old = json.loads((evidence / old_name).read_text(encoding="utf-8"))
    new = json.loads((evidence / new_name).read_text(encoding="utf-8"))
    fullmix = json.loads((evidence / fullmix_name).read_text(encoding="utf-8"))
    fm_by = {row["region_id"]: row for row in fullmix.get("regions") or []}
    old_by = {row["region_id"]: row for row in old.get("regions") or []}
    new_by = {row["region_id"]: row for row in new.get("regions") or []}
    rows = []
    for region_id in ("REGION_A", "REGION_B", "REGION_C"):
        o = old_by.get(region_id) or {}
        n = new_by.get(region_id) or {}
        f = fm_by.get(region_id) or {}
        events = f.get("energy_events") or []
        rows.append(
            {
                "region": region_id,
                "old_evidence": "LowEndObservation only",
                "new_fullmix_evidence": {
                    "energy_event_count": len(events),
                    "kinds": sorted({ev.get("kind") for ev in events}),
                    "events": [
                        {
                            "kind": ev.get("kind"),
                            "start_s": ev.get("start_s"),
                            "end_s": ev.get("end_s"),
                            "drop_db": ev.get("relative_drop_db"),
                            "similarity": ev.get("event_similarity_count"),
                            "period_s": ev.get("approx_period_s"),
                            "repetition_strength": ev.get("repetition_strength"),
                        }
                        for ev in events
                    ],
                },
                "astra_old": {
                    "accepted": o.get("accepted"),
                    "failure": o.get("failure"),
                    "category": (o.get("output") or {}).get("category"),
                    "status": (o.get("output") or {}).get("status"),
                    "summary": (o.get("output") or {}).get("summary"),
                },
                "astra_new": {
                    "accepted": n.get("accepted"),
                    "failure": n.get("failure"),
                    "category": (n.get("output") or {}).get("category"),
                    "status": (n.get("output") or {}).get("status"),
                    "summary": (n.get("output") or {}).get("summary"),
                },
                "human": n.get("human") or o.get("human"),
            }
        )
    payload = {
        "status": "ASTRA REVISION COMPARE",
        "source_run": source_run,
        "old_artifact": str(evidence / old_name),
        "new_artifact": str(evidence / new_name),
        "fullmix_artifact": str(evidence / fullmix_name),
        "MUSICAL WRITES": 0,
        "regions": rows,
    }
    out = evidence / f"{source_run}_astra_compare_r1_r2.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload


def revalidate_historical_r2_region(
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
    region_id: str = "REGION_C",
    r2_name: str = "session_run1_astra_r2.json",
) -> dict[str, Any]:
    """Re-run grounding on persisted Astra r2 output. No model call."""
    evidence = Path(evidence or "logs")
    r2 = json.loads((evidence / r2_name).read_text(encoding="utf-8"))
    row = next((r for r in r2.get("regions") or [] if r.get("region_id") == region_id), None)
    if row is None:
        raise ValueError(f"{region_id} missing from {r2_name}")
    if not row.get("output"):
        raise ValueError(f"{region_id} has no persisted output")
    packs, _ = load_merged_packs(evidence, source_run, include_fullmix=True)
    pack = next(p for p in packs if p.pack_id == region_id)
    output = ReasoningOutput.model_validate(row["output"])
    report = validate_reasoning(output, pack)
    before = "REJECTED" if not row.get("accepted") else "ACCEPTED"
    after = "ACCEPTED" if report.accepted else "REJECTED"
    payload = {
        "status": "HISTORICAL R2 REVALIDATION",
        "source_run": source_run,
        "region_id": region_id,
        "R2_C_BEFORE": before if region_id == "REGION_C" else ("REJECTED" if not row.get("accepted") else "ACCEPTED"),
        "R2_C_AFTER": after if region_id == "REGION_C" else after,
        f"R2_{region_id}_BEFORE": before,
        f"R2_{region_id}_AFTER": after,
        "before_failure": row.get("failure"),
        "after_accepted": report.accepted,
        "after_issues": [{"kind": i.kind.value, "detail": i.detail} for i in report.issues],
        "remaining_reason": None
        if report.accepted
        else (report.issues[0].detail if report.issues else "unknown"),
        "ASTRA CALLS": 0,
        "MUSICAL WRITES": 0,
        "MusicPlan": None,
        "note": "Persisted r2 output revalidated locally; r2 artifact not overwritten.",
    }
    # Annotate r2 artifact with revalidation block without changing output/accepted fields.
    r2["historical_revalidation"] = {
        region_id: {
            "before": before,
            "after": after,
            "issues": payload["after_issues"],
            "revalidated_at": now_iso(),
        }
    }
    (evidence / r2_name).write_text(json.dumps(r2, indent=2, ensure_ascii=False), encoding="utf-8")
    out = evidence / f"{source_run}_astra_r2_{region_id.lower()}_revalidate.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload


def run_region_causal_astra(
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
    region_id: str = "REGION_C",
    diagnosis_revision: int = 3,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
    store: EvalStore | None = None,
) -> dict[str, Any]:
    """Astra revision with FullMix + causal evidence for one region. No MusicPlan."""
    from copilot.audio.causal_evidence import gather_region_c_causal_evidence

    evidence = Path(evidence or "logs")
    store = store or EvalStore()
    assert_run_locked(source_run, store=store)
    provider = provider or configured_http_provider()
    if provider is None:
        return {
            "status": "REAL_MODEL_UNAVAILABLE",
            "reason": "No COPILOT_REASONING_API_KEY or OPENAI_API_KEY",
            "ASTRA CALLS": 0,
            "MUSICAL WRITES": 0,
            "MusicPlan": None,
            "MUSICPLAN_GATE": "CLOSED",
            "diagnosis_revision": diagnosis_revision,
        }

    causal = gather_region_c_causal_evidence(
        evidence=evidence,
        source_run=source_run,
        region_id=region_id,
    )
    packs, fullmix_rows = load_merged_packs(evidence, source_run, include_fullmix=True)
    pack = next(p for p in packs if p.pack_id == region_id)
    pack = merge_causal_into_pack(pack, causal)
    humans = _human_by_region(source_run, store=store)

    print(f"ASTRA r{diagnosis_revision} {pack.pack_id} {pack.region} +causal", flush=True)
    result = reason(pack, provider, timeout_s=timeout_s)
    print(
        f"ASTRA r{diagnosis_revision} {pack.pack_id} accepted={result.accepted} "
        f"failure={None if result.failure is None else result.failure.value}",
        flush=True,
    )
    calls = 1
    run = store.load_run(source_run)
    if run is not None:
        run.astra_calls = int(run.astra_calls or 0) + calls
        store.save_run(run)

    status = (result.output.status.value if result.output else None) or (
        result.diagnosis.status.value if result.diagnosis else None
    )
    gate = "CLOSED"
    if (
        result.accepted
        and status in {"SUPPORTED", "WEAKLY_SUPPORTED"}
        and result.diagnosis is not None
    ):
        # Still require unambiguous actionable target — keep CLOSED unless clearly met.
        actions = list((result.output.candidate_actions if result.output else []) or [])
        has_target = any(
            a.action_type.value not in {"NO_CHANGE", "REQUEST_EVIDENCE"} and a.target
            for a in actions
        )
        if has_target and status == "SUPPORTED":
            gate = "CLOSED"  # MusicPlan still blocked by NO LIVE-4 / explicit STOP
    # Explicit product rule: MusicPlan remains BLOCKED for this packet.
    gate = "CLOSED"

    human = humans.get(region_id) or {}
    region_row = {
        "region_id": region_id,
        "region": pack.region,
        "accepted": result.accepted,
        "failure": None if result.failure is None else result.failure.value,
        "diagnosis": None if result.diagnosis is None else result.diagnosis.model_dump(mode="json"),
        "output": None if result.output is None else result.output.model_dump(mode="json"),
        "audit": result.to_dict()["audit"],
        "human": human,
        "human_in_prompt": False,
        "pack_domain": pack.domain,
        "pack_analysis_version": pack.analysis_version,
        "causal_artifact": causal.get("artifact"),
    }
    payload = {
        "status": "ASTRA CAUSAL REVISION COMPLETE",
        "source_run": source_run,
        "diagnosis_revision": diagnosis_revision,
        "called_at": now_iso(),
        "provider": provider.identity,
        "provider_version": provider.version,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "include_fullmix": True,
        "include_causal": True,
        "region_id": region_id,
        "input_evidence_hashes": _evidence_hashes([pack]),
        "ASTRA CALLS": calls,
        "MUSICAL WRITES": 0,
        "MusicPlan": None,
        "MUSICPLAN_GATE": gate,
        "CandidateActions_executable": False,
        "human_in_prompt": False,
        "NO LIVE-4": True,
        "requested_evidence_from_r2": causal.get("requested_evidence_from_r2"),
        "evidence_obtained_summary": {
            "types": list((causal.get("evidence_obtained") or {}).keys()),
            "not_obtained": causal.get("not_obtained"),
            "ableton_ok": ((causal.get("evidence_obtained") or {}).get("RoutingObservation") or {}).get(
                "ok"
            ),
        },
        "fullmix_region": next(
            (row for row in fullmix_rows if row.get("region_id") == region_id),
            None,
        ),
        "regions": [region_row],
        "layers": causal.get("layers"),
    }
    out = evidence / f"{source_run}_astra_r{diagnosis_revision}_{region_id.lower()}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload


def run_region_causal_trace_astra(
    *,
    evidence: Path | None = None,
    source_run: str = "session_run1",
    region_id: str = "REGION_C",
    diagnosis_revision: int = 4,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
    store: EvalStore | None = None,
) -> dict[str, Any]:
    """Astra r4 with FullMix + CausalTrace exact timeline evidence. No MusicPlan auto-build."""
    from copilot.audio.causal_trace import build_causal_trace
    from copilot.reasoning.from_causal_trace import merge_causal_trace_into_pack

    evidence = Path(evidence or "logs")
    store = store or EvalStore()
    assert_run_locked(source_run, store=store)

    trace_payload = build_causal_trace(
        evidence=evidence,
        source_run=source_run,
        region_id=region_id,
    )
    if trace_payload.get("status") == "PROJECT_STATE_CONFLICT":
        return {
            **trace_payload,
            "ASTRA CALLS": 0,
            "MUSICPLAN_GATE": "CLOSED",
            "diagnosis_revision": diagnosis_revision,
        }

    provider = provider or configured_http_provider()
    if provider is None:
        return {
            "status": "REAL_MODEL_UNAVAILABLE",
            "reason": "No COPILOT_REASONING_API_KEY or OPENAI_API_KEY",
            "ASTRA CALLS": 0,
            "MUSICAL WRITES": 0,
            "MusicPlan": None,
            "MUSICPLAN_GATE": "CLOSED",
            "diagnosis_revision": diagnosis_revision,
            "causal_trace_artifact": trace_payload.get("artifact"),
        }

    packs, fullmix_rows = load_merged_packs(evidence, source_run, include_fullmix=True)
    pack = next(p for p in packs if p.pack_id == region_id)
    pack = merge_causal_trace_into_pack(pack, trace_payload)
    humans = _human_by_region(source_run, store=store)

    print(f"ASTRA r{diagnosis_revision} {pack.pack_id} {pack.region} +causal_trace", flush=True)
    result = reason(pack, provider, timeout_s=timeout_s)
    print(
        f"ASTRA r{diagnosis_revision} {pack.pack_id} accepted={result.accepted} "
        f"failure={None if result.failure is None else result.failure.value}",
        flush=True,
    )
    calls = 1
    run = store.load_run(source_run)
    if run is not None:
        run.astra_calls = int(run.astra_calls or 0) + calls
        store.save_run(run)

    status = (result.output.status.value if result.output else None) or (
        result.diagnosis.status.value if result.diagnosis else None
    )
    trace = trace_payload.get("trace") or {}
    strong = [
        c
        for c in (trace.get("supported_cause_candidates") or [])
        if c.get("strength") == "STRONG"
    ]
    actions = list((result.output.candidate_actions if result.output else []) or [])
    actionable = [
        a
        for a in actions
        if a.action_type.value not in {"NO_CHANGE", "REQUEST_EVIDENCE"} and a.target
    ]
    gate = "CLOSED"
    gate_reason = "default_closed"
    if not result.accepted:
        gate_reason = "diagnosis_not_accepted"
    elif status in {"INSUFFICIENT_EVIDENCE", "DIAGNOSIS_UNSTABLE"}:
        gate_reason = f"status_{status}"
    elif status == "NO_ACTION_REQUIRED":
        gate_reason = "no_action_required_structural_or_intentional"
    elif status in {"SUPPORTED", "WEAKLY_SUPPORTED"} and strong and actionable:
        gate = "OPEN"
        gate_reason = "supported_cause_and_actionable_target"
    elif status in {"SUPPORTED", "WEAKLY_SUPPORTED"} and not strong:
        gate_reason = "diagnosis_supported_but_no_strong_timeline_cause"
    elif status in {"SUPPORTED", "WEAKLY_SUPPORTED"} and not actionable:
        gate_reason = "supported_but_no_unambiguous_reversible_target"
    else:
        gate_reason = f"status_{status}"

    human = humans.get(region_id) or {}
    region_row = {
        "region_id": region_id,
        "region": pack.region,
        "accepted": result.accepted,
        "failure": None if result.failure is None else result.failure.value,
        "diagnosis": None if result.diagnosis is None else result.diagnosis.model_dump(mode="json"),
        "output": None if result.output is None else result.output.model_dump(mode="json"),
        "audit": result.to_dict()["audit"],
        "human": human,
        "human_in_prompt": False,
        "pack_domain": pack.domain,
        "pack_analysis_version": pack.analysis_version,
        "causal_trace_artifact": trace_payload.get("artifact"),
    }
    payload = {
        "status": "ASTRA CAUSAL TRACE REVISION COMPLETE",
        "source_run": source_run,
        "diagnosis_revision": diagnosis_revision,
        "called_at": now_iso(),
        "provider": provider.identity,
        "provider_version": provider.version,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "include_fullmix": True,
        "include_causal_trace": True,
        "region_id": region_id,
        "input_evidence_hashes": _evidence_hashes([pack]),
        "ASTRA CALLS": calls,
        "MUSICAL WRITES": 0,
        "MusicPlan": None,
        "MUSICPLAN_GATE": gate,
        "MUSICPLAN_GATE_REASON": gate_reason,
        "CandidateActions_executable": False,
        "NO LIVE-4": True,
        "primary_gap_event": next(
            (
                e
                for e in (trace.get("events") or [])
                if e.get("event_id") == trace.get("primary_gap_event_id")
            ),
            None,
        ),
        "ruled_out": trace.get("ruled_out"),
        "supported_cause_candidates": trace.get("supported_cause_candidates"),
        "next_evidence": trace.get("next_evidence"),
        "live_reconciliation": trace_payload.get("live_reconciliation"),
        "fullmix_region": next(
            (row for row in fullmix_rows if row.get("region_id") == region_id),
            None,
        ),
        "regions": [region_row],
    }
    out = evidence / f"{source_run}_astra_r{diagnosis_revision}_{region_id.lower()}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    payload["artifact"] = str(out)
    payload["causal_trace_artifact"] = trace_payload.get("artifact")
    return payload


def _pack_target_token(pack: EvidencePack) -> str | None:
    for item in pack.items:
        if item.target_token:
            return item.target_token
    return getattr(pack, "target_token", None)


def run_region_source_audio_astra(
    *,
    source_payload: dict[str, Any],
    evidence: Path | None = None,
    source_run: str = "session_run1",
    region_id: str = "REGION_C",
    diagnosis_revision: int = 5,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
    store: EvalStore | None = None,
    live_project_token: str | None = None,
    live_audible_token: str | None = None,
    live_target_token: str | None = None,
) -> dict[str, Any]:
    """Astra r5 on a Core-provided source-audio evidence package. No DAW access."""
    from copilot.reasoning.from_causal_trace import merge_causal_trace_into_pack
    from copilot.reasoning.from_source_audio import merge_source_audio_into_pack

    evidence = Path(evidence or "logs")
    store = store or EvalStore()
    assert_run_locked(source_run, store=store)

    if source_payload.get("status") in {"PRE-FLIGHT STOP", "PROJECT_STATE_CONFLICT"}:
        return {
            **source_payload,
            "ASTRA CALLS": 0,
            "MUSICPLAN_GATE": "CLOSED",
            "diagnosis_revision": diagnosis_revision,
        }

    trace_path = evidence / f"{source_run}_causal_trace_{region_id.lower()}.json"
    if not trace_path.is_file():
        return {
            "status": "EVIDENCE_INCOMPLETE",
            "reason": f"missing causal trace artifact: {trace_path}",
            "ASTRA CALLS": 0,
            "MUSICAL WRITES": 0,
            "MusicPlan": None,
            "MUSICPLAN_GATE": "CLOSED",
            "diagnosis_revision": diagnosis_revision,
        }
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    trace_payload["artifact"] = str(trace_path)

    provider = provider or configured_http_provider()
    if provider is None:
        return {
            "status": "REAL_MODEL_UNAVAILABLE",
            "reason": "No COPILOT_REASONING_API_KEY or OPENAI_API_KEY",
            "ASTRA CALLS": 0,
            "MUSICAL WRITES": 0,
            "MusicPlan": None,
            "MUSICPLAN_GATE": "CLOSED",
            "diagnosis_revision": diagnosis_revision,
            "source_audio_artifact": source_payload.get("artifact"),
            "observations": source_payload.get("observations"),
            "decision": source_payload.get("decision"),
        }

    packs, fullmix_rows = load_merged_packs(evidence, source_run, include_fullmix=True)
    pack = next(p for p in packs if p.pack_id == region_id)
    pack = merge_causal_trace_into_pack(pack, trace_payload)
    pack = merge_source_audio_into_pack(pack, source_payload)
    humans = _human_by_region(source_run, store=store)

    print(f"ASTRA r{diagnosis_revision} {pack.pack_id} {pack.region} +source_audio", flush=True)
    result = reason(pack, provider, timeout_s=timeout_s)
    print(
        f"ASTRA r{diagnosis_revision} {pack.pack_id} accepted={result.accepted} "
        f"failure={None if result.failure is None else result.failure.value}",
        flush=True,
    )
    calls = 1
    run = store.load_run(source_run)
    if run is not None:
        run.astra_calls = int(run.astra_calls or 0) + calls
        store.save_run(run)

    status = (result.output.status.value if result.output else None) or (
        result.diagnosis.status.value if result.diagnosis else None
    )
    decision = source_payload.get("decision") or {}
    actions = list((result.output.candidate_actions if result.output else []) or [])
    actionable = [
        a
        for a in actions
        if a.action_type.value not in {"NO_CHANGE", "REQUEST_EVIDENCE"} and a.target
    ]
    gate_result = evaluate_musicplan_gate(
        diagnosis_accepted=bool(result.accepted),
        diagnosis_status=status,
        actionable=bool(actionable) and decision.get("case") in {"A", "C"},
        evidence_project_token=source_payload.get("project_token") or pack.project_token,
        evidence_audible_token=source_payload.get("audible_token") or pack.audible_token,
        evidence_target_token=_pack_target_token(pack),
        live_project_token=live_project_token,
        live_audible_token=live_audible_token,
        live_target_token=live_target_token,
        require_target=bool(live_target_token is not None or _pack_target_token(pack)),
        require_cause_supported=False,
    )

    human = humans.get(region_id) or {}
    region_row = {
        "region_id": region_id,
        "region": pack.region,
        "accepted": result.accepted,
        "failure": None if result.failure is None else result.failure.value,
        "diagnosis": None if result.diagnosis is None else result.diagnosis.model_dump(mode="json"),
        "output": None if result.output is None else result.output.model_dump(mode="json"),
        "audit": result.to_dict()["audit"],
        "human": human,
        "pack_domain": pack.domain,
        "pack_analysis_version": pack.analysis_version,
        "source_audio_artifact": source_payload.get("artifact"),
    }
    payload = {
        "status": "ASTRA SOURCE AUDIO REVISION COMPLETE",
        "source_run": source_run,
        "diagnosis_revision": diagnosis_revision,
        "called_at": now_iso(),
        "provider": provider.identity,
        "provider_version": provider.version,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "region_id": region_id,
        "input_evidence_hashes": _evidence_hashes([pack]),
        "ASTRA CALLS": calls,
        "MUSICAL WRITES": 0,
        "AUDIBLE_MIX_MUTATIONS": source_payload.get("AUDIBLE_MIX_MUTATIONS", 0),
        "capture_routing_mutations": source_payload.get("capture_routing_mutations", 0),
        "MusicPlan": None,
        "MUSICPLAN_GATE": gate_result.gate,
        "MUSICPLAN_GATE_REASON": gate_result.reason,
        "MUSICPLAN_GATE_CODE": gate_result.code,
        "CandidateActions_executable": False,
        "NO LIVE-4": True,
        "observations": source_payload.get("observations"),
        "decision": decision,
        "passes": source_payload.get("passes"),
        "event_qn_range": source_payload.get("event_qn_range"),
        "event_audio_range_s": source_payload.get("event_audio_range_s"),
        "regions": [region_row],
        "fullmix_observation_count": len(fullmix_rows),
    }
    out = evidence / f"{source_run}_astra_r{diagnosis_revision}_{region_id.lower()}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    payload["artifact"] = str(out)
    payload["source_audio_artifact"] = source_payload.get("artifact")
    return payload


def run_region_strum_device_astra(
    *,
    strum_payload: dict[str, Any],
    evidence: Path | None = None,
    source_run: str = "session_run1",
    region_id: str = "REGION_C",
    diagnosis_revision: int = 6,
    provider: ReasoningProvider | None = None,
    timeout_s: float = ASTRA_TIMEOUT_S,
    store: EvalStore | None = None,
    live_project_token: str | None = None,
    live_audible_token: str | None = None,
    live_target_token: str | None = None,
) -> dict[str, Any]:
    """Astra r6 on a Core-provided Strum device evidence package. No DAW access."""
    from copilot.reasoning.from_causal_trace import merge_causal_trace_into_pack
    from copilot.reasoning.from_source_audio import merge_source_audio_into_pack
    from copilot.reasoning.from_strum_device import merge_strum_device_into_pack

    evidence = Path(evidence or "logs")
    store = store or EvalStore()
    assert_run_locked(source_run, store=store)

    if strum_payload.get("status") == "PRE-FLIGHT STOP":
        return {
            **strum_payload,
            "ASTRA CALLS": 0,
            "MUSICPLAN_GATE": "CLOSED",
            "diagnosis_revision": diagnosis_revision,
        }

    trace_path = evidence / f"{source_run}_causal_trace_{region_id.lower()}.json"
    source_path = evidence / f"{source_run}_source_audio_trace_{region_id.lower()}.json"
    if not trace_path.is_file() or not source_path.is_file():
        return {
            "status": "EVIDENCE_INCOMPLETE",
            "reason": "missing prior causal/source-audio artifacts",
            "ASTRA CALLS": 0,
            "MUSICAL WRITES": 0,
            "MusicPlan": None,
            "MUSICPLAN_GATE": "CLOSED",
            "diagnosis_revision": diagnosis_revision,
        }
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    trace_payload["artifact"] = str(trace_path)
    source_payload = json.loads(source_path.read_text(encoding="utf-8"))
    source_payload["artifact"] = str(source_path)

    provider = provider or configured_http_provider()
    if provider is None:
        return {
            "status": "REAL_MODEL_UNAVAILABLE",
            "reason": "No COPILOT_REASONING_API_KEY or OPENAI_API_KEY",
            "ASTRA CALLS": 0,
            "MUSICAL WRITES": 0,
            "MusicPlan": None,
            "MUSICPLAN_GATE": "CLOSED",
            "diagnosis_revision": diagnosis_revision,
            "strum_device_artifact": strum_payload.get("artifact"),
            "cause_result": strum_payload.get("cause_result"),
        }

    packs, _ = load_merged_packs(evidence, source_run, include_fullmix=True)
    pack = next(p for p in packs if p.pack_id == region_id)
    pack = merge_causal_trace_into_pack(pack, trace_payload)
    pack = merge_source_audio_into_pack(pack, source_payload)
    pack = merge_strum_device_into_pack(pack, strum_payload)
    humans = _human_by_region(source_run, store=store)

    print(f"ASTRA r{diagnosis_revision} {pack.pack_id} {pack.region} +strum_device", flush=True)
    result = reason(pack, provider, timeout_s=timeout_s)
    print(
        f"ASTRA r{diagnosis_revision} {pack.pack_id} accepted={result.accepted} "
        f"failure={None if result.failure is None else result.failure.value}",
        flush=True,
    )
    calls = 1
    run = store.load_run(source_run)
    if run is not None:
        run.astra_calls = int(run.astra_calls or 0) + calls
        store.save_run(run)

    status = (result.output.status.value if result.output else None) or (
        result.diagnosis.status.value if result.diagnosis else None
    )
    cause = strum_payload.get("cause_result") or {}
    actions = list((result.output.candidate_actions if result.output else []) or [])
    actionable = [
        a
        for a in actions
        if a.action_type.value not in {"NO_CHANGE", "REQUEST_EVIDENCE"} and a.target
    ]
    snap = strum_payload.get("state_snapshot") or {}
    evidence_project = snap.get("PROJECT_STATE_TOKEN") or pack.project_token
    evidence_audible = snap.get("AUDIBLE_STATE_TOKEN") or pack.audible_token
    evidence_target = snap.get("TARGET_STATE_TOKEN") or _pack_target_token(pack)
    gate_result = evaluate_musicplan_gate(
        diagnosis_accepted=bool(result.accepted),
        diagnosis_status=status,
        actionable=bool(actionable),
        cause_status=(cause.get("status") if isinstance(cause, dict) else None),
        require_cause_supported=True,
        evidence_project_token=evidence_project,
        evidence_audible_token=evidence_audible,
        evidence_target_token=evidence_target,
        live_project_token=live_project_token,
        live_audible_token=live_audible_token,
        live_target_token=live_target_token,
        require_target=True,
    )

    human = humans.get(region_id) or {}
    region_row = {
        "region_id": region_id,
        "region": pack.region,
        "accepted": result.accepted,
        "failure": None if result.failure is None else result.failure.value,
        "diagnosis": None if result.diagnosis is None else result.diagnosis.model_dump(mode="json"),
        "output": None if result.output is None else result.output.model_dump(mode="json"),
        "audit": result.to_dict()["audit"],
        "human": human,
        "pack_domain": pack.domain,
        "strum_device_artifact": strum_payload.get("artifact"),
    }
    payload = {
        "status": "ASTRA STRUM DEVICE REVISION COMPLETE",
        "source_run": source_run,
        "diagnosis_revision": diagnosis_revision,
        "called_at": now_iso(),
        "provider": provider.identity,
        "provider_version": provider.version,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "region_id": region_id,
        "input_evidence_hashes": _evidence_hashes([pack]),
        "ASTRA CALLS": calls,
        "MUSICAL WRITES": 0,
        "MusicPlan": None,
        "MUSICPLAN_GATE": gate_result.gate,
        "MUSICPLAN_GATE_REASON": gate_result.reason,
        "MUSICPLAN_GATE_CODE": gate_result.code,
        "CandidateActions_executable": False,
        "NO LIVE-4": True,
        "state_snapshot": strum_payload.get("state_snapshot"),
        "cause_result": cause,
        "automation_candidates": strum_payload.get("automation_candidates"),
        "event_qn_range": strum_payload.get("event_qn_range"),
        "regions": [region_row],
    }
    out = evidence / f"{source_run}_astra_r{diagnosis_revision}_{region_id.lower()}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    payload["artifact"] = str(out)
    payload["strum_device_artifact"] = strum_payload.get("artifact")
    return payload
