"""Execution graph, optimizer, and structured-concurrency scheduler."""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from copilot.perf.trace import CAT_ORCH, span
from copilot.runtime.context import CleanupFailure, OperationContext
from copilot.runtime.registry import Capability, CapabilityRegistry
from copilot.runtime.resources import ResourceReq


@dataclass
class GraphNode:
    node_id: str
    capability_id: str
    depends_on: list[str] = field(default_factory=list)
    resources: list[ResourceReq] = field(default_factory=list)
    batch_group: str | None = None
    skip: bool = False
    skip_reason: str | None = None
    parallel_group: str | None = None
    input_key: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "capability_id": self.capability_id,
            "depends_on": list(self.depends_on),
            "skip": self.skip,
            "skip_reason": self.skip_reason,
            "batch_group": self.batch_group,
            "parallel_group": self.parallel_group,
            "attributes": self.attributes,
        }


@dataclass
class ExecutionGraph:
    task: str
    nodes: list[GraphNode] = field(default_factory=list)

    def node_map(self) -> dict[str, GraphNode]:
        return {node.node_id: node for node in self.nodes}

    def edges(self) -> list[tuple[str, str]]:
        return [
            (dep, node.node_id)
            for node in self.nodes
            for dep in node.depends_on
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [list(edge) for edge in self.edges()],
        }


@dataclass
class OptimizerReport:
    operations_before: int
    operations_after: int
    duplicates_removed: int = 0
    batch_groups: int = 0
    rpcs_eliminated: int = 0
    parallel_groups: int = 0
    cache_hits: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operations_before": self.operations_before,
            "operations_after": self.operations_after,
            "duplicates_removed": self.duplicates_removed,
            "batch_groups": self.batch_groups,
            "rpcs_eliminated": self.rpcs_eliminated,
            "parallel_groups": self.parallel_groups,
            "cache_hits": self.cache_hits,
            "decisions": self.decisions,
        }


def _ready(node: GraphNode, done: set[str], nodes: dict[str, GraphNode]) -> bool:
    return all(dep in done or nodes[dep].skip for dep in node.depends_on)


def optimize_graph(
    graph: ExecutionGraph,
    registry: CapabilityRegistry,
    *,
    satisfied: set[str] | None = None,
    cache_hits: set[str] | None = None,
) -> tuple[ExecutionGraph, OptimizerReport]:
    satisfied = set(satisfied or ())
    cache_hits = set(cache_hits or ())
    before = len(graph.nodes)
    seen: dict[tuple[str, str | None], str] = {}
    kept: list[GraphNode] = []
    duplicates = 0
    decisions: list[dict[str, Any]] = []
    for node in graph.nodes:
        key = (node.capability_id, node.input_key)
        if node.capability_id in satisfied:
            node.skip = True
            node.skip_reason = "already_satisfied"
            decisions.append({"node": node.node_id, "decision": "skip_satisfied"})
        elif node.capability_id in cache_hits:
            node.skip = True
            node.skip_reason = "strong_cache_hit"
            decisions.append({"node": node.node_id, "decision": "skip_cache"})
        elif key in seen:
            node.skip = True
            node.skip_reason = f"duplicate_of:{seen[key]}"
            duplicates += 1
            decisions.append(
                {
                    "node": node.node_id,
                    "decision": "duplicate_removed",
                    "kept": seen[key],
                }
            )
        else:
            seen[key] = node.node_id
        cap = registry.get(node.capability_id)
        node.resources = list(cap.resources)
        if cap.supports_batch and cap.max_batch:
            node.attributes.setdefault("max_batch", cap.max_batch)
        kept.append(node)

    batch_groups = 0
    sources = [n for n in kept if n.capability_id == "CAPTURE_SOURCES" and not n.skip]
    if sources:
        cap = registry.get("CAPTURE_SOURCES")
        width = int(cap.max_batch or 2)
        for offset, node in enumerate(sources):
            node.batch_group = f"sources_{offset // width}"
            node.attributes["max_batch_sources"] = width
        batch_groups = len({n.batch_group for n in sources})
        decisions.append(
            {
                "decision": "batch_split",
                "capability": "CAPTURE_SOURCES",
                "max_batch_sources": width,
                "groups": batch_groups,
                "sources": len(sources),
            }
        )

    parallel_ids = {"FULLMIX_ANALYSIS", "LOWEND_ANALYSIS", "READ_MIDI", "READ_ROUTING", "READ_DEVICES"}
    parallel_nodes = [n for n in kept if n.capability_id in parallel_ids and not n.skip]
    parallel_groups = 0
    if len(parallel_nodes) >= 2:
        group = "post_capture_local"
        for node in parallel_nodes:
            node.parallel_group = group
        parallel_groups = 1
        decisions.append({"decision": "parallel_group", "group": group, "nodes": len(parallel_nodes)})

    after = len([n for n in kept if not n.skip])
    report = OptimizerReport(
        operations_before=before,
        operations_after=after,
        duplicates_removed=duplicates,
        batch_groups=batch_groups,
        parallel_groups=parallel_groups,
        cache_hits=len(cache_hits),
        decisions=decisions,
    )
    return ExecutionGraph(task=graph.task, nodes=kept), report


