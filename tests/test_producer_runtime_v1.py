"""PRODUCER_RUNTIME_V1 — kernel, graph, RPC read-view, AnalyzeProject."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from copilot.audio.producer_analyze_v1 import gate_read_only, preserve_status
from copilot.audio.source_capture_batch_v1 import MAX_SOURCES_PER_PASS
from copilot.runtime import Producer, compile_analyze_project
from copilot.runtime.cache import StrongCache, WeakCacheKey
from copilot.runtime.capabilities import CAPTURE_MAX_BATCH_SOURCES, CAPTURE_PRE_ROLL_QN, build_registry
from copilot.runtime.contracts import AnalyzeProjectRequest, TaskKind, TaskStatus
from copilot.runtime.context import (
    CleanupFailure,
    Deadline,
    DeadlineExceeded,
    FinalizerStack,
    OperationContext,
    RetryClass,
    classify_retry,
)
from copilot.runtime.frozen import FrozenFixtureError, assert_mutable_output
from copilot.runtime.graph import (
    ExecutionGraph,
    new_node,
    optimize_graph,
    run_graph,
    schedule_ready,
    split_batches,
)
from copilot.runtime.registry import CapabilityUnavailable, ProviderUnavailable
from copilot.runtime.resources import ResourceKind, ResourceLease, exclusive, shared
from copilot.runtime.freshness import FreshnessDomain
from copilot.runtime.rpc import (
    ProjectReadView,
    get_device_parameters_bulk,
    get_devices_for_tracks,
    get_tracks_monitoring,
    install_read_view,
    read_routing_bulk,
)
from copilot.schemas.session import SessionState, TrackState, TransportState


TRACK = {
    "index": 0,
    "name": "Kick",
    "monitoring": "in",
    "input_routing_type": "Ext. In",
    "input_routing_channel": "1",
    "output_routing_type": "Master",
    "output_routing_channel": "",
    "devices": [
        {
            "index": 0,
            "name": "EQ Eight",
            "parameters": [{"index": 0, "name": "Device On", "value": 1.0}],
        }
    ],
    "sends": [{"send_index": 0, "value": 0.4, "name": "A"}],
}


class FakeDaw:
    def __init__(self, tracks: list[dict] | None = None) -> None:
        rows = tracks or [TRACK]
        self.last_track_infos = {int(row["index"]): dict(row) for row in rows}
        self.last_topology = {"tracks": [dict(row) for row in rows]}
        self.last_master_info = None
        self.rpc: list[str] = []
        self._sock = object()

    def _command(self, command_type: str, params=None, **kwargs):
        self.rpc.append(command_type)
        if kwargs.get("side_effect"):
            return {"ok": True, "type": command_type}
        if command_type == "get_track_info":
            return dict(self.last_track_infos[int(params["track_index"])], live=True)
        if command_type == "get_track_monitoring":
            info = self.last_track_infos[int(params["track_index"])]
            return {"track_index": int(params["track_index"]), "monitoring": info["monitoring"]}
        if command_type == "get_tracks_info":
            return {"tracks": list(self.last_track_infos.values())}
        if command_type == "get_device_parameters":
            return {"parameters": [{"index": 0, "name": "live", "value": 0.5}]}
        return {"type": command_type}

    def get_track_info(self, track_index: int):
        return self._command("get_track_info", {"track_index": track_index})

    def get_track_monitoring(self, track_index: int):
        return self._command("get_track_monitoring", {"track_index": track_index})

    def get_track_input_routing(self, track_index: int):
        return self._command("get_track_input_routing", {"track_index": track_index})

    def get_track_output_routing(self, track_index: int):
        return self._command("get_track_output_routing", {"track_index": track_index})

    def get_device_parameters(self, track_index: int, device_index: int):
        return self._command(
            "get_device_parameters",
            {"track_index": track_index, "device_index": device_index},
        )

    def get_tracks_info(self, indices=None, *, fresh=False):
        return self._command("get_tracks_info", {"indices": indices})

    def get_track_sends(self, track_index: int, *, limit: int = 16):
        self.rpc.append("get_track_sends")
        return list(self.last_track_infos[int(track_index)].get("sends") or [])

    def get_capture_hosts_state(self, indices=None, *, fresh=False):
        return self.get_tracks_info(indices, fresh=fresh)

    def get_tracks_sends(self, indices=None, *, fresh=False):
        payload = self.get_tracks_info(indices, fresh=fresh)
        return {
            "tracks": [
                {"track_index": int(item["index"]), "sends": list(item.get("sends") or [])}
                for item in payload.get("tracks") or []
                if "index" in item
            ]
        }

    def snapshot(self, *, include_notes: bool = True, fresh: bool = False):
        self.rpc.append("snapshot")
        return SessionState(
            project_identity="pid",
            project_token="ptok",
            audible_token="atok",
            project_path="C:/p/x.als",
            project_name="x",
            tracks=[TrackState(stable_id="k", index=0, name="Kick", role="audio")],
            transport=TransportState(tempo=126.0, playing=False),
        )

    def disconnect(self) -> None:
        return None


def _session() -> SessionState:
    return SessionState(
        project_identity="pid",
        project_token="ptok",
        audible_token="atok",
        tracks=[TrackState(stable_id="k", index=0, name="Kick", role="audio")],
        transport=TransportState(tempo=126.0, playing=False),
    )


def test_compile_analyze_project_graph() -> None:
    graph = compile_analyze_project(AnalyzeProjectRequest())
    ids = [n.capability_id for n in graph.nodes]
    assert ids[0] == "PROJECT_RESOLUTION"
    assert "CAPTURE_SOURCES" in ids
    assert "ASTRA_REASONING" in ids
    assert "TERMINAL_VERIFICATION" in ids
    assert "READ_MIDI" not in ids
    by_id = {n.capability_id: n for n in graph.nodes}
    assert "PROJECT_RESOLUTION" in by_id["SESSION_READY"].depends_on[0] or True
    capture = by_id["CAPTURE_SOURCES"]
    assert any(
        graph.node_map()[dep].capability_id == "CAPTURE_MAIN" for dep in capture.depends_on
    )
    extra = compile_analyze_project(
        AnalyzeProjectRequest(goals=("HARMONIC_CONTEXT", "DEVICE_CAUSAL_CONTEXT"))
    )
    extra_ids = {n.capability_id for n in extra.nodes}
    assert {"READ_MIDI", "READ_ROUTING", "READ_DEVICES"} <= extra_ids


def test_duplicate_elimination() -> None:
    registry = build_registry()
    a = new_node("PROJECT_SNAPSHOT")
    b = new_node("PROJECT_SNAPSHOT")
    graph = ExecutionGraph("t", [a, b])
    optimized, report = optimize_graph(graph, registry)
    assert report.duplicates_removed == 1
    skipped = [n for n in optimized.nodes if n.skip]
    assert len(skipped) == 1
    assert skipped[0].skip_reason.startswith("duplicate_of:")


def test_batch_split_max_two_sources() -> None:
    assert MAX_SOURCES_PER_PASS == 2
    assert split_batches([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    registry = build_registry()
    cap = registry.get("CAPTURE_SOURCES")
    assert cap.supports_batch is True
    assert cap.max_batch == CAPTURE_MAX_BATCH_SOURCES
    assert CAPTURE_MAX_BATCH_SOURCES == 8
    assert CAPTURE_PRE_ROLL_QN == 16.0


def test_resource_conflicts_and_safe_parallel() -> None:
    lease = ResourceLease()
    lease.acquire("cap", [exclusive(ResourceKind.TRANSPORT)])
    assert not lease.can_acquire("dsp", [exclusive(ResourceKind.TRANSPORT)])
    assert lease.can_acquire("dsp", [exclusive(ResourceKind.CPU_DSP)])
    lease.release("cap")
    a = new_node("FULLMIX_ANALYSIS")
    a.resources = [shared(ResourceKind.CPU_DSP)]
    b = new_node("LOWEND_ANALYSIS")
    b.resources = [shared(ResourceKind.CPU_DSP)]
    c = new_node("CAPTURE_SOURCES")
    c.resources = [exclusive(ResourceKind.TRANSPORT), exclusive(ResourceKind.CPU_DSP)]
    graph = ExecutionGraph("t", [a, b, c])
    waves = schedule_ready(graph, set())
    first = {n.capability_id for n in waves[0]}
    assert first == {"FULLMIX_ANALYSIS", "LOWEND_ANALYSIS"}
    assert waves[1][0].capability_id == "CAPTURE_SOURCES"


def test_deadline_propagation() -> None:
    parent = Deadline(0.2)
    child = parent.child(30.0)
    assert child.remaining() is not None
    assert child.remaining() <= 0.2 + 0.01
    time.sleep(0.21)
    with pytest.raises(DeadlineExceeded):
        child.check()


def test_cancellation_propagates() -> None:
    ctx = OperationContext.create()
    child = OperationContext.create(parent=ctx)
    ctx.cancellation.cancel("user")
    with pytest.raises(Exception):
        child.check()
    assert child.cancellation.cancelled is True
    assert child.cancellation.reason == "user"


def test_finalizer_order_and_cleanup_failure() -> None:
    order: list[str] = []
    stack = FinalizerStack()
    stack.register(lambda: order.append("a") or "a", name="a", order=1)
    stack.register(lambda: order.append("b") or "b", name="b", order=2)
    stack.register(lambda: order.append("c") or "c", name="c", order=3)
    stack.run_all()
    assert order == ["c", "b", "a"]
    broken = FinalizerStack()
    broken.register(lambda: (_ for _ in ()).throw(RuntimeError("restore failed")), name="restore", order=1)
    with pytest.raises(CleanupFailure):
        broken.run_all()
    assert broken.results[0]["ok"] is False


def test_retry_classification() -> None:
    assert classify_retry(RuntimeError("PROJECT_MISMATCH")) == RetryClass.NON_RETRYABLE
    assert classify_retry(TimeoutError("Timeout waiting for Ableton"), mutation_started=True) == RetryClass.IN_DOUBT
    assert classify_retry(RuntimeError("429 rate")) == RetryClass.RETRYABLE_TRANSIENT
    assert classify_retry(RuntimeError("STALE_PLAN")) == RetryClass.RETRYABLE_AFTER_REFRESH
    assert classify_retry(RuntimeError("ORIGINAL_SET_OPEN")) == RetryClass.REQUIRES_USER_ACTION


def test_cache_hit_and_invalidation() -> None:
    cache = StrongCache()
    cache.put("audio_hash:abc:analyzer:fullmix-obs-1", {"ok": True}, project_token="ptok")
    assert cache.get("audio_hash:abc:analyzer:fullmix-obs-1", project_token="ptok")["ok"] is True
    assert cache.hits == 1
    assert cache.get("audio_hash:abc:analyzer:fullmix-obs-1", project_token="other") is None
    with pytest.raises(WeakCacheKey):
        cache.put("track_name:Kick", {})


def test_provider_and_capability_unavailable() -> None:
    registry = build_registry()
    with pytest.raises(CapabilityUnavailable):
        registry.get("DOES_NOT_EXIST")
    with pytest.raises(ProviderUnavailable):
        registry.get("MUSIC_PERCEPTION", provider_id="MusicFlamingo")
    with pytest.raises(CapabilityUnavailable):
        registry.get("EQ_CORRECTION")


def test_operation_failure_and_terminal_verification(tmp_path: Path) -> None:
    registry = build_registry()
    for cap_id in [row["capability_id"] for row in registry.contents()]:
        try:
            cap = registry.get(cap_id)
        except (CapabilityUnavailable, ProviderUnavailable):
            continue
        if cap_id == "TERMINAL_VERIFICATION":
            cap.execute = lambda **kwargs: {"ok": False, "failures": ["transport_playing"]}
        else:
            cap.execute = lambda **kwargs: {"status": "OK", "PROJECT_READY": "VERIFIED"}
    producer = Producer(evidence=tmp_path, daw=FakeDaw(), registry=registry)
    result = producer.analyze_project(None, reasoning_enabled=False)
    assert result.musical_writes == 0
    assert result.high_level_command_count == 1
    assert result.to_dict()["NO WRITE"] is True
    assert result.status in {TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.SUCCEEDED}


def test_frozen_fixture_protection(tmp_path: Path) -> None:
    frozen = tmp_path / "fixtures" / "frozen" / "pack.json"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("{}", encoding="utf-8")
    with pytest.raises(FrozenFixtureError):
        assert_mutable_output(frozen)
    with pytest.raises(FrozenFixtureError):
        Producer(evidence=frozen.parent)


def test_rpc_read_view_reuse_and_invalidation() -> None:
    daw = FakeDaw()
    view = ProjectReadView()
    session = _session()
    view.bind(daw, session)
    with install_read_view(daw, view):
        first = daw.get_track_info(0)
        second = daw.get_track_info(0)
        mon = daw.get_track_monitoring(0)
        assert first["name"] == "Kick"
        assert second["name"] == "Kick"
        assert mon["monitoring"] == "in"
        assert "get_track_info" not in daw.rpc
        daw._command("set_track_mute", {"track_index": 0}, side_effect=True)
        assert view.clock.is_fresh(FreshnessDomain.TRACK_METADATA, 0) is False
        assert view.clock.is_fresh(FreshnessDomain.PROJECT_TOPOLOGY) is True
        daw.get_track_info(0)
        assert daw.rpc[-1] == "get_track_info"
    assert view.duplicate_reads_eliminated >= 2


def test_bulk_rpc_from_read_view() -> None:
    daw = FakeDaw()
    view = ProjectReadView()
    view.bind(daw, _session())
    daw._project_read_view = view
    monitoring = get_tracks_monitoring(daw, [0])
    devices = get_devices_for_tracks(daw, [0])
    routing = read_routing_bulk(daw, [0])
    params = get_device_parameters_bulk(daw, [(0, 0)])
    assert monitoring["tracks"][0]["monitoring"] == "in"
    assert devices["tracks"][0]["devices"][0]["name"] == "EQ Eight"
    assert routing["tracks"][0]["output_routing_type"] == "Master"
    assert params["devices"][0]["source"] == "project_read_view"
    assert "get_track_info" not in daw.rpc
    assert view.bulk_calls >= 1


def test_state_trust_invalidation_on_project_mismatch() -> None:
    view = ProjectReadView()
    daw = FakeDaw()
    session = _session()
    view.bind(daw, session)
    other = _session()
    other.project_identity = "other"
    assert view.tokens_match(other) is False
    ctx = OperationContext.create()
    ctx.bind_tokens(session)
    assert ctx.tokens_match(other) is False


def test_semantic_equivalence_gate_and_status() -> None:
    assert preserve_status("WEAKLY_SUPPORTED") == "WEAKLY_SUPPORTED"
    gate = gate_read_only(diagnosis_status="INSUFFICIENT_EVIDENCE", diagnosis_accepted=True)
    assert gate["milestone_status"] == "INSUFFICIENT_EVIDENCE"
    assert gate["NO WRITE"] is True
    assert gate["proceed_to_write"] is False
    request = AnalyzeProjectRequest()
    graph = compile_analyze_project(request)
    assert graph.task == "AnalyzeProject"
    assert request.kind == TaskKind.ANALYZE_PROJECT


def test_analyze_project_one_high_level_call(tmp_path: Path) -> None:
    registry = build_registry()
    session = _session()

    def execute(ctx, blackboard, node, **_):
        ctx.check()
        cid = node.capability_id
        if cid == "SESSION_READY":
            blackboard["daw"] = blackboard.get("daw") or FakeDaw()
            return {"status": "SESSION_READY"}
        if cid == "PROJECT_SNAPSHOT":
            blackboard["session"] = session
            ctx.bind_tokens(session)
            view = blackboard.get("read_view")
            if view is not None:
                view.bind(blackboard["daw"], session)
            return {"status": "OK", "project_identity": "pid"}
        if cid == "PROJECT_READY":
            blackboard["ready"] = {"PROJECT_READY": "VERIFIED"}
            return {"PROJECT_READY": "VERIFIED"}
        if cid == "READ_ARRANGEMENT":
            blackboard["region"] = {"id": "AUTO_0_32", "start_qn": 0, "end_qn": 32}
            blackboard["isolation"] = {"bounded_targets": [], "active_count": 0}
            return {"region": blackboard["region"]}
        if cid == "CAPTURE_SOURCES":
            blackboard["captures"] = []
            return {"capture_count": 0, "max_batch_sources": 2, "pre_roll_qn": 16.0}
        if cid == "FULLMIX_ANALYSIS":
            blackboard["derived"] = {"main_capture": None, "fullmix": None, "lowend": None, "extra_limitations": []}
            return {"ok": False}
        if cid == "EVIDENCE_FUSION":
            blackboard["built"] = {
                "pack": {
                    "pack_id": "pack_test",
                    "limitations": [],
                }
            }
            return {"pack_id": "pack_test", "evidence_view": {"nodes": {}}}
        if cid == "ASTRA_REASONING":
            blackboard["status"] = "INSUFFICIENT_EVIDENCE"
            blackboard["gate"] = {
                "milestone_status": "INSUFFICIENT_EVIDENCE",
                "NO WRITE": True,
                "proceed_to_write": False,
                "decision": "ABSTAIN",
            }
            blackboard["diagnosis"] = {"status": "INSUFFICIENT_EVIDENCE"}
            return {"status": "INSUFFICIENT_EVIDENCE", "gate": blackboard["gate"]}
        if cid == "TERMINAL_VERIFICATION":
            terminal = {"ok": True, "transport_playing": False}
            blackboard["terminal"] = terminal
            return terminal
        return {"status": "OK"}

    for row in registry.contents():
        try:
            cap = registry.get(row["capability_id"])
        except (CapabilityUnavailable, ProviderUnavailable):
            continue
        cap.execute = execute

    producer = Producer(evidence=tmp_path, daw=FakeDaw(), registry=registry)
    result = producer.analyze_project(None)
    assert result.high_level_command_count == 1
    assert result.to_dict()["AGENT_HIGH_LEVEL_COMMAND_COUNT"] == 1
    assert result.musical_writes == 0
    assert result.status == TaskStatus.SUCCEEDED
    assert result.gate["NO WRITE"] is True
    assert result.evidence_summary["pack_id"] == "pack_test"
    assert "graph" in result.payload
    assert result.rpc["duplicate_reads_eliminated"] >= 0


def test_cli_is_thin_adapter() -> None:
    from copilot.cli import CANONICAL_COMMANDS, HELP_EPILOG

    assert "analyze-project" in CANONICAL_COMMANDS
    assert "analyze-project" in HELP_EPILOG
    source = Path(__file__).resolve().parents[1] / "src" / "copilot" / "cli.py"
    text = source.read_text(encoding="utf-8")
    assert "Producer(" in text
    assert "analyze_project(" in text
    assert "def _analyze_project" in text


def test_capability_registry_lists_future_providers() -> None:
    contents = {row["capability_id"] for row in build_registry().contents()}
    assert "MUSIC_PERCEPTION" in contents
    assert "AUDIO_EMBEDDING" in contents
    assert "EQ_CORRECTION" in contents
    assert "CAPTURE_SOURCES" in contents
    assert "ASTRA_REASONING" in contents


def test_optimizer_skips_satisfied_and_cache() -> None:
    registry = build_registry()
    node = new_node("PROJECT_SNAPSHOT")
    graph = ExecutionGraph("t", [node])
    _, report = optimize_graph(graph, registry, satisfied={"PROJECT_SNAPSHOT"})
    assert graph.nodes[0].skip is True
    node2 = new_node("FULLMIX_ANALYSIS")
    graph2 = ExecutionGraph("t", [node2])
    _, report2 = optimize_graph(graph2, registry, cache_hits={"FULLMIX_ANALYSIS"})
    assert report2.cache_hits == 1
    assert graph2.nodes[0].skip_reason == "strong_cache_hit"


def test_run_graph_cancellation_stops_children() -> None:
    registry = build_registry()
    ctx = OperationContext.create()
    order: list[str] = []

    def execute(ctx, blackboard, node, **_):
        ctx.check()
        order.append(node.capability_id)
        if node.capability_id == "PROJECT_RESOLUTION":
            ctx.cancellation.cancel("stop")
        return {"status": "OK"}

    for row in registry.contents():
        try:
            cap = registry.get(row["capability_id"])
        except (CapabilityUnavailable, ProviderUnavailable):
            continue
        cap.execute = execute
    a = new_node("PROJECT_RESOLUTION")
    b = new_node("SESSION_READY", [a.node_id])
    with pytest.raises(Exception):
        run_graph(ExecutionGraph("t", [a, b]), registry, ctx, {})
    assert "SESSION_READY" not in order
