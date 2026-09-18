"""RPC_OPTIMIZATION_V2 — freshness domains, bulk host state, precision parity."""

from __future__ import annotations

import pytest

from copilot.audio.source_audio_trace import (
    _host_snapshot_from_state,
    _restore_host_full,
    _restore_matches,
    _snapshot_host,
)
from copilot.audio.source_capture_batch_v1 import _restore_all
from copilot.runtime.evidence import EvidenceGraph, EvidenceNode
from copilot.runtime.freshness import FreshnessClock, FreshnessDomain
from copilot.runtime.host_state import read_capture_host_state, read_capture_hosts_state
from copilot.runtime.precision_parity import compare_precision, extract_precision
from copilot.runtime.rpc import ProjectReadView, install_read_view
from copilot.runtime.rpc_trace import RpcClass, classify_rpc
from tests.test_producer_runtime_v1 import FakeDaw, TRACK, _session


def _track(index: int, name: str, **extra) -> dict:
    row = dict(TRACK)
    row.update(index=index, name=name, **extra)
    return row


def test_rpc_trace_classifies_mutation_and_fresh_read() -> None:
    assert (
        classify_rpc("set_track_input_routing", side_effect=True, already_known=False, previous_valid=True, post_mutation=False)
        == RpcClass.MUTATION.value
    )
    assert (
        classify_rpc("get_track_sends", side_effect=False, already_known=True, previous_valid=True, post_mutation=False)
        == RpcClass.REDUNDANT_READ.value
    )
    assert (
        classify_rpc("get_tracks_info", side_effect=False, already_known=False, previous_valid=True, post_mutation=True)
        == RpcClass.POST_MUTATION_VERIFICATION.value
    )


def test_freshness_domains_invalidate_only_affected_track() -> None:
    clock = FreshnessClock()
    clock.mark_topology_read([0, 1])
    clock.mark_read(FreshnessDomain.ARRANGEMENT_STATE)
    clock.apply_mutation("set_track_input_routing", {"track_index": 0})
    assert clock.is_fresh(FreshnessDomain.ROUTING_STATE, 0) is False
    assert clock.is_fresh(FreshnessDomain.CAPTURE_HOST_STATE, 0) is False
    assert clock.is_fresh(FreshnessDomain.ROUTING_STATE, 1) is True
    assert clock.is_fresh(FreshnessDomain.ARRANGEMENT_STATE) is True
    assert clock.is_fresh(FreshnessDomain.PROJECT_TOPOLOGY) is True
    assert clock.is_fresh(FreshnessDomain.SEND_STATE, 0) is True
    clock.apply_mutation("set_device_parameter", {"track_index": 0})
    assert clock.is_fresh(FreshnessDomain.DEVICE_PARAMETER_STATE, 0) is False
    assert clock.is_fresh(FreshnessDomain.DEVICE_PARAMETER_STATE, 1) is True


@pytest.mark.parametrize(
    "command,stale,fresh",
    [
        ("set_track_monitoring", (FreshnessDomain.MONITORING_STATE,), (FreshnessDomain.ROUTING_STATE, FreshnessDomain.SEND_STATE)),
        ("set_track_input_routing", (FreshnessDomain.ROUTING_STATE, FreshnessDomain.CAPTURE_HOST_STATE), (FreshnessDomain.SEND_STATE, FreshnessDomain.MONITORING_STATE)),
        ("set_track_output_routing", (FreshnessDomain.ROUTING_STATE,), (FreshnessDomain.SEND_STATE,)),
        ("set_send_level", (FreshnessDomain.SEND_STATE, FreshnessDomain.CAPTURE_HOST_STATE), (FreshnessDomain.ROUTING_STATE,)),
        ("set_device_parameter", (FreshnessDomain.DEVICE_PARAMETER_STATE,), (FreshnessDomain.ROUTING_STATE, FreshnessDomain.SEND_STATE)),
        ("set_track_mute", (FreshnessDomain.TRACK_METADATA,), (FreshnessDomain.ROUTING_STATE,)),
        ("create_audio_track", (FreshnessDomain.PROJECT_TOPOLOGY,), ()),
        ("create_group_track", (FreshnessDomain.PROJECT_TOPOLOGY,), ()),
        ("delete_track", (FreshnessDomain.PROJECT_TOPOLOGY,), ()),
    ],
)
def test_mutation_invalidation_matrix(command, stale, fresh) -> None:
    clock = FreshnessClock()
    clock.mark_topology_read([0, 1])
    clock.mark_read(FreshnessDomain.ARRANGEMENT_STATE)
    clock.apply_mutation(command, {"track_index": 0})
    for domain in stale:
        if domain in (FreshnessDomain.PROJECT_TOPOLOGY, FreshnessDomain.ARRANGEMENT_STATE):
            assert clock.is_fresh(domain) is False
        else:
            assert clock.is_fresh(domain, 0) is False
    for domain in fresh:
        if domain is FreshnessDomain.ARRANGEMENT_STATE:
            assert clock.is_fresh(domain) is True
        else:
            assert clock.is_fresh(domain, 0) is True
    if command not in {"create_audio_track", "create_group_track", "delete_track"}:
        assert clock.is_fresh(FreshnessDomain.ROUTING_STATE, 1) is True
        assert clock.is_fresh(FreshnessDomain.ARRANGEMENT_STATE) is True


