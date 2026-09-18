"""ABLETON_MUTATION_PROTOCOL_V1 — compound capture-host mutations without safety loss."""

from __future__ import annotations

import inspect

import pytest

from copilot.audio import source_capture_batch_v1 as batch
from copilot.daw.mutation_protocol import (
    COMPOUND_CAPABILITY,
    COMPOUND_MUTATION_VERSION,
    MUTATION_INVENTORY,
    WHITELIST,
    BatchStatus,
    MutationBatch,
    MutationProtocolError,
    MutationStep,
    StepStatus,
    compile_prepare_hosts,
    compile_restore_hosts,
    compound_available,
    execute_mutation_plan,
    journal_fields,
)
from copilot.daw.protocol import COMMAND_CAPABILITY
from copilot.daw.write import WriteInDoubt
from copilot.runtime.capabilities import build_registry
from copilot.schemas.session import SessionState, TrackState


def _session(identity: str = "pid") -> SessionState:
    return SessionState(
        tracks=[TrackState(stable_id="t0", index=0, name="Kick", role="audio")],
        project_path="C:/p/x.als",
        project_name="x",
        project_identity=identity,
        project_token=identity,
    )


def _host(index: int, name: str, target: str) -> dict:
    return {
        "index": index,
        "name": name,
        "host_id": name,
        "target_name": target,
        "target": target,
        "slot": 1 if index == 9 else 2,
    }


def _inventory(*hosts: tuple[int, int]) -> list[dict]:
    rows = []
    for track_index, device_index in hosts:
        rows.append(
            {
                "track_index": track_index,
                "device_index": device_index,
                "device_on_param_index": 0,
                "rec_param_index": 1,
            }
        )
    return rows


def _baseline(index: int, *, send: float = 0.4) -> dict:
    return {
        "input": {"input_routing_type": "Ext. In", "input_routing_channel": "1"},
        "output": {"output_routing_type": "Master", "output_routing_channel": ""},
        "monitoring": {"monitoring": "auto"},
        "sends": [{"send_index": 0, "value": send}],
    }


class SequentialDaw:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.capabilities: set[str] = set()
        self.fail_on: str | None = None
        self.doubt_on: str | None = None

    def set_track_monitoring(self, track_index, monitoring):
        self.calls.append(("set_track_monitoring", (track_index, monitoring)))
        if self.fail_on == "set_track_monitoring":
            raise RuntimeError("monitoring failed")
        if self.doubt_on == "set_track_monitoring":
            raise WriteInDoubt("set_track_monitoring", "req_x")
        return {"track_index": track_index, "monitoring": monitoring}

    def set_send_level(self, track_index, send_index, level):
        self.calls.append(("set_send_level", (track_index, send_index, level)))
        return {"track_index": track_index, "send_index": send_index, "level": level}

    def set_device_parameter(self, track_index, device_index, parameter_index, value):
        self.calls.append(
            ("set_device_parameter", (track_index, device_index, parameter_index, value))
        )
        return {"value": value}

    def get_available_inputs(self, track_index):
        self.calls.append(("get_available_inputs", (track_index,)))
        return {
            "available_inputs": ["Kick", "Bass"],
            "available_input_channels": ["Post Mixer", "Pre FX"],
        }

    def set_track_input_routing(self, track_index, routing_type, routing_channel=""):
        self.calls.append(
            ("set_track_input_routing", (track_index, routing_type, routing_channel))
        )
        if self.fail_on == "set_track_input_routing" and routing_channel:
            raise RuntimeError("host 2 routing failed")
        return {
            "track_index": track_index,
            "input_routing_type": routing_type,
            "input_routing_channel": routing_channel,
        }

    def get_available_outputs(self, track_index):
        self.calls.append(("get_available_outputs", (track_index,)))
        return {"available_outputs": ["No Output", "Master"]}

    def set_track_output_routing(self, track_index, routing_type, routing_channel=""):
        self.calls.append(
            ("set_track_output_routing", (track_index, routing_type, routing_channel))
        )
        return {"output_routing_type": routing_type}

    def get_track_input_routing(self, track_index):
        return {"input_routing_type": "Kick", "input_routing_channel": "Post Mixer"}


