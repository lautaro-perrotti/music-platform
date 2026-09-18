"""CAPTURE_HOST_BASELINE_V1 — parked state, pool readiness, restore baseline."""

from __future__ import annotations

from pathlib import Path

from copilot.audio.capture_host_baseline_v1 import (
    CANONICAL_PARKED,
    LIFECYCLE_NOT_READY,
    evaluate_parked,
    live_default_is_invalid,
    parked_restore_baseline,
    record_host_repair,
)
from copilot.audio.capture_journal_recovery import recover_stale_capture_journal
from copilot.audio.capture_scalability_v2 import discover_capacity
from copilot.audio.source_capture_batch_v1 import (
    available_hosts,
    plan_batches,
    _require_parked_baselines,
)
from copilot.audio.tap_trust import FAILED, CaptureJournal
from copilot.audio.live_capture import AudioCaptureError
from copilot.daw.mutation_protocol import compile_restore_hosts
from copilot.schemas.session import SessionState, TrackState


def _parked_state(*, slot: int = 1) -> dict:
    return {
        "input": {"input_routing_type": "Resampling", "input_routing_channel": ""},
        "output": {"output_routing_type": "Sends Only", "output_routing_channel": ""},
        "monitoring": {"monitoring": "off"},
        "sends": [],
        "taps": [
            {
                "parameters": [
                    {"name": "Rec", "value": 0.0},
                    {"name": "Device On", "value": 1.0},
                    {"name": "Slot", "value": float(slot)},
                ]
            }
        ],
        "devices": [{"name": "Copilot Audio Tap"}],
    }


def _live_default_state() -> dict:
    return {
        "input": {"input_routing_type": "Ext. In", "input_routing_channel": ""},
        "output": {"output_routing_type": "Main", "output_routing_channel": ""},
        "monitoring": {"monitoring": "In"},
        "sends": [],
    }


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


def test_live_default_ext_in_main_is_not_pool_ready() -> None:
    state = _live_default_state()
    assert live_default_is_invalid(state) is True
    verdict = evaluate_parked(state)
    assert verdict["ok"] is False
    assert verdict["lifecycle"] == LIFECYCLE_NOT_READY
    assert "live_default" in verdict["mismatches"]
    assert "forbidden_input" in verdict["mismatches"]
    assert "forbidden_output" in verdict["mismatches"]


def test_normalize_contract_parked_state_verifies() -> None:
    verdict = evaluate_parked(_parked_state(slot=3), expected_slot=3)
    assert verdict["ok"] is True
    assert verdict["lifecycle"] == "HOST_AVAILABLE"
    assert CANONICAL_PARKED.input_routing_type == "Resampling"
    assert CANONICAL_PARKED.output_routing_type == "Sends Only"
    assert CANONICAL_PARKED.monitoring == "off"
    assert CANONICAL_PARKED.rec == 0.0


def test_leftover_source_route_is_not_parked() -> None:
    state = {
        "input": {"input_routing_type": "SC Trigger", "input_routing_channel": "Post Mixer"},
        "output": {"output_routing_type": "Sends Only", "output_routing_channel": ""},
        "monitoring": {"monitoring": "off"},
        "sends": [],
    }
    verdict = evaluate_parked(state)
    assert verdict["ok"] is False
    assert "input" in verdict["mismatches"]


def test_pool_counts_only_ready_hosts() -> None:
    session = _session(
        [
            "Copilot Capture",
            "Copilot Capture Bass",
            "Copilot Capture 3",
            "Copilot Capture 4",
        ]
    )
    ready = ["Copilot Capture", "Copilot Capture Bass", "Copilot Capture 3"]
    cap = discover_capacity(
        session=session, advertised_protocol=4, ready_hosts=ready
    )
    assert cap.available_hosts == 3
    assert cap.max_concurrent_sources == 3
    hosts = available_hosts(session, cap, ready_names=ready)
    assert [row["name"] for row in hosts] == ready
    assert plan_batches([1, 2, 3, 4], session, capacity=cap, ready_names=ready) == [
        [1, 2, 3],
        [4],
    ]