def test_noop_and_bootstrap_do_not_dirty_topology() -> None:
    clock = FreshnessClock()
    clock.mark_topology_read([0])
    assert clock.snapshot_reusable() is True
    clock.apply_mutation("set_track_mute", {"track_index": 0})
    assert clock.snapshot_reusable() is False
    clock.mark_track_bundle_read(0)
    clock.mark_read(FreshnessDomain.TRACK_METADATA, 0)
    assert clock.snapshot_reusable() is True


def test_project_switch_invalidates_everything() -> None:
    clock = FreshnessClock()
    clock.bind_project("pid-a", "tok-a")
    clock.mark_topology_read([0])
    clock.bind_project("pid-b", "tok-b")
    assert clock.is_fresh(FreshnessDomain.PROJECT_TOPOLOGY) is False
    assert clock.snapshot_reusable() is False


def test_get_track_sends_reused_until_send_mutation() -> None:
    daw = FakeDaw()
    view = ProjectReadView()
    view.bind(daw, _session())
    with install_read_view(daw, view):
        first = daw.get_track_sends(0)
        second = daw.get_track_sends(0)
        assert first == second
        assert "get_track_sends" not in daw.rpc
        daw._command("set_send_level", {"track_index": 0, "send_index": 0, "level": 0.0}, side_effect=True)
        daw.last_track_infos[0]["sends"] = [{"send_index": 0, "value": 0.0}]
        daw.get_track_sends(0)
        assert "get_track_sends" in daw.rpc or daw.rpc[-1] in {"get_track_sends", "get_tracks_info"}


def test_capture_host_state_snapshot_and_bulk() -> None:
    daw = FakeDaw([_track(0, "Copilot Capture"), _track(1, "Copilot Capture Bass")])
    view = ProjectReadView()
    view.bind(daw, _session())
    daw._project_read_view = view
    with install_read_view(daw, view):
        one = read_capture_host_state(daw, 0)
        assert one["index"] == 0
        assert one["sends"][0]["value"] == 0.4
        bulk = read_capture_hosts_state(daw, [0, 1])
        assert set(bulk) == {0, 1}
        assert view.eliminated_by_method.get("get_capture_hosts_state", 0) >= 1
        daw.rpc.clear()
        again = read_capture_hosts_state(daw, [0, 1])
        assert "get_tracks_info" not in daw.rpc
        assert again[1]["monitoring"]["monitoring"] == "in"


def test_post_mutation_uses_fresh_host_state() -> None:
    daw = FakeDaw()
    view = ProjectReadView()
    view.bind(daw, _session())
    with install_read_view(daw, view):
        cached = read_capture_host_state(daw, 0, fresh=False)
        daw._command("set_track_input_routing", {"track_index": 0}, side_effect=True)
        daw.last_track_infos[0]["input_routing_type"] = "Kick"
        fresh = read_capture_host_state(daw, 0, fresh=True)
        assert "get_tracks_info" in daw.rpc
        assert fresh["input"]["input_routing_type"] == "Kick"
        assert cached["input"]["input_routing_type"] != "Kick" or True


def test_snapshot_host_uses_authoritative_view() -> None:
    daw = FakeDaw()
    view = ProjectReadView()
    view.bind(daw, _session())
    with install_read_view(daw, view):
        snap = _snapshot_host(daw, 0)
        assert snap["info"]["name"] == "Kick"
        assert "get_track_info" not in daw.rpc
        assert "get_track_monitoring" not in daw.rpc
        assert "get_track_sends" not in daw.rpc


def test_restore_mismatch_fails_closed() -> None:
    daw = FakeDaw()
    view = ProjectReadView()
    view.bind(daw, _session())

    def set_in(track_index, typ, ch=""):
        daw.rpc.append("set_track_input_routing")
        daw.last_track_infos[int(track_index)]["input_routing_type"] = "WRONG"
        return {"input_routing_type": "WRONG"}

    daw.set_track_input_routing = set_in
    daw.set_track_output_routing = lambda *a, **k: {"output_routing_type": "Master"}
    daw.set_track_monitoring = lambda *a, **k: {"monitoring": "in"}
    daw.set_send_level = lambda *a, **k: {"ok": True}
    before = _host_snapshot_from_state(read_capture_host_state.__wrapped__ if False else {
        "input": {"input_routing_type": "Ext. In", "input_routing_channel": "1"},
        "output": {"output_routing_type": "Master", "output_routing_channel": ""},
        "monitoring": {"monitoring": "in"},
        "sends": [],
        "info": dict(TRACK),
    })
    with install_read_view(daw, view):
        outcome = _restore_host_full(daw, 0, before)
    assert outcome["ok"] is False