class CompoundDaw(SequentialDaw):
    def __init__(self) -> None:
        super().__init__()
        self.capabilities = {COMPOUND_CAPABILITY, "session.read", "track.mute"}
        self.compound_mutation_version = COMPOUND_MUTATION_VERSION
        self.batches: list[dict] = []
        self.fail_step: str | None = None
        self.mismatch = False
        self.drop = False

    def execute_mutation_batch(self, payload):
        self.batches.append(payload)
        self.calls.append(("execute_mutation_batch", (payload.get("batch_id"),)))
        if self.drop:
            raise WriteInDoubt("execute_mutation_batch", "req_batch")
        if self.mismatch:
            return {
                "batch_id": payload.get("batch_id"),
                "batch_status": BatchStatus.FAILED_BEFORE_EXECUTION.value,
                "error": "PROJECT_MISMATCH",
                "step_results": [],
            }
        results = []
        stop = False
        for step in payload.get("steps") or []:
            status = "NOT_ATTEMPTED"
            error = None
            if stop and not step.get("independent"):
                error = "not_attempted_after_failure"
            elif self.fail_step and step.get("step_id") == self.fail_step:
                status = "FAILED"
                error = "injected"
                stop = True
            else:
                status = "APPLIED"
            results.append(
                {
                    "step_id": step.get("step_id"),
                    "status": status,
                    "error": error,
                    "observed": {"ok": True} if status == "APPLIED" else None,
                }
            )
        failed = any(row["status"] == "FAILED" for row in results)
        return {
            "batch_id": payload.get("batch_id"),
            "project_identity": payload.get("project_identity"),
            "batch_status": (
                BatchStatus.PARTIAL_FAILURE.value if failed else BatchStatus.COMPLETE.value
            ),
            "step_results": results,
        }


def _ready(batch: MutationBatch) -> MutationBatch:
    batch.prestate_persisted = True
    batch.journals_prepared = True
    return batch


def test_inventory_covers_capture_host_writes() -> None:
    purposes = {row["purpose"] for row in MUTATION_INVENTORY}
    assert purposes >= {"TAP_PARAMETER", "ROUTING", "MONITORING", "OUTPUT", "RESTORE"}
    assert WHITELIST == {
        "SET_TRACK_MONITORING",
        "SET_TRACK_INPUT_ROUTING",
        "SET_TRACK_OUTPUT_ROUTING",
        "SET_DEVICE_PARAMETER",
        "SET_SEND_LEVEL",
    }
    assert "execute_mutation_batch" in COMMAND_CAPABILITY
    assert COMMAND_CAPABILITY["execute_mutation_batch"] == COMPOUND_CAPABILITY


def test_compound_not_inferred_from_ableton_version() -> None:
    class Live12:
        capabilities = set()
        ableton_version = "12.1"

    assert compound_available(Live12()) is False
    assert compound_available(None) is False


def test_whitelist_rejects_arbitrary_execution() -> None:
    step = MutationStep(
        step_id="s000",
        operation="SET_TRACK_VOLUME",
        target_ref={"track_index": 0},
        arguments={"track_index": 0, "volume": 0.5},
    )
    with pytest.raises(MutationProtocolError, match="UNWHITELISTED"):
        step.to_wire()
    lom = MutationStep(
        step_id="s000",
        operation="SET_TRACK_MONITORING",
        target_ref={"track_index": 0},
        arguments={"track_index": 0, "monitoring": "in", "eval": "track.mute"},
    )
    batch = MutationBatch(
        batch_id="b",
        project_identity="pid",
        expected_state_tokens={},
        steps=[lom],
    )
    with pytest.raises(MutationProtocolError, match="ARBITRARY"):
        batch.to_wire()


def test_durable_prestate_required_before_any_write() -> None:
    daw = SequentialDaw()
    plan = compile_prepare_hosts(
        [_host(9, "Copilot Capture", "Kick")],
        inventory=_inventory((9, 0)),
        baselines={9: _baseline(9)},
        project_identity="pid",
    )
    result = execute_mutation_plan(daw, plan, session=_session())
    assert result.batch_status is BatchStatus.FAILED_BEFORE_EXECUTION
    assert result.error == "DURABLE_PRESTATE_MISSING"
    assert daw.calls == []
    assert all(row.status is StepStatus.NOT_ATTEMPTED for row in result.step_results)


def test_project_mismatch_fails_closed() -> None:
    daw = SequentialDaw()
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick")],
            inventory=_inventory((9, 0)),
            baselines={9: _baseline(9)},
            project_identity="pid",
        )
    )
    result = execute_mutation_plan(daw, plan, session=_session("other"))
    assert result.batch_status is BatchStatus.FAILED_BEFORE_EXECUTION
    assert result.error == "PROJECT_MISMATCH"
    assert daw.calls == []


def test_never_returns_one_boolean_for_multistep() -> None:
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick")],
            inventory=_inventory((9, 0)),
            baselines={9: _baseline(9)},
            project_identity="pid",
        )
    )
    result = execute_mutation_plan(SequentialDaw(), plan, session=_session())
    payload = result.to_dict()
    assert payload["batch_status"] == "COMPLETE"
    assert "success" not in payload
    assert len(payload["step_results"]) >= 4
    assert {row["status"] for row in payload["step_results"]} <= {"APPLIED", "StepStatus.APPLIED"}
    assert all(str(row["status"]).endswith("APPLIED") for row in payload["step_results"])


