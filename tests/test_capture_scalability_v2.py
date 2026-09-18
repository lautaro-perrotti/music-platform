"""CAPTURE_SCALABILITY_V2 — capacity discovery and pass planning."""

from __future__ import annotations

from copilot.audio.capture_scalability_v2 import (
    LEGACY_SOURCE_CAPACITY,
    PRACTICAL_MAX_SOURCE_SLOTS,
    SCALABLE_TAP_PROTOCOL,
    CaptureCapacity,
    capture_identity,
    discover_capacity,
    host_name_for_slot,
    is_capture_host_name,
    plan_source_passes,
    staging_for_slot,
)
from copilot.audio.source_capture_batch_v1 import (
    MAX_SOURCES_PER_PASS,
    available_hosts,
    plan_batches,
)
from copilot.daw.mutation_protocol import CAPTURE_HOST_LIMIT, compile_prepare_hosts
from copilot.runtime.resources import ResourceKind
from copilot.schemas.session import SessionState, TrackState


def _session(names: list[str]) -> SessionState:
    return SessionState(
        tracks=[
            TrackState(stable_id=f"t{i}", index=i, name=name, role="audio")
            for i, name in enumerate(names)
        ],
        project_path="C:/p/x.als",
        project_name="x",
        project_identity="pid",
    )


def test_legacy_protocol_capacity_is_two() -> None:
    cap = discover_capacity(advertised_protocol=3)
    assert cap.max_concurrent_sources == LEGACY_SOURCE_CAPACITY == 2
    assert MAX_SOURCES_PER_PASS == 2


def test_protocol_four_discovers_eight_source_slots() -> None:
    cap = discover_capacity(advertised_protocol=SCALABLE_TAP_PROTOCOL)
    assert cap.max_concurrent_sources == PRACTICAL_MAX_SOURCE_SLOTS == 8
    assert cap.protocol_version == 4


def test_inventory_protocol_is_authoritative() -> None:
    cap = discover_capacity(
        advertised_protocol=3,
        inventory=[{"tap_protocol": 4, "slot": 3, "track_index": 12}],
    )
    assert cap.protocol_version == 4
    assert cap.max_concurrent_sources == 8


def test_four_sources_one_pass_when_capacity_four() -> None:
    cap = CaptureCapacity(
        max_concurrent_sources=4,
        available_hosts=4,
        occupied_hosts=0,
        main_sidecar_supported=True,
        protocol_version=4,
        max_slot=8,
    )
    assert plan_source_passes([1, 2, 3, 4], capacity=cap) == [[1, 2, 3, 4]]


def test_five_sources_capacity_four_splits_4_plus_1() -> None:
    cap = CaptureCapacity(
        max_concurrent_sources=4,
        available_hosts=4,
        occupied_hosts=0,
        main_sidecar_supported=True,
        protocol_version=4,
        max_slot=8,
    )
    assert plan_source_passes([1, 2, 3, 4, 5], capacity=cap) == [[1, 2, 3, 4], [5]]


def test_available_hosts_without_capacity_returns_all_provisioned() -> None:
    session = _session(
        [
            "Copilot Capture",
            "Copilot Capture Bass",
            "Copilot Capture 3",
            "Copilot Capture 4",
        ]
    )
    hosts = available_hosts(session)
    assert [row["slot"] for row in hosts] == [1, 2, 3, 4]


def test_ready_names_without_capacity_does_not_legacy_slice() -> None:
    session = _session(
        [
            "Copilot Capture",
            "Copilot Capture Bass",
            "Copilot Capture 3",
            "Copilot Capture 4",
        ]
    )
    ready = [
        "Copilot Capture",
        "Copilot Capture Bass",
        "Copilot Capture 3",
        "Copilot Capture 4",
    ]
    hosts = available_hosts(session, ready_names=ready)
    assert [row["slot"] for row in hosts] == [1, 2, 3, 4]


def test_four_provisioned_hosts_ready_names_filter() -> None:
    session = _session(
        [
            "Copilot Capture",
            "Copilot Capture Bass",
            "Copilot Capture 3",
            "Copilot Capture 4",
        ]
    )
    ready = ["Copilot Capture", "Copilot Capture Bass", "Copilot Capture 3"]
    cap = discover_capacity(session=session, advertised_protocol=4, ready_hosts=ready)
    assert cap.available_hosts == 3
    assert [row["name"] for row in available_hosts(session, cap, ready_names=ready)] == ready


def test_legacy_fallback_capacity_two() -> None:
    session = _session(
        ["Kick", "Copilot Capture", "Copilot Capture Bass"]
    )
    assert plan_batches([1, 2, 3, 4], session) == [[1, 2], [3, 4]]


def test_host_identity_and_staging_are_collision_proof() -> None:
    assert host_name_for_slot(1) == "Copilot Capture"
    assert host_name_for_slot(2) == "Copilot Capture Bass"
    assert host_name_for_slot(3) == "Copilot Capture 3"
    assert is_capture_host_name("Copilot Capture 4")
    assert not is_capture_host_name("Copilot Capture Extra")
    names = [staging_for_slot(slot) for slot in range(0, 9)]
    assert len(names) == len(set(names))
    ident = capture_identity(
        operation_id="op1",
        source_id="srcA",
        host_id="Copilot Capture 3",
        slot=3,
        pass_id="pass1",
    )
    assert ident["shared_transport_pass"] is True
    assert "slot3" in ident["artifact_name"]


def test_compile_prepare_accepts_four_hosts() -> None:
    hosts = [
        {
            "index": i,
            "name": host_name_for_slot(i),
            "host_id": host_name_for_slot(i),
            "target_name": f"S{i}",
        }
        for i in range(1, 5)
    ]
    batch = compile_prepare_hosts(
        hosts,
        inventory=[],
        baselines={},
        project_identity="pid",
    )
    assert len(batch.steps) >= 4
    assert CAPTURE_HOST_LIMIT == 8


def test_capture_host_pool_resource_exists() -> None:
    assert ResourceKind.CAPTURE_HOST_POOL.value == "CAPTURE_HOST_POOL"
