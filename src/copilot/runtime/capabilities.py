"""Wrap existing canonical capabilities. Do not copy their implementations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copilot.audio.arrangement_seek import PRE_ROLL_QN
from copilot.audio.capture_scalability_v2 import PRACTICAL_MAX_SOURCE_SLOTS
from copilot.audio.source_capture_batch_v1 import plan_batches
from copilot.runtime.context import OperationContext, TaskBlocked
from copilot.runtime.graph import GraphNode
from copilot.runtime.registry import (
    Capability,
    CapabilityRegistry,
    FailureSemantics,
    LatencyCategory,
    Provider,
)
from copilot.runtime.resources import (
    ResourceKind,
    exclusive,
    shared,
)

ABLETON = Provider("Ableton", version="live-tcp-1")
DSP = Provider("EssentiaCompat", version="fullmix-obs-1")
ASTRA = Provider("Astra", version="gpt-6-astra")
ALS = Provider("AlsParser", version="midi-read-only-1")
FS = Provider("Filesystem", version="1")
RUNTIME = Provider("ProducerRuntime", version="v1")

# Upper bound for scheduler metadata. Actual pass width is discovered live.
CAPTURE_MAX_BATCH_SOURCES = PRACTICAL_MAX_SOURCE_SLOTS
CAPTURE_PRE_ROLL_QN = PRE_ROLL_QN


def _bb(blackboard: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in blackboard:
            return blackboard[key]
    return None


def cap_resolve_project(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    request = blackboard["request"]
    project = request.project
    evidence = Path(ctx.evidence_root or blackboard["evidence"])
    if not project:
        return {"status": "CURRENT_SESSION", "project": None, "folder": None}
    path = Path(str(project))
    if path.is_file() and path.suffix.lower() == ".als":
        return {"status": "ALS", "project": str(path), "folder": str(path.parent)}
    from copilot.importing.project_folder_resolver_v1 import resolve_ableton_project
    from copilot.importing.working_copy_manager_v1 import create_working_copy

    discovery = resolve_ableton_project(path)
    if discovery.get("status") != "RESOLVED":
        return {"status": "BLOCKED", "reason": discovery.get("status"), "discovery": discovery}
    source_als = Path(str(discovery["source_als"]))
    source_root = Path(str(discovery["project_root"]))
    copy = create_working_copy(
        source_als=source_als,
        project_root=source_root,
        copy_scope=str(discovery.get("copy_scope") or "project_directory"),
    )
    blackboard["working_als"] = copy.get("working_als")
    return {
        "status": copy.get("status"),
        "discovery": discovery,
        "copy": copy,
        "working_als": copy.get("working_als"),
        "folder": str(path),
        "evidence": str(evidence),
    }


def cap_session_ready(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.daw.session_ready_v1 import SESSION_READY, probe_session_ready, wait_for_session
    from copilot.importing.ableton_launcher_v1 import launch_working_copy, names_match, paths_match

    resolved = _bb(blackboard, "PROJECT_RESOLUTION") or {}
    working_als = resolved.get("working_als") or blackboard.get("working_als")
    if working_als:
        from copilot.importing.m4l_runtime_v1 import ensure_m4l_runtime

        evidence = Path(ctx.evidence_root or blackboard["evidence"])
        provision = ensure_m4l_runtime(evidence=evidence)
        launch = launch_working_copy(Path(str(working_als)), force=provision.get("status") in {"INSTALLED", "UPDATED"})
        probe = wait_for_session(deadline_s=ctx.deadline.timeout_or(120.0))
        if probe.status != SESSION_READY:
            raise TaskBlocked(probe.status, {"session": probe.to_dict(), "launch": launch})
        if not paths_match(probe.project_path, Path(str(working_als))) and not names_match(
            probe.project_name, Path(str(working_als))
        ):
            raise TaskBlocked("OPENED_PROJECT_MISMATCH", {"session": probe.to_dict()})
    else:
        probe = probe_session_ready()
        if probe.status != SESSION_READY:
            raise TaskBlocked(probe.status, {"session": probe.to_dict()})
        launch = {"status": "ALREADY_OPEN"}
    daw = blackboard.get("daw")
    owns = bool(blackboard.get("_owns_daw", daw is None))
    if daw is None:
        daw = AbletonTcpAdapter()
        owns = True
        blackboard["_owns_daw"] = True
    if getattr(daw, "_sock", None) is None:
        daw.connect()
    blackboard["daw"] = daw
    view = blackboard.get("read_view")
    if view is not None and not getattr(daw, "_runtime_instrumented", False):
        from copilot.runtime.rpc import attach_runtime_instrumentation

        attach_runtime_instrumentation(daw, view, ctx)
        daw._runtime_instrumented = True

    if owns:
        def _disconnect() -> None:
            try:
                daw.disconnect()
            except Exception:
                pass

        ctx.finalizers.register(_disconnect, name="disconnect_ableton", order=10)
    return {"status": SESSION_READY, "session": probe.to_dict(), "launch": launch}


def cap_snapshot(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.audio.cross_project_bootstrap_v1 import retain_tokens
    from copilot.daw.state_tokens import attach_tokens
    from copilot.runtime.rpc import ProjectReadView

    daw = blackboard["daw"]
    session = daw.snapshot(include_notes=False)
    retain_tokens(session)
    attach_tokens(session, path=session.project_path, name=session.project_name)
    ctx.bind_tokens(session)
    view = blackboard.get("read_view")
    if isinstance(view, ProjectReadView):
        view.bind(daw, session)
    blackboard["session"] = session
    return {
        "status": "OK",
        "project_identity": session.project_identity,
        "project_token": session.project_token,
        "track_count": len(session.tracks),
        "snapshot_source": getattr(daw, "snapshot_source", None),
    }


def cap_project_ready(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.audio.doctor_v1 import doctor
    from copilot.audio.project_ready_v1 import project_ready

    daw = blackboard["daw"]
    evidence = Path(ctx.evidence_root or blackboard["evidence"])
    doctor_report = doctor(evidence=evidence, daw=daw)
    ready = project_ready(daw, evidence=evidence)
    blackboard["ready"] = ready
    blackboard["doctor"] = doctor_report
    session = daw.snapshot(include_notes=False)
    from copilot.audio.cross_project_bootstrap_v1 import retain_tokens

    retain_tokens(session)
    ctx.bind_tokens(session)
    view = blackboard.get("read_view")
    if view is not None:
        view.bind(daw, session)
    blackboard["session"] = session
    if ready.get("PROJECT_READY") != "VERIFIED":
        raise TaskBlocked(
            str(ready.get("reason") or "PROJECT_NOT_READY"),
            {"project_ready": ready},
        )
    return ready


def cap_evidence_requirements(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.runtime.evidence import inventory_for_goals, requirements_for_goals

    request = blackboard["request"]
    graph = blackboard.get("evidence_graph")
    needed = requirements_for_goals(request.goals, graph)
    inventory = inventory_for_goals(request.goals, graph)
    blackboard["evidence_requirements"] = needed
    blackboard["evidence_inventory"] = inventory
    return {
        "goals": list(request.goals),
        "required_capabilities": needed,
        "existing": inventory["existing"],
        "valid": inventory["valid"],
        "stale": inventory["stale"],
        "missing": inventory["missing"],
        "acquisition": inventory["acquisition"],
    }


def cap_plan_observation(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.audio.producer_analyze_v1 import plan_observation

    request = blackboard["request"]
    session = blackboard["session"]
    planned = plan_observation(
        session,
        region_id=request.region_preference,
        start_qn=request.start_qn,
        end_qn=request.end_qn,
    )
    blackboard["region"] = planned["region"]
    blackboard["isolation"] = planned["isolation"]
    blackboard["clips"] = planned["clips"]
    return {
        "region": planned["region"],
        "active_count": (planned["isolation"] or {}).get("active_count"),
        "eligible_count": (planned["isolation"] or {}).get("eligible_count"),
        "pre_roll_qn": CAPTURE_PRE_ROLL_QN,
    }


def cap_capture_sources(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.audio.producer_analyze_v1 import capture_bounded_sources

    daw = blackboard["daw"]
    session = blackboard["session"]
    ready = blackboard.get("ready") or {}
    isolation = blackboard["isolation"]
    region = blackboard["region"]
    evidence = Path(ctx.evidence_root or blackboard["evidence"])
    captures = capture_bounded_sources(
        daw,
        session=session,
        ready=ready,
        isolation=isolation,
        region=region,
        evidence=evidence,
        cancellation=ctx.cancellation,
    )
    blackboard["captures"] = captures
    groups = plan_batches(list(isolation.get("bounded_targets") or []), session)
    return {
        "capture_count": len(captures),
        "batch_groups": len(groups),
        "max_batch_sources": CAPTURE_MAX_BATCH_SOURCES,
        "pre_roll_qn": CAPTURE_PRE_ROLL_QN,
        "ok": sum(1 for row in captures if row.get("ok")),
    }


def cap_fullmix(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.audio.producer_analyze_v1 import observations_from_captures

    region = blackboard["region"]
    session = blackboard["session"]
    derived = observations_from_captures(
        blackboard.get("captures") or [],
        region_id=str(region["id"]),
        start_qn=float(region["start_qn"]),
        end_qn=float(region["end_qn"]),
        tempo=float(session.transport.tempo),
    )
    blackboard["derived"] = derived
    return {"ok": bool((derived.get("fullmix") or {}).get("ok")), "main": bool(derived.get("main_capture"))}


def cap_lowend(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    derived = blackboard.get("derived") or {}
    lowend = derived.get("lowend") or {}
    return {"ok": bool(lowend.get("ok")), "present": bool(lowend)}


def cap_read_midi(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    return {"status": "SKIPPED_DEFAULT_SCOPE", "reason": "optional_goal_not_default"}


def cap_read_routing(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.runtime.rpc import read_routing_bulk

    daw = blackboard["daw"]
    session = blackboard["session"]
    indices = [track.index for track in session.tracks]
    return read_routing_bulk(daw, indices)


def cap_read_devices(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.runtime.rpc import get_devices_for_tracks

    daw = blackboard["daw"]
    session = blackboard["session"]
    indices = [track.index for track in session.tracks]
    return get_devices_for_tracks(daw, indices)


def cap_read_arrangement(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    isolation = blackboard.get("isolation") or {}
    return {
        "active_count": isolation.get("active_count"),
        "eligible_count": isolation.get("eligible_count"),
    }


def cap_evidence_fusion(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.audio.evidence_pack_v1 import build_evidence_pack, persist_evidence_pack
    from copilot.runtime.evidence import graph_from_pack

    session = blackboard["session"]
    region = blackboard["region"]
    ready = blackboard.get("ready") or {}
    isolation = blackboard.get("isolation") or {}
    captures = blackboard.get("captures") or []
    derived = blackboard.get("derived") or {}
    evidence = Path(ctx.evidence_root or blackboard["evidence"])
    built = build_evidence_pack(
        project_token=session.project_token,
        audible_token=session.audible_token,
        project_identity=session.project_identity or session.project_token,
        region_id=str(region["id"]),
        region=region,
        main_capture=derived.get("main_capture"),
        source_captures=captures,
        fullmix=derived.get("fullmix"),
        lowend=derived.get("lowend"),
        arrangement=isolation,
        routing=ready.get("capture_readiness"),
        extra_limitations=derived.get("extra_limitations") or [],
    )
    persist_evidence_pack(built, evidence)
    daw = blackboard.get("daw")
    clock = getattr(daw, "freshness", None)
    graph = graph_from_pack(
        built["pack"],
        project_identity=session.project_identity or session.project_token,
        project_state=session.project_token,
        provider="producer_analyze_v1",
        provider_version="PRODUCER_ANALYZE_V1",
        clock=clock,
    )
    graph.fuse_comparable()
    pack = built["pack"]
    blackboard["built"] = built
    blackboard["evidence_graph"] = graph
    return {"pack_id": pack["pack_id"], "evidence_view": graph.view().to_dict()}


def cap_reasoning(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.audio.producer_analyze_v1 import apply_reasoning_result
    from copilot.reasoning.provider import configured_http_provider
    from copilot.reasoning.session_astra import ASTRA_TIMEOUT_S
    from copilot.schemas.diagnosis import DiagnosisStatus

    request = blackboard["request"]
    if not request.reasoning_enabled:
        return {"status": "SKIPPED", "reason": "reasoning_disabled"}
    http = configured_http_provider()
    if http is None:
        gate = {
            "milestone_status": "BLOCKED",
            "decision": "ABSTAIN",
            "reason": "ASTRA_NOT_CONFIGURED",
            "NO WRITE": True,
            "proceed_to_write": False,
        }
        blackboard["gate"] = gate
        blackboard["status"] = "BLOCKED"
        return {"status": "BLOCKED", "gate": gate}
    try:
        from copilot.reasoning.pipeline import reason
        from copilot.schemas.evidence import EvidencePack

        pack = EvidencePack.model_validate(blackboard["built"]["pack"])
        timeout_s = ctx.deadline.timeout_or(ASTRA_TIMEOUT_S)
        graph = blackboard.get("evidence_graph")
        view = graph.view() if graph is not None and hasattr(graph, "view") else None
        if view is None:
            fusion = blackboard.get("EVIDENCE_FUSION") or {}
            raw_view = fusion.get("evidence_view")
            if isinstance(raw_view, dict):
                from copilot.runtime.evidence import EvidenceView as _EvidenceView

                view = _EvidenceView.from_dict(raw_view)
        result = reason(pack, http, view=view, timeout_s=timeout_s)
        applied = apply_reasoning_result(result)
        blackboard["diagnosis"] = applied["diagnosis"]
        blackboard["gate"] = applied["gate"]
        blackboard["status"] = applied["status"]
        blackboard["reasoning_audit"] = applied["reasoning_audit"]
        return applied
    except Exception as exc:  # noqa: BLE001
        status = DiagnosisStatus.DIAGNOSIS_UNSTABLE.value
        gate = {
            "milestone_status": status,
            "decision": "ABSTAIN",
            "reason": f"reason_failed:{exc}",
            "NO WRITE": True,
            "proceed_to_write": False,
        }
        blackboard["gate"] = gate
        blackboard["status"] = status
        return {"status": status, "gate": gate}


def cap_terminal(ctx: OperationContext, blackboard: dict[str, Any], node: GraphNode) -> dict[str, Any]:
    from copilot.audio.cross_project_bootstrap_v1 import _live_host_infos, retain_tokens
    from copilot.audio.tap_trust import inventory_taps
    from copilot.audio.terminal_state_v1 import verify_terminal_state

    daw = blackboard["daw"]
    session = daw.snapshot(include_notes=False)
    retain_tokens(session)
    if not ctx.tokens_match(session):
        terminal = {
            "ok": False,
            "failures": ["PROJECT_MISMATCH"],
            "transport_playing": session.transport.playing,
        }
    else:
        terminal = verify_terminal_state(
            transport_playing=session.transport.playing,
            taps=inventory_taps(daw),
            host_infos=_live_host_infos(daw, session),
        )
    blackboard["terminal"] = terminal
    blackboard["session"] = session
    return terminal


def build_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    rows = (
        Capability(
            "PROJECT_RESOLUTION",
            "AnalyzeProjectRequest.project",
            "ResolvedProject",
            RUNTIME,
            resources=[shared(ResourceKind.FILESYSTEM_IO)],
            execute=cap_resolve_project,
        ),
        Capability(
            "SESSION_READY",
            "ResolvedProject",
            "SessionReadyProbe",
            ABLETON,
            resources=[shared(ResourceKind.ABLETON_SESSION)],
            dependencies=("PROJECT_RESOLUTION",),
            latency_category=LatencyCategory.RPC,
            execute=cap_session_ready,
        ),
        Capability(
            "PROJECT_SNAPSHOT",
            "SessionReady",
            "SessionState",
            ABLETON,
            resources=[shared(ResourceKind.ABLETON_SESSION)],
            dependencies=("SESSION_READY",),
            latency_category=LatencyCategory.RPC,
            cache_semantics="state-token",
            execute=cap_snapshot,
        ),
        Capability(
            "PROJECT_READY",
            "SessionState",
            "ProjectReadyReport",
            ABLETON,
            resources=[shared(ResourceKind.ABLETON_SESSION)],
            dependencies=("PROJECT_SNAPSHOT",),
            latency_category=LatencyCategory.RPC,
            execute=cap_project_ready,
        ),
        Capability(
            "DETERMINE_EVIDENCE",
            "AnalyzeProjectRequest.goals",
            "EvidenceRequirements",
            RUNTIME,
            dependencies=("PROJECT_READY",),
            execute=cap_evidence_requirements,
        ),
        Capability(
            "READ_ARRANGEMENT",
            "SessionState+ALS",
            "SourceInventory",
            ALS,
            resources=[shared(ResourceKind.FILESYSTEM_IO), shared(ResourceKind.CPU_DSP)],
            dependencies=("DETERMINE_EVIDENCE",),
            execute=cap_plan_observation,
        ),
        Capability(
            "CAPTURE_MAIN",
            "Region+Hosts",
            "MainCapture",
            ABLETON,
            resources=[
                exclusive(ResourceKind.ABLETON_SESSION),
                exclusive(ResourceKind.TRANSPORT),
            ],
            dependencies=("READ_ARRANGEMENT",),
            latency_category=LatencyCategory.CAPTURE_INTRINSIC,
            execute=lambda **kwargs: {"status": "sidecar_with_sources"},
        ),
        Capability(
            "CAPTURE_SOURCES",
            "SourceRefs+Region",
            "SourceCaptures",
            ABLETON,
            resources=[
                exclusive(ResourceKind.ABLETON_SESSION),
                exclusive(ResourceKind.TRANSPORT),
                exclusive(ResourceKind.CAPTURE_HOST_POOL),
            ],
            dependencies=("CAPTURE_MAIN",),
            supports_batch=True,
            max_batch=CAPTURE_MAX_BATCH_SOURCES,
            latency_category=LatencyCategory.CAPTURE_INTRINSIC,
            execute=cap_capture_sources,
        ),
        Capability(
            "READ_MIDI",
            "ALS+Region",
            "MidiFacts",
            ALS,
            resources=[shared(ResourceKind.FILESYSTEM_IO)],
            dependencies=("READ_ARRANGEMENT",),
            execute=cap_read_midi,
        ),
        Capability(
            "READ_ROUTING",
            "ProjectReadView",
            "RoutingFacts",
            ABLETON,
            resources=[shared(ResourceKind.ABLETON_SESSION)],
            dependencies=("PROJECT_SNAPSHOT",),
            latency_category=LatencyCategory.RPC,
            execute=cap_read_routing,
        ),
        Capability(
            "READ_DEVICES",
            "ProjectReadView",
            "DeviceInventory",
            ABLETON,
            resources=[shared(ResourceKind.ABLETON_SESSION)],
            dependencies=("PROJECT_SNAPSHOT",),
            latency_category=LatencyCategory.RPC,
            execute=cap_read_devices,
        ),
        Capability(
            "FULLMIX_ANALYSIS",
            "MainCapture.wav+analyzer_version",
            "FullMixObservation",
            DSP,
            resources=[shared(ResourceKind.CPU_DSP)],
            dependencies=("CAPTURE_SOURCES",),
            cache_semantics="audio_hash+analyzer_version",
            execute=cap_fullmix,
        ),
        Capability(
            "LOWEND_ANALYSIS",
            "Main+2SourceWavs+analyzer_version",
            "LowEndFeatures",
            DSP,
            resources=[shared(ResourceKind.CPU_DSP)],
            dependencies=("CAPTURE_SOURCES",),
            cache_semantics="audio_hash+analyzer_version",
            execute=cap_lowend,
        ),
        Capability(
            "EVIDENCE_FUSION",
            "EvidenceNodes",
            "EvidencePack+EvidenceGraph",
            RUNTIME,
            dependencies=("FULLMIX_ANALYSIS", "LOWEND_ANALYSIS"),
            execute=cap_evidence_fusion,
        ),
        Capability(
            "ASTRA_REASONING",
            "EvidenceView",
            "Diagnosis",
            ASTRA,
            resources=[exclusive(ResourceKind.ASTRA_MODEL)],
            dependencies=("EVIDENCE_FUSION",),
            latency_category=LatencyCategory.MODEL,
            execute=cap_reasoning,
        ),
        Capability(
            "TERMINAL_VERIFICATION",
            "OperationContext",
            "TerminalState",
            ABLETON,
            resources=[shared(ResourceKind.ABLETON_SESSION)],
            dependencies=("ASTRA_REASONING",),
            failure_semantics=FailureSemantics.FAIL_CLOSED,
            execute=cap_terminal,
        ),
        # Future providers — registered as unavailable so the graph can name them.
        Capability(
            "MUSIC_PERCEPTION",
            "AudioRef",
            "PerceptionEmbedding",
            Provider("MusicFlamingo", available=False),
            available=False,
            resources=[exclusive(ResourceKind.MUSIC_PERCEPTION_MODEL)],
        ),
        Capability(
            "AUDIO_EMBEDDING",
            "AudioRef",
            "Embedding",
            Provider("CLAP", available=False),
            available=False,
            resources=[exclusive(ResourceKind.EMBEDDING_MODEL)],
        ),
        Capability(
            "EQ_CORRECTION",
            "MixIntent",
            "EqPlan",
            Provider("AbletonEQEight", available=False),
            available=False,
            write=True,
            read_only=False,
            resources=[exclusive(ResourceKind.PROJECT_MUTATION)],
        ),
        Capability(
            "COMPOUND_TEMPORARY_MUTATION",
            "MutationBatch (whitelisted capture-host ops)",
            "MutationBatchResult per-step",
            ABLETON,
            version="1",
            available=False,
            write=True,
            read_only=False,
            supports_batch=True,
            max_batch=CAPTURE_MAX_BATCH_SOURCES,
            failure_semantics=FailureSemantics.IN_DOUBT,
            latency_category=LatencyCategory.RPC,
            cache_semantics="handshake:compound.temporary_mutation",
        ),
    )
    for row in rows:
        registry.register(row)
    from copilot.audio.physical_dsp_v2.capability import register_physical_dsp_v2

    register_physical_dsp_v2(registry)
    return registry