def test_two_host_prepare_compiles_ordered_steps() -> None:
    plan = compile_prepare_hosts(
        [_host(9, "Copilot Capture", "Kick"), _host(10, "Copilot Capture Bass", "Bass")],
        inventory=_inventory((9, 0), (10, 0)),
        baselines={9: _baseline(9), 10: _baseline(10)},
        project_identity="pid",
    )
    ops = [step.operation for step in plan.steps]
    assert ops.count("SET_TRACK_INPUT_ROUTING") == 2
    assert ops.count("SET_TRACK_MONITORING") == 2
    assert ops.count("SET_TRACK_OUTPUT_ROUTING") == 2
    assert plan.kind == "TEMPORARY_CAPTURE_HOST"
    three = compile_prepare_hosts(
        [_host(1, "A", "a"), _host(2, "B", "b"), _host(3, "C", "c")],
        inventory=[],
        baselines={},
        project_identity="pid",
    )
    assert len(three.steps) >= 3
    with pytest.raises(MutationProtocolError, match="CAPTURE_HOST_LIMIT"):
        compile_prepare_hosts(
            [_host(i, f"H{i}", "x") for i in range(9)],
            inventory=[],
            baselines={},
            project_identity="pid",
        )


def test_partial_failure_stops_dependent_later_steps() -> None:
    daw = SequentialDaw()
    daw.fail_on = "set_track_input_routing"
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick"), _host(10, "Copilot Capture Bass", "Bass")],
            inventory=_inventory((9, 0), (10, 0)),
            baselines={9: _baseline(9, send=0.0), 10: _baseline(10, send=0.0)},
            project_identity="pid",
        )
    )
    result = execute_mutation_plan(daw, plan, session=_session())
    assert result.batch_status is BatchStatus.PARTIAL_FAILURE
    applied_hosts = {row.host_id for row in result.step_results if row.status is StepStatus.APPLIED}
    not_attempted = [row for row in result.step_results if row.status is StepStatus.NOT_ATTEMPTED]
    failed = [row for row in result.step_results if row.status is StepStatus.FAILED]
    assert failed
    assert "Copilot Capture" in applied_hosts
    assert any(row.host_id == "Copilot Capture Bass" for row in not_attempted)
    assert result.hosts_untouched()


def test_in_doubt_does_not_resend() -> None:
    daw = SequentialDaw()
    daw.doubt_on = "set_track_monitoring"
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick")],
            inventory=_inventory((9, 0)),
            baselines={9: _baseline(9, send=0.0)},
            project_identity="pid",
        )
    )
    result = execute_mutation_plan(daw, plan, session=_session())
    assert result.batch_status is BatchStatus.IN_DOUBT
    assert any(row.status is StepStatus.UNKNOWN for row in result.step_results)
    with pytest.raises(MutationProtocolError, match="BLIND_RESEND"):
        execute_mutation_plan(daw, plan, session=_session(), allow_resend=True)


def test_cancel_before_dispatch_mutates_nothing() -> None:
    daw = SequentialDaw()
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick")],
            inventory=_inventory((9, 0)),
            baselines={9: _baseline(9)},
            project_identity="pid",
        )
    )
    result = execute_mutation_plan(daw, plan, session=_session(), cancelled=True)
    assert result.batch_status is BatchStatus.FAILED_BEFORE_EXECUTION
    assert result.error == "CANCELLED_BEFORE_DISPATCH"
    assert daw.calls == []


def test_compound_path_is_one_rpc() -> None:
    daw = CompoundDaw()
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick"), _host(10, "Copilot Capture Bass", "Bass")],
            inventory=_inventory((9, 0), (10, 0)),
            baselines={9: _baseline(9), 10: _baseline(10)},
            project_identity="pid",
        )
    )
    result = execute_mutation_plan(daw, plan, session=_session())
    assert result.transport == "compound"
    assert result.batch_status is BatchStatus.COMPLETE
    assert [call[0] for call in daw.calls] == ["execute_mutation_batch"]
    assert len(daw.batches[0]["steps"]) == len(plan.steps)


def test_compound_partial_failure_and_in_doubt() -> None:
    daw = CompoundDaw()
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick"), _host(10, "Copilot Capture Bass", "Bass")],
            inventory=_inventory((9, 0), (10, 0)),
            baselines={9: _baseline(9, send=0.0), 10: _baseline(10, send=0.0)},
            project_identity="pid",
        )
    )
    host2_first = next(step.step_id for step in plan.steps if step.host_id == "Copilot Capture Bass")
    daw.fail_step = host2_first
    partial = execute_mutation_plan(daw, plan, session=_session())
    assert partial.batch_status is BatchStatus.PARTIAL_FAILURE
    assert partial.applied_step_ids()
    assert partial.failed_step_ids() == [host2_first]
    assert partial.not_attempted_step_ids()
    daw.fail_step = None
    daw.drop = True
    doubt = execute_mutation_plan(daw, plan, session=_session())
    assert doubt.batch_status is BatchStatus.IN_DOUBT
    assert doubt.resent is False