def test_four_existing_unready_hosts_are_not_capacity() -> None:
    session = _session(
        [
            "Copilot Capture",
            "Copilot Capture Bass",
            "Copilot Capture 3",
            "Copilot Capture 4",
        ]
    )
    cap = discover_capacity(session=session, advertised_protocol=4, ready_hosts=[])
    assert cap.available_hosts == 0
    assert available_hosts(session, cap, ready_names=[]) == []


def test_parked_restore_baseline_is_not_live_default() -> None:
    baseline = parked_restore_baseline()
    assert baseline["input"]["input_routing_type"] == "Resampling"
    assert baseline["output"]["output_routing_type"] == "Sends Only"
    assert baseline["monitoring"]["monitoring"] == "off"
    assert "Ext. In" not in str(baseline)
    assert "Main" not in str(baseline)
    batch = compile_restore_hosts(
        [{"index": 12, "name": "Copilot Capture 4"}],
        baselines={12: baseline},
        project_identity="pid",
    )
    inputs = [
        step.arguments.get("routing_type")
        for step in batch.steps
        if step.operation == "SET_TRACK_INPUT_ROUTING"
    ]
    outputs = [
        step.arguments.get("routing_type")
        for step in batch.steps
        if step.operation == "SET_TRACK_OUTPUT_ROUTING"
    ]
    assert inputs == ["Resampling"]
    assert outputs == ["Sends Only"]


class _HostDaw:
    def __init__(self, states: dict[int, dict]) -> None:
        self.states = states
        self.last_track_infos: dict[int, dict] = {}

    def get_tracks_info(self, indices, fresh=False):
        tracks = []
        for idx in indices:
            row = dict(self.states[int(idx)])
            row["index"] = int(idx)
            tracks.append(row)
        return {"tracks": tracks}


def test_invalid_parked_state_blocks_capture() -> None:
    daw = _HostDaw(
        {
            4: {
                "index": 4,
                "input_routing_type": "Ext. In",
                "input_routing_channel": "",
                "output_routing_type": "Main",
                "output_routing_channel": "",
                "monitoring": "In",
                "sends": [],
                "devices": [],
                "taps": [],
            }
        }
    )
    try:
        _require_parked_baselines(
            daw, [{"index": 4, "name": "Copilot Capture 4", "slot": 4}]
        )
    except AudioCaptureError as exc:
        assert exc.code == "HOST_NOT_READY"
    else:
        raise AssertionError("unparked host must fail closed")


def test_verified_parked_host_supplies_parked_baseline() -> None:
    daw = _HostDaw(
        {
            1: {
                "index": 1,
                "input_routing_type": "Resampling",
                "input_routing_channel": "",
                "output_routing_type": "Sends Only",
                "output_routing_channel": "",
                "monitoring": "off",
                "sends": [],
                "devices": [{"name": "Copilot Audio Tap"}],
                "taps": [
                    {
                        "parameters": [
                            {"name": "Rec", "value": 0.0},
                            {"name": "Device On", "value": 1.0},
                            {"name": "Slot", "value": 1.0},
                        ]
                    }
                ],
            }
        }
    )
    baselines = _require_parked_baselines(
        daw, [{"index": 1, "name": "Copilot Capture", "slot": 1}]
    )
    assert baselines[1]["input"]["input_routing_type"] == "Resampling"
    assert baselines[1]["output"]["output_routing_type"] == "Sends Only"


def test_historical_failed_journal_stays_failed(tmp_path: Path) -> None:
    journal = CaptureJournal("hist_failed", directory=tmp_path)
    journal.record(FAILED, reason="RESTORE_FAILED")
    out = recover_stale_capture_journal("hist_failed", directory=tmp_path)
    assert out["terminal"] == FAILED
    assert out["appended"] is False
    assert journal.last_status() == FAILED
    repair = record_host_repair(
        "Copilot Capture 4",
        {"verified": True, "note": "canonical parked applied"},
        directory=tmp_path / "repair",
    )
    assert repair.exists()
    assert repair != journal.path
    assert journal.last_status() == FAILED


def test_musical_writes_remain_zero_on_parked_contract() -> None:
    assert CANONICAL_PARKED.sends_silent is True
    assert "volume" not in parked_restore_baseline()