def split_batches(items: list[Any], max_batch: int) -> list[list[Any]]:
    width = max(1, int(max_batch))
    return [items[i : i + width] for i in range(0, len(items), width)]


def _can_run_together(a: GraphNode, b: GraphNode) -> bool:
    for req in a.resources:
        for other in b.resources:
            if req.conflicts(other):
                return False
    return True


def schedule_ready(
    graph: ExecutionGraph, done: set[str]
) -> list[list[GraphNode]]:
    """Return waves of nodes that can run together."""
    nodes = graph.node_map()
    ready = [
        node
        for node in graph.nodes
        if not node.skip and node.node_id not in done and _ready(node, done, nodes)
    ]
    waves: list[list[GraphNode]] = []
    remaining = list(ready)
    while remaining:
        wave: list[GraphNode] = []
        next_remaining: list[GraphNode] = []
        for node in remaining:
            if all(_can_run_together(node, other) for other in wave):
                wave.append(node)
            else:
                next_remaining.append(node)
        waves.append(wave)
        remaining = next_remaining
    return waves


def run_graph(
    graph: ExecutionGraph,
    registry: CapabilityRegistry,
    ctx: OperationContext,
    blackboard: dict[str, Any],
) -> dict[str, Any]:
    done: set[str] = {n.node_id for n in graph.nodes if n.skip}
    outputs: dict[str, Any] = {}
    resource_wait_s = 0.0
    parallel_savings_s = 0.0
    try:
        while len(done) < len(graph.nodes):
            ctx.check()
            waves = schedule_ready(graph, done)
            if not waves:
                pending = [n.node_id for n in graph.nodes if n.node_id not in done]
                raise RuntimeError(f"graph stalled: {pending}")
            wave = waves[0]
            if len(wave) == 1:
                node = wave[0]
                outputs[node.node_id] = _run_node(node, registry, ctx, blackboard)
                done.add(node.node_id)
                continue
            wall = _run_parallel(wave, registry, ctx, blackboard, outputs)
            exclusive = sum(
                float((outputs[n.node_id] or {}).get("_wall_s") or 0.0) for n in wave
            )
            parallel_savings_s += max(0.0, exclusive - wall)
            done.update(n.node_id for n in wave)
    except Exception:
        _run_finalizers(ctx)
        raise
    cleanup = _run_finalizers(ctx)
    return {
        "outputs": outputs,
        "cleanup": cleanup,
        "resource_wait_s": resource_wait_s,
        "parallel_savings_s": round(parallel_savings_s, 6),
    }


def _run_finalizers(ctx: OperationContext) -> dict[str, Any]:
    try:
        results = ctx.finalizers.run_all()
        return {"ok": True, "results": results}
    except CleanupFailure as exc:
        return {"ok": False, "error": str(exc), "results": ctx.finalizers.results}


def _run_node(
    node: GraphNode,
    registry: CapabilityRegistry,
    ctx: OperationContext,
    blackboard: dict[str, Any],
) -> Any:
    cap = registry.get(node.capability_id)
    if cap.execute is None:
        raise RuntimeError(f"no execute for {node.capability_id}")
    ctx.check()
    ctx.resources.acquire(node.node_id, node.resources or cap.resources)
    started = __import__("time").perf_counter()
    try:
        with span(node.capability_id, category=CAT_ORCH, node_id=node.node_id):
            result = cap.execute(ctx=ctx, blackboard=blackboard, node=node)
    finally:
        ctx.resources.release(node.node_id)
    if isinstance(result, dict):
        result = dict(result)
        result["_wall_s"] = __import__("time").perf_counter() - started
    blackboard[node.capability_id] = result
    blackboard[node.node_id] = result
    return result


def _run_parallel(
    wave: list[GraphNode],
    registry: CapabilityRegistry,
    ctx: OperationContext,
    blackboard: dict[str, Any],
    outputs: dict[str, Any],
) -> float:
    started = __import__("time").perf_counter()
    errors: list[BaseException] = []

    def work(node: GraphNode) -> tuple[str, Any]:
        return node.node_id, _run_node(node, registry, ctx, blackboard)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(wave)) as pool:
        futures = [pool.submit(work, node) for node in wave]
        for future in concurrent.futures.as_completed(futures):
            try:
                node_id, result = future.result()
                outputs[node_id] = result
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                ctx.cancellation.cancel(f"child_failed:{exc}")
    if errors:
        raise errors[0]
    return __import__("time").perf_counter() - started


def new_node(capability_id: str, depends_on: list[str] | None = None, **attrs: Any) -> GraphNode:
    return GraphNode(
        node_id=f"{capability_id}:{uuid4().hex[:8]}",
        capability_id=capability_id,
        depends_on=list(depends_on or []),
        attributes=dict(attrs),
        input_key=str(attrs.get("input_key") or capability_id),
    )