def test_compound_project_mismatch_from_remote() -> None:
    daw = CompoundDaw()
    daw.mismatch = True
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick")],
            inventory=_inventory((9, 0)),
            baselines={9: _baseline(9)},
            project_identity="pid",
        )
    )
    result = execute_mutation_plan(daw, plan, session=_session())
    assert result.batch_status is BatchStatus.FAILED_BEFORE_EXECUTION
    assert result.error == "PROJECT_MISMATCH"


def test_restore_marks_later_host_independent() -> None:
    plan = compile_restore_hosts(
        [_host(9, "Copilot Capture", "Kick"), _host(10, "Copilot Capture Bass", "Bass")],
        baselines={9: _baseline(9), 10: _baseline(10)},
        project_identity="pid",
    )
    host2 = [step for step in plan.steps if step.host_id == "Copilot Capture Bass"]
    assert host2[0].independent is True
    assert all(step.independent is False for step in host2[1:])
    daw = SequentialDaw()
    daw.fail_on = "set_track_monitoring"
    result = execute_mutation_plan(daw, _ready(plan), session=_session())
    host2_results = [row for row in result.step_results if row.host_id == "Copilot Capture Bass"]
    assert any(row.status is StepStatus.APPLIED for row in host2_results)


def test_per_host_journals_keep_step_ids() -> None:
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick"), _host(10, "Copilot Capture Bass", "Bass")],
            inventory=_inventory((9, 0), (10, 0)),
            baselines={9: _baseline(9, send=0.0), 10: _baseline(10, send=0.0)},
            project_identity="pid",
        )
    )
    result = execute_mutation_plan(CompoundDaw(), plan, session=_session())
    kick = journal_fields(result, "Copilot Capture")
    bass = journal_fields(result, "Copilot Capture Bass")
    assert kick["batch_id"] == bass["batch_id"]
    assert kick["step_ids"]
    assert set(kick["step_ids"]).isdisjoint(set(bass["step_ids"]))


def test_sequential_fallback_when_capability_missing() -> None:
    daw = SequentialDaw()
    plan = _ready(
        compile_prepare_hosts(
            [_host(9, "Copilot Capture", "Kick")],
            inventory=_inventory((9, 0)),
            baselines={9: _baseline(9, send=0.0)},
            project_identity="pid",
        )
    )
    result = execute_mutation_plan(daw, plan, session=_session())
    assert result.transport == "sequential"
    assert "execute_mutation_batch" not in [call[0] for call in daw.calls]
    assert "set_device_parameter" in [call[0] for call in daw.calls]


def test_capability_registry_exposes_compound_gated_false() -> None:
    rows = {row["capability_id"]: row for row in build_registry().contents()}
    cap = rows["COMPOUND_TEMPORARY_MUTATION"]
    assert cap["available"] is False
    assert cap["version"] == "1"
    assert cap["supports_batch"] is True
    assert cap["cache_semantics"] == "handshake:compound.temporary_mutation"


def test_capture_batch_uses_protocol_when_advertised() -> None:
    source = inspect.getsource(batch.capture_sources_post_mixer_batch)
    assert "compound_available" in source
    assert "_apply_compound_prepare" in source
    restore = inspect.getsource(batch._restore_all)
    assert "_restore_all_compound" in restore


def test_mock_server_can_advertise_compound() -> None:
    from copilot.daw.mock_tcp_server import MockRemoteScriptServer

    server = MockRemoteScriptServer()
    server.advertise_compound = True
    hello = server._dispatch("protocol_hello", {})
    assert COMPOUND_CAPABILITY in hello["capabilities"]
    assert hello["compound_mutation_version"] == "1"
    payload = {
        "batch_id": "b1",
        "project_identity": "pid",
        "steps": [
            {
                "step_id": "s000",
                "operation": "SET_TRACK_MONITORING",
                "independent": False,
                "arguments": {"track_index": 0, "monitoring": "in"},
            },
            {
                "step_id": "s001",
                "operation": "SET_TRACK_MONITORING",
                "independent": False,
                "arguments": {"track_index": 1, "monitoring": "in"},
            },
        ],
    }
    server.fail_mutation_step = "s000"
    result = server._dispatch("execute_mutation_batch", payload)
    assert result["batch_status"] == "PARTIAL_FAILURE"
    assert result["step_results"][0]["status"] == "FAILED"
    assert result["step_results"][1]["status"] == "NOT_ATTEMPTED"
