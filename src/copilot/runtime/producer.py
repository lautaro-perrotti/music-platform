"""Producer Runtime — agent intent in, execution graph out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copilot.perf.ableton import instrumented_rpc
from copilot.perf.trace import CAT_ORCH, active_trace, span
from copilot.runtime.capabilities import build_registry
from copilot.runtime.contracts import (
    AnalyzeProjectRequest,
    AnalyzeProjectResult,
    TaskStatus,
)
from copilot.runtime.context import OperationContext, TaskBlocked
from copilot.runtime.frozen import assert_mutable_output
from copilot.runtime.graph import (
    ExecutionGraph,
    new_node,
    optimize_graph,
    run_graph,
)
from copilot.runtime.rpc import ProjectReadView, install_read_view, rpc_metrics
from copilot.schemas.diagnosis import DiagnosisStatus

PRESERVED = {
    DiagnosisStatus.SUPPORTED.value,
    DiagnosisStatus.WEAKLY_SUPPORTED.value,
    DiagnosisStatus.INSUFFICIENT_EVIDENCE.value,
    DiagnosisStatus.NO_ACTION_REQUIRED.value,
    DiagnosisStatus.DIAGNOSIS_UNSTABLE.value,
    "ACTION_NOT_AVAILABLE",
    "BLOCKED",
}

DEFERRED = {
    "RUNTIME_LAUNCH_PATH_OPTIMIZATION": (
        "launcher unconditional sleep and PROBE_BACKOFF overshoot remain "
        "unresolved. Not modified in PRODUCER_RUNTIME_V1."
    ),
    "COMPOUND_CAPTURE_HOST_MUTATIONS": (
        "ABLETON_MUTATION_PROTOCOL_V1 is VERIFIED / FROZEN on Groove Rider Live. "
        "Handshake-gated on compound.temporary_mutation. Sequential fallback "
        "remains for older Remote Scripts. Do not use MutationBatch for musical "
        "production writes."
    ),
}


def compile_analyze_project(request: AnalyzeProjectRequest) -> ExecutionGraph:
    """Intent → dependency graph. No execution here."""
    resolve = new_node("PROJECT_RESOLUTION")
    ready_session = new_node("SESSION_READY", [resolve.node_id])
    snapshot = new_node("PROJECT_SNAPSHOT", [ready_session.node_id])
    project_ready = new_node("PROJECT_READY", [snapshot.node_id])
    requirements = new_node("DETERMINE_EVIDENCE", [project_ready.node_id])
    arrangement = new_node("READ_ARRANGEMENT", [requirements.node_id])
    capture_main = new_node("CAPTURE_MAIN", [arrangement.node_id])
    capture_sources = new_node("CAPTURE_SOURCES", [capture_main.node_id])
    fullmix = new_node("FULLMIX_ANALYSIS", [capture_sources.node_id])
    lowend = new_node("LOWEND_ANALYSIS", [fullmix.node_id])
    fusion_deps = [fullmix.node_id, lowend.node_id]
    nodes = [
        resolve,
        ready_session,
        snapshot,
        project_ready,
        requirements,
        arrangement,
        capture_main,
        capture_sources,
        fullmix,
        lowend,
    ]
    goals = set(request.goals)
    if "HARMONIC_CONTEXT" in goals:
        midi = new_node("READ_MIDI", [arrangement.node_id])
        nodes.append(midi)
        fusion_deps.append(midi.node_id)
    if "DEVICE_CAUSAL_CONTEXT" in goals:
        routing = new_node("READ_ROUTING", [snapshot.node_id, arrangement.node_id])
        devices = new_node("READ_DEVICES", [snapshot.node_id, arrangement.node_id])
        nodes.extend([routing, devices])
        fusion_deps.extend([routing.node_id, devices.node_id])
    fusion = new_node("EVIDENCE_FUSION", fusion_deps)
    reasoning = new_node("ASTRA_REASONING", [fusion.node_id])
    terminal = new_node("TERMINAL_VERIFICATION", [reasoning.node_id])
    nodes.extend([fusion, reasoning, terminal])
    return ExecutionGraph(task="AnalyzeProject", nodes=nodes)


def _status_from_blackboard(blackboard: dict[str, Any], cleanup: dict[str, Any]) -> TaskStatus:
    if cleanup and cleanup.get("ok") is False:
        return TaskStatus.FAILED
    ready = blackboard.get("PROJECT_READY") or blackboard.get("ready") or {}
    if ready.get("PROJECT_READY") == "BLOCKED" or ready.get("status") == "BLOCKED":
        return TaskStatus.BLOCKED
    session = blackboard.get("SESSION_READY") or {}
    if session.get("status") and session.get("status") not in {"SESSION_READY", "OK"}:
        return TaskStatus.BLOCKED
    status = str(blackboard.get("status") or (blackboard.get("ASTRA_REASONING") or {}).get("status") or "")
    if status in PRESERVED:
        if status == "BLOCKED":
            return TaskStatus.BLOCKED
        return TaskStatus.SUCCEEDED
    if status == "SKIPPED":
        return TaskStatus.SUCCEEDED
    if blackboard.get("built"):
        return TaskStatus.SUCCEEDED
    return TaskStatus.BLOCKED


class Producer:
    """One high-level call per product intent."""

    def __init__(
        self,
        *,
        evidence: Path | str | None = None,
        daw: Any = None,
        registry: Any = None,
        deadline_s: float | None = None,
    ) -> None:
        self.evidence = Path(evidence) if evidence is not None else Path("logs")
        assert_mutable_output(self.evidence)
        self.daw = daw
        self.registry = registry or build_registry()
        self.deadline_s = deadline_s

    def analyze_project(
        self,
        project: str | Path | None = None,
        *,
        scope: str = "default",
        region_preference: str | None = None,
        start_qn: float | None = None,
        end_qn: float | None = None,
        reasoning_enabled: bool = True,
        goals: list[str] | tuple[str, ...] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> AnalyzeProjectResult:
        request = project if isinstance(project, AnalyzeProjectRequest) else AnalyzeProjectRequest(
            project=None if project is None else str(project),
            scope=scope,
            region_preference=region_preference,
            start_qn=start_qn,
            end_qn=end_qn,
            reasoning_enabled=reasoning_enabled,
            goals=goals,
            constraints=constraints,
        )
        return self._run_analyze(request)

    def _run_analyze(self, request: AnalyzeProjectRequest) -> AnalyzeProjectResult:
        evidence = self.evidence
        assert_mutable_output(evidence)
        evidence.mkdir(parents=True, exist_ok=True)
        graph = compile_analyze_project(request)
        optimized, optimizer = optimize_graph(graph, self.registry)
        ctx = OperationContext.create(evidence_root=evidence, deadline_s=self.deadline_s)
        view = ProjectReadView()
        blackboard: dict[str, Any] = {
            "request": request,
            "evidence": evidence,
            "read_view": view,
            "_owns_daw": self.daw is None,
        }
        if self.daw is not None:
            blackboard["daw"] = self.daw

        def _execute() -> dict[str, Any]:
            return run_graph(optimized, self.registry, ctx, blackboard)

        with active_trace("AnalyzeProject", project_id=request.project) as trace:
            ctx.trace = trace
            with span("compile_and_optimize", category=CAT_ORCH):
                trace.attributes["optimizer"] = optimizer.to_dict()
            try:
                if self.daw is not None:
                    with install_read_view(self.daw, view), instrumented_rpc(self.daw):
                        self.daw._runtime_instrumented = True
                        executed = _execute()
                else:
                    executed = _execute()
            except TaskBlocked as exc:
                return AnalyzeProjectResult(
                    status=TaskStatus.BLOCKED,
                    reason=exc.reason,
                    performance={"error": "TaskBlocked"},
                    rpc=rpc_metrics(trace, view),
                    payload={
                        "graph": optimized.to_dict(),
                        "optimizer": optimizer.to_dict(),
                        "blocked": exc.payload,
                        "deferred": DEFERRED,
                    },
                    musical_writes=0,
                )
            except Exception as exc:  # noqa: BLE001
                return AnalyzeProjectResult(
                    status=TaskStatus.FAILED,
                    reason=str(exc),
                    performance={"error": type(exc).__name__},
                    rpc=rpc_metrics(trace, view),
                    payload={
                        "graph": optimized.to_dict(),
                        "optimizer": optimizer.to_dict(),
                        "deferred": DEFERRED,
                    },
                    musical_writes=0,
                )
            rpc = rpc_metrics(trace, view)
            rpc["optimizer"] = optimizer.to_dict()
            rpc["compound_mutations"] = {
                "capability": "compound.temporary_mutation",
                "live_execution": "handshake_gated",
                "sequential_fallback": True,
            }
            try:
                (evidence / "RPC_TRACE_V2.json").write_text(
                    json.dumps(rpc.get("rpc_trace") or {}, indent=2, default=str),
                    encoding="utf-8",
                )
            except Exception:
                pass
            status = _status_from_blackboard(blackboard, executed.get("cleanup") or {})
            diagnosis = blackboard.get("diagnosis")
            built = blackboard.get("built") or {}
            pack = (built.get("pack") or {}) if isinstance(built, dict) else {}
            session = blackboard.get("session")
            terminal = blackboard.get("terminal") or {}
            gate = blackboard.get("gate") or {}
            if status == TaskStatus.SUCCEEDED and terminal and not terminal.get("ok", True):
                status = TaskStatus.FAILED
                reason = "TERMINAL_NOT_RESTORED"
            else:
                reason = gate.get("reason") or blackboard.get("status")
            performance = {
                "wall_time": trace.total_s,
                "metrics": trace.metrics().to_dict(),
                "optimizer": optimizer.to_dict(),
                "parallel_savings_s": executed.get("parallel_savings_s"),
                "tree": trace.render(),
                "deferred": DEFERRED,
            }
            try:
                trace.persist(evidence, name="analyze_project_trace.json")
            except Exception:
                pass
            return AnalyzeProjectResult(
                status=status,
                reason=None if status == TaskStatus.SUCCEEDED else str(reason),
                project_identity=getattr(session, "project_identity", None)
                or ctx.project_identity,
                analysis_scope=request.scope,
                evidence_summary={
                    "pack_id": pack.get("pack_id"),
                    "region": blackboard.get("region"),
                    "captures": (blackboard.get("CAPTURE_SOURCES") or {}).get(
                        "capture_count"
                    ),
                    "evidence_view": (blackboard.get("EVIDENCE_FUSION") or {}).get(
                        "evidence_view"
                    ),
                },
                diagnosis=diagnosis,
                confidence=(diagnosis or {}).get("status")
                if isinstance(diagnosis, dict)
                else None,
                limitations=list(
                    (pack.get("limitations") or []) if isinstance(pack, dict) else []
                ),
                next_evidence_requests=_next_evidence(request, blackboard),
                gate=gate,
                performance=performance,
                rpc=rpc,
                terminal=terminal if isinstance(terminal, dict) else {},
                payload={
                    "graph": optimized.to_dict(),
                    "optimizer": optimizer.to_dict(),
                    "canonical_report": _canonical_report(blackboard),
                    "high_level_calls": 1,
                    "deferred": DEFERRED,
                },
                musical_writes=0,
            )


def _next_evidence(request: AnalyzeProjectRequest, blackboard: dict[str, Any]) -> list[str]:
    diagnosis = blackboard.get("diagnosis") or {}
    raw = []
    if isinstance(diagnosis, dict):
        raw = list(diagnosis.get("requested_evidence") or [])
    formatted: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            kind = item.get("request_kind") or ""
            target = item.get("target") or ""
            region = item.get("region") or ""
            why = item.get("why_needed") or item.get("reason") or ""
            formatted.append(f"{kind} for {target} in {region}: {why}".strip(": ").strip())
        else:
            formatted.append(str(item))
    if formatted:
        return formatted
    have = set(request.goals)
    extra = []
    if "HARMONIC_CONTEXT" not in have:
        extra.append("HARMONIC_CONTEXT")
    if "DEVICE_CAUSAL_CONTEXT" not in have:
        extra.append("DEVICE_CAUSAL_CONTEXT")
    return extra


def _canonical_report(blackboard: dict[str, Any]) -> dict[str, Any]:
    from copilot.human_eval.store import now_iso

    built = blackboard.get("built") or {}
    pack = built.get("pack") if isinstance(built, dict) else None
    return {
        "milestone": "PRODUCER_ANALYZE_V1",
        "status": blackboard.get("status"),
        "ts": now_iso(),
        "project_ready": {
            "PROJECT_READY": (blackboard.get("ready") or {}).get("PROJECT_READY")
        },
        "region": blackboard.get("region"),
        "evidence_pack_id": None if not pack else pack.get("pack_id"),
        "diagnosis": blackboard.get("diagnosis"),
        "gate": blackboard.get("gate"),
        "reasoning_audit": blackboard.get("reasoning_audit"),
        "terminal": blackboard.get("terminal"),
        "NO WRITE": True,
    }