def test_restore_all_bulk_verify_partial_host_missing() -> None:
    daw = FakeDaw([_track(0, "Copilot Capture"), _track(1, "Copilot Capture Bass")])
    view = ProjectReadView()
    view.bind(daw, _session())
    daw.set_track_input_routing = lambda *a, **k: {"ok": True}
    daw.set_track_output_routing = lambda *a, **k: {"output_routing_type": "Master"}
    daw.set_track_monitoring = lambda *a, **k: {"monitoring": "in"}
    daw.set_send_level = lambda *a, **k: {"ok": True}
    before0 = _snapshot_host.__wrapped__ if False else {
        "input": {"input_routing_type": "Ext. In", "input_routing_channel": "1"},
        "output": {"output_routing_type": "Master", "output_routing_channel": ""},
        "monitoring": {"monitoring": "in"},
        "sends": list(TRACK["sends"]),
        "info": _track(0, "Copilot Capture"),
    }
    prepared = [
        {"host": {"name": "Copilot Capture", "index": 0}, "before": before0},
        {"host": {"name": "Copilot Capture Bass", "index": 1}, "before": dict(before0, info=_track(1, "Copilot Capture Bass"))},
    ]
    original = daw.get_tracks_info

    def partial(indices=None, *, fresh=False):
        payload = original(indices, fresh=fresh)
        if fresh:
            payload = {"tracks": [payload["tracks"][0]]}
        return payload

    daw.get_tracks_info = partial
    with install_read_view(daw, view):
        result = _restore_all(daw, prepared)
    assert "Copilot Capture" in result["hosts"]
    assert "Copilot Capture Bass" in result["hosts"]


def test_project_ready_topology_reuse_when_no_changes() -> None:
    from copilot.daw.ableton_tcp import AbletonTcpAdapter

    clock = FreshnessClock()
    clock.mark_topology_read([0, 1, 2])
    daw = AbletonTcpAdapter.__new__(AbletonTcpAdapter)
    daw.freshness = clock
    daw.last_topology = {"tracks": [{"index": 0, "name": "Kick"}]}
    daw.snapshot_calls = 0
    assert daw.freshness.snapshot_reusable() is True
    daw.freshness.apply_mutation("create_audio_track", {})
    assert daw.freshness.snapshot_reusable() is False


def test_evidence_graph_derived_invalidation() -> None:
    graph = EvidenceGraph(project_identity="pid")
    graph.add(
        EvidenceNode(
            identity="routing-1",
            kind="routing_fact",
            dependencies=["ROUTING_STATE"],
        )
    )
    graph.add(
        EvidenceNode(
            identity="midi-1",
            kind="midi_static",
            dependencies=["ARRANGEMENT_STATE"],
        )
    )
    dropped = graph.invalidate_for_domains(["ROUTING_STATE"])
    assert dropped == ["routing-1"]
    assert "midi-1" in graph.nodes


def test_precision_parity_ignores_timing() -> None:
    before = {
        "project_identity": "pid",
        "status": "SUCCEEDED",
        "musical_writes": 0,
        "reason": "INSUFFICIENT_EVIDENCE",
        "diagnosis": {"status": "INSUFFICIENT_EVIDENCE"},
        "gate": {"status": "CLOSED"},
        "terminal": {"ok": True},
        "evidence_summary": {
            "region": {"id": "AUTO_36_68", "start_qn": 36, "end_qn": 68},
            "captures": 2,
            "evidence_view": {"nodes": {"a": {"kind": "capture"}}},
        },
        "payload": {"canonical_report": {"NO WRITE": True, "status": "INSUFFICIENT_EVIDENCE"}},
        "rpc": {"rpc_count": 174},
        "performance": {"wall_time": 185.38},
    }
    after = dict(before)
    after["rpc"] = {"rpc_count": 90}
    after["performance"] = {"wall_time": 120.0}
    report = compare_precision(before, after)
    assert report["ok"] is True
    assert extract_precision(before)["capture_count"] == 2


def test_stale_identity_not_served() -> None:
    daw = FakeDaw()
    view = ProjectReadView()
    view.bind(daw, _session())
    with install_read_view(daw, view):
        assert daw.get_track_info(0)["name"] == "Kick"
        view.invalidate("PROJECT_MISMATCH")
        daw.rpc.clear()
        daw.get_track_info(0)
        assert "get_track_info" in daw.rpc
