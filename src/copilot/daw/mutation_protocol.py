"""ABLETON_MUTATION_PROTOCOL_V1 — compound capture-host mutations.

Reduce Live scheduler round trips. Do not reduce observability.

V1 is only for temporary capture/observation host state. Musical EQ, MIDI,
automation and arrangement writes are a separate certification track.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Callable
from uuid import uuid4

from copilot.daw.adapter import DawError
from copilot.daw.write import WriteInDoubt

MILESTONE = "ABLETON_MUTATION_PROTOCOL_V1"
COMPOUND_TEMPORARY_MUTATION = "compound.temporary_mutation"
COMPOUND_CAPABILITY = COMPOUND_TEMPORARY_MUTATION
COMPOUND_MUTATION_VERSION = "1"
KIND_TEMPORARY_CAPTURE_HOST = "TEMPORARY_CAPTURE_HOST"


class StepStatus(StrEnum):
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    UNKNOWN = "UNKNOWN"


class BatchStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED_BEFORE_EXECUTION = "FAILED_BEFORE_EXECUTION"
    IN_DOUBT = "IN_DOUBT"


STATUS_APPLIED = StepStatus.APPLIED
STATUS_FAILED = StepStatus.FAILED
STATUS_NOT_ATTEMPTED = StepStatus.NOT_ATTEMPTED
STATUS_UNKNOWN = StepStatus.UNKNOWN

BATCH_COMPLETE = BatchStatus.COMPLETE
BATCH_PARTIAL = BatchStatus.PARTIAL_FAILURE
BATCH_FAILED_BEFORE = BatchStatus.FAILED_BEFORE_EXECUTION
BATCH_IN_DOUBT = BatchStatus.IN_DOUBT

TRANSPORT_COMPOUND = "compound"
TRANSPORT_SEQUENTIAL = "sequential"

OP_SET_TRACK_MONITORING = "SET_TRACK_MONITORING"
OP_SET_TRACK_INPUT_ROUTING = "SET_TRACK_INPUT_ROUTING"
OP_SET_TRACK_OUTPUT_ROUTING = "SET_TRACK_OUTPUT_ROUTING"
OP_SET_DEVICE_PARAMETER = "SET_DEVICE_PARAMETER"
OP_SET_SEND_LEVEL = "SET_SEND_LEVEL"

WHITELISTED_OPERATIONS = frozenset(
    {
        OP_SET_TRACK_MONITORING,
        OP_SET_TRACK_INPUT_ROUTING,
        OP_SET_TRACK_OUTPUT_ROUTING,
        OP_SET_DEVICE_PARAMETER,
        OP_SET_SEND_LEVEL,
    }
)
WHITELIST = WHITELISTED_OPERATIONS
FORBIDDEN_ARGUMENT_KEYS = frozenset(
    {"lom", "eval", "python", "shell", "expression", "method", "property_path"}
)

# V2 capture pool may prepare more than two hosts; Main is still not a target.
CAPTURE_HOST_LIMIT = 8

# Phase 1 inventory — temporary capture-host writes only. Do not mutate.
MUTATION_INVENTORY_V1 = (
    {"purpose": "TAP_PARAMETER", "operation": OP_SET_DEVICE_PARAMETER, "phase": "PREPARE"},
    {"purpose": "TAP_PARAMETER", "operation": OP_SET_DEVICE_PARAMETER, "phase": "DISARM"},
    {"purpose": "ROUTING", "operation": OP_SET_TRACK_INPUT_ROUTING, "phase": "PREPARE"},
    {"purpose": "MONITORING", "operation": OP_SET_TRACK_MONITORING, "phase": "PREPARE"},
    {"purpose": "OUTPUT", "operation": OP_SET_TRACK_OUTPUT_ROUTING, "phase": "PREPARE"},
    {"purpose": "ROUTING", "operation": OP_SET_SEND_LEVEL, "phase": "PREPARE"},
    {"purpose": "RESTORE", "operation": OP_SET_TRACK_INPUT_ROUTING, "phase": "RESTORE"},
    {"purpose": "RESTORE", "operation": OP_SET_TRACK_OUTPUT_ROUTING, "phase": "RESTORE"},
    {"purpose": "RESTORE", "operation": OP_SET_TRACK_MONITORING, "phase": "RESTORE"},
    {"purpose": "RESTORE", "operation": OP_SET_SEND_LEVEL, "phase": "RESTORE"},
)
MUTATION_INVENTORY = MUTATION_INVENTORY_V1


class MutationProtocolError(DawError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


PREFERRED_INPUT_CHANNEL = "Post Mixer"
OUTPUT_OFF_CANDIDATES = ("No Output", "Sends Only")


@dataclass
class MutationStep:
    step_id: str
    operation: str
    target_ref: dict[str, Any]
    arguments: dict[str, Any]
    purpose: str = ""
    independent: bool = False
    host_id: str = ""

    def to_wire(self) -> dict[str, Any]:
        if self.operation not in WHITELISTED_OPERATIONS:
            raise MutationProtocolError(
                "UNWHITELISTED_OPERATION",
                f"{self.operation} is not in the temporary-observation vocabulary",
            )
        return {
            "step_id": self.step_id,
            "operation": self.operation,
            "target_ref": dict(self.target_ref),
            "arguments": dict(self.arguments),
            "purpose": self.purpose,
            "independent": bool(self.independent),
            "host_id": self.host_id,
        }


@dataclass
class MutationBatch:
    batch_id: str
    project_identity: str
    expected_state_tokens: dict[str, Any]
    steps: list[MutationStep]
    prestate_persisted: bool = False
    journals_prepared: bool = False
    kind: str = KIND_TEMPORARY_CAPTURE_HOST
    expected_project_path: str = ""
    expected_project_name: str = ""

    def to_wire(self) -> dict[str, Any]:
        for step in self.steps:
            bad = FORBIDDEN_ARGUMENT_KEYS.intersection(step.arguments)
            if bad:
                raise MutationProtocolError(
                    "ARBITRARY_EXECUTION_FORBIDDEN",
                    f"step {step.step_id} contains forbidden keys {sorted(bad)}",
                )
        return {
            "batch_id": self.batch_id,
            "project_identity": self.project_identity,
            "expected_state_tokens": dict(self.expected_state_tokens),
            "expected_project_path": self.expected_project_path,
            "expected_project_name": self.expected_project_name,
            "kind": self.kind,
            "steps": [step.to_wire() for step in self.steps],
        }


def _as_step_status(value: Any) -> StepStatus:
    try:
        return StepStatus(str(value))
    except ValueError:
        return StepStatus.UNKNOWN


def _as_batch_status(value: Any) -> BatchStatus:
    try:
        return BatchStatus(str(value))
    except ValueError:
        return BatchStatus.IN_DOUBT


@dataclass
class MutationStepResult:
    step_id: str
    target: dict[str, Any]
    operation: str
    requested_value: Any
    status: StepStatus
    observed: Any = None
    error: str | None = None
    host_id: str = ""
    purpose: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = str(self.status)
        return payload


@dataclass
class MutationBatchResult:
    batch_id: str
    project_identity: str
    step_results: list[MutationStepResult]
    batch_status: BatchStatus
    transport: str
    error: str | None = None
    replaced_rpc_count: int = 0
    mutation_rpc_count: int = 0
    resent: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["batch_status"] = str(self.batch_status)
        payload["step_results"] = [row.to_dict() for row in self.step_results]
        return payload

    def failed_step_ids(self) -> list[str]:
        return [row.step_id for row in self.step_results if row.status is StepStatus.FAILED]

    def not_attempted_step_ids(self) -> list[str]:
        return [row.step_id for row in self.step_results if row.status is StepStatus.NOT_ATTEMPTED]

    def applied_step_ids(self) -> list[str]:
        return [row.step_id for row in self.step_results if row.status is StepStatus.APPLIED]

    def hosts_untouched(self) -> set[str]:
        return self.untouched_host_ids()

    def applied_host_ids(self) -> set[str]:
        return {
            str(row.host_id)
            for row in self.step_results
            if row.status is STATUS_APPLIED and row.host_id
        }

    def failed_host_ids(self) -> set[str]:
        return {
            str(row.host_id)
            for row in self.step_results
            if row.status is STATUS_FAILED and row.host_id
        }

    def untouched_host_ids(self) -> set[str]:
        applied = self.applied_host_ids()
        hosts = {str(row.host_id) for row in self.step_results if row.host_id}
        return {host for host in hosts if host not in applied}


def journal_fields(result: MutationBatchResult, host_id: str) -> dict[str, Any]:
    """Per-host journal extras. Compound transport must not collapse source journals."""
    steps = [row for row in result.step_results if str(row.host_id) == str(host_id)]
    return {
        "batch_id": result.batch_id,
        "step_ids": [row.step_id for row in steps],
        "host_id": host_id,
        "mutation_result": [row.to_dict() for row in steps],
        "mutation_transport": result.transport,
        "batch_status": str(result.batch_status),
    }


def compound_available(daw: Any) -> bool:
    if daw is None:
        return False
    caps = getattr(daw, "capabilities", None) or set()
    if COMPOUND_TEMPORARY_MUTATION not in caps:
        return False
    version = str(getattr(daw, "compound_mutation_version", "") or COMPOUND_MUTATION_VERSION)
    return version == COMPOUND_MUTATION_VERSION


def compound_capability_record(*, advertised: bool = False) -> dict[str, Any]:
    return {
        "id": COMPOUND_TEMPORARY_MUTATION.upper().replace(".", "_"),
        "available": bool(advertised),
        "version": COMPOUND_MUTATION_VERSION,
        "supported_operations": sorted(WHITELISTED_OPERATIONS),
        "kind": KIND_TEMPORARY_CAPTURE_HOST,
    }


def new_batch_id() -> str:
    return f"mut_{uuid4().hex[:12]}"


def _not_attempted(step: MutationStep, error: str | None = None) -> MutationStepResult:
    return MutationStepResult(
        step_id=step.step_id,
        target=dict(step.target_ref),
        operation=step.operation,
        requested_value=dict(step.arguments),
        status=STATUS_NOT_ATTEMPTED,
        error=error,
        host_id=step.host_id,
        purpose=step.purpose,
    )


def _failed_before(
    batch: MutationBatch, error: str, transport: str = TRANSPORT_SEQUENTIAL
) -> MutationBatchResult:
    return MutationBatchResult(
        batch_id=batch.batch_id,
        project_identity=batch.project_identity,
        step_results=[_not_attempted(step, error) for step in batch.steps],
        batch_status=BATCH_FAILED_BEFORE,
        transport=transport,
        error=error,
        replaced_rpc_count=len(batch.steps),
        mutation_rpc_count=0,
    )


def _normalize_path(value: str) -> str:
    return str(value or "").replace("\\", "/").rstrip("/").lower()


def project_identity_mismatch(
    batch: MutationBatch, session: Any | None
) -> str | None:
    if session is None:
        return None
    expected = str(batch.project_identity or "")
    actual = str(
        getattr(session, "project_identity", None)
        or getattr(session, "project_token", None)
        or ""
    )
    if expected and actual and expected != actual:
        return "PROJECT_MISMATCH"
    expected_path = _normalize_path(batch.expected_project_path)
    actual_path = _normalize_path(str(getattr(session, "project_path", "") or ""))
    if expected_path and actual_path and expected_path != actual_path:
        return "PROJECT_MISMATCH"
    expected_name = str(batch.expected_project_name or "").strip().lower()
    actual_name = str(getattr(session, "project_name", "") or "").strip().lower()
    if expected_name and actual_name and expected_name != actual_name:
        return "PROJECT_MISMATCH"
    return None


def _validate_batch(batch: MutationBatch) -> str | None:
    if batch.kind != KIND_TEMPORARY_CAPTURE_HOST:
        return "MUSICAL_MUTATION_FORBIDDEN"
    if not batch.prestate_persisted:
        return "DURABLE_PRESTATE_MISSING"
    if not batch.journals_prepared:
        return "DURABLE_PRESTATE_MISSING"
    for step in batch.steps:
        if step.operation not in WHITELISTED_OPERATIONS:
            return f"OPERATION_NOT_WHITELISTED:{step.operation}"
        if FORBIDDEN_ARGUMENT_KEYS.intersection(step.arguments):
            return "ARBITRARY_EXECUTION_FORBIDDEN"
        if "eval" in step.operation.lower() or "lom" in step.operation.lower():
            return "ARBITRARY_EXECUTION_FORBIDDEN"
    return None


def _classify_batch(results: list[MutationStepResult], *, in_doubt: bool = False) -> BatchStatus:
    if in_doubt:
        return BATCH_IN_DOUBT
    if not results:
        return BATCH_COMPLETE
    if any(row.status is STATUS_UNKNOWN for row in results):
        return BATCH_IN_DOUBT
    if all(row.status is STATUS_APPLIED for row in results):
        return BATCH_COMPLETE
    if all(row.status is STATUS_NOT_ATTEMPTED for row in results):
        return BATCH_FAILED_BEFORE
    return BATCH_PARTIAL


def run_ordered_steps(
    steps: list[MutationStep],
    apply_fn: Callable[[MutationStep], Any],
    *,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[MutationStepResult], bool]:
    """Execute steps in order. Core, not Remote Script, owns rollback."""
    results: list[MutationStepResult] = []
    stop = False
    in_doubt = False
    for step in steps:
        if (not stop) and _is_cancelled(cancelled):
            stop = True
        if stop and not step.independent:
            reason = "IN_DOUBT" if in_doubt else "STOPPED_AFTER_FAILURE"
            if _is_cancelled(cancelled):
                reason = "CANCELLED"
            results.append(_not_attempted(step, reason))
            continue
        try:
            observed = apply_fn(step)
        except WriteInDoubt as exc:
            in_doubt = True
            stop = True
            results.append(
                MutationStepResult(
                    step_id=step.step_id,
                    target=dict(step.target_ref),
                    operation=step.operation,
                    requested_value=dict(step.arguments),
                    status=STATUS_UNKNOWN,
                    error=str(exc),
                    host_id=step.host_id,
                    purpose=step.purpose,
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 — per-step truth, do not swallow batch
            stop = True
            results.append(
                MutationStepResult(
                    step_id=step.step_id,
                    target=dict(step.target_ref),
                    operation=step.operation,
                    requested_value=dict(step.arguments),
                    status=STATUS_FAILED,
                    error=str(exc),
                    host_id=step.host_id,
                    purpose=step.purpose,
                )
            )
            continue
        results.append(
            MutationStepResult(
                step_id=step.step_id,
                target=dict(step.target_ref),
                operation=step.operation,
                requested_value=dict(step.arguments),
                status=STATUS_APPLIED,
                observed=observed,
                host_id=step.host_id,
                purpose=step.purpose,
            )
        )
    return results, in_doubt


def _pick_name(available: list[Any], *needles: str) -> str | None:
    names = [str(item) for item in available]
    for needle in needles:
        wanted = needle.lower()
        for name in names:
            if name.lower() == wanted:
                return name
        for name in names:
            if wanted in name.lower():
                return name
    return None


def _apply_sequential_step(daw: Any, step: MutationStep) -> Any:
    args = step.arguments
    track_index = int(args.get("track_index", (step.target_ref or {}).get("track_index") or 0))
    if step.operation == OP_SET_TRACK_MONITORING:
        return daw.set_track_monitoring(track_index, str(args.get("monitoring") or "in"))
    if step.operation == OP_SET_TRACK_INPUT_ROUTING:
        routing_type = str(args.get("routing_type") or "")
        routing_channel = str(args.get("routing_channel") or "")
        target_name = str(args.get("target_name") or "")
        preferred = str(args.get("preferred_channel") or PREFERRED_INPUT_CHANNEL)
        if not routing_type and target_name:
            available = list(
                (daw.get_available_inputs(track_index) or {}).get("available_inputs") or []
            )
            picked = _pick_name(available, target_name)
            if not picked:
                raise DawError(f"no Audio From type matching {target_name!r}")
            daw.set_track_input_routing(track_index, picked, "")
            after = daw.get_available_inputs(track_index) or {}
            channel = _pick_name(
                list(after.get("available_input_channels") or []), preferred, "Post Mixer"
            )
            if channel:
                return daw.set_track_input_routing(track_index, picked, channel)
            return daw.get_track_input_routing(track_index)
        if not routing_type:
            raise DawError(
                "sequential SET_TRACK_INPUT_ROUTING requires routing_type or target_name"
            )
        return daw.set_track_input_routing(track_index, routing_type, routing_channel)
    if step.operation == OP_SET_TRACK_OUTPUT_ROUTING:
        routing_type = str(args.get("routing_type") or "")
        if not routing_type:
            candidates = list(args.get("routing_candidates") or [])
            last_error = DawError("no output routing_type")
            for candidate in candidates:
                try:
                    result = daw.set_track_output_routing(track_index, str(candidate), "")
                    observed = str((result or {}).get("output_routing_type") or candidate)
                    if observed.lower() not in {"main", "master"}:
                        return result
                except DawError as exc:
                    last_error = exc
            raise last_error
        return daw.set_track_output_routing(
            track_index,
            routing_type,
            str(args.get("routing_channel") or ""),
        )
    if step.operation == OP_SET_DEVICE_PARAMETER:
        return daw.set_device_parameter(
            track_index,
            int(args["device_index"]),
            int(args["parameter_index"]),
            float(args["value"]),
        )
    if step.operation == OP_SET_SEND_LEVEL:
        return daw.set_send_level(
            track_index,
            int(args["send_index"]),
            float(args["level"]),
        )
    raise DawError(f"OPERATION_NOT_WHITELISTED:{step.operation}")


def _step_results_from_wire(
    batch: MutationBatch, payload: dict[str, Any]
) -> list[MutationStepResult]:
    by_id = {step.step_id: step for step in batch.steps}
    rows: list[MutationStepResult] = []
    for item in payload.get("step_results") or payload.get("steps") or []:
        step = by_id.get(str(item.get("step_id") or ""))
        rows.append(
            MutationStepResult(
                step_id=str(item.get("step_id") or (step.step_id if step else "")),
                target=dict(item.get("target") or (step.target_ref if step else {})),
                operation=str(item.get("operation") or (step.operation if step else "")),
                requested_value=item.get("requested_value")
                if item.get("requested_value") is not None
                else (dict(step.arguments) if step else {}),
                status=_as_step_status(item.get("status") or STATUS_UNKNOWN),
                observed=item.get("observed") or item.get("result"),
                error=item.get("error"),
                host_id=str(item.get("host_id") or (step.host_id if step else "")),
                purpose=str(item.get("purpose") or (step.purpose if step else "")),
            )
        )
    if not rows:
        return [_not_attempted(step, "EMPTY_COMPOUND_RESULT") for step in batch.steps]
    seen = {row.step_id for row in rows}
    for step in batch.steps:
        if step.step_id not in seen:
            rows.append(_not_attempted(step, "MISSING_STEP_RESULT"))
    return rows


def _is_cancelled(cancelled: Any) -> bool:
    if cancelled is None:
        return False
    if callable(cancelled):
        return bool(cancelled())
    return bool(cancelled)

def execute_mutation_plan(
    daw: Any,
    batch: MutationBatch,
    *,
    session: Any | None = None,
    cancelled: Any = None,
    allow_resend: bool = False,
) -> MutationBatchResult:
    """Send a compound batch when advertised; otherwise sequential SET_*."""
    if allow_resend:
        raise MutationProtocolError(
            "BLIND_RESEND_FORBIDDEN",
            "IN_DOUBT recovery is reconcile + restore, not a second dispatch",
        )
    invalid = _validate_batch(batch)
    if invalid:
        return _failed_before(batch, invalid)
    mismatch = project_identity_mismatch(batch, session)
    if mismatch:
        return _failed_before(batch, mismatch)
    if _is_cancelled(cancelled):
        return _failed_before(batch, "CANCELLED_BEFORE_DISPATCH")

    if compound_available(daw) and hasattr(daw, "execute_mutation_batch"):
        try:
            payload = daw.execute_mutation_batch(batch.to_wire())
        except WriteInDoubt as exc:
            return MutationBatchResult(
                batch_id=batch.batch_id,
                project_identity=batch.project_identity,
                step_results=[
                    MutationStepResult(
                        step_id=step.step_id,
                        target=dict(step.target_ref),
                        operation=step.operation,
                        requested_value=dict(step.arguments),
                        status=STATUS_UNKNOWN,
                        error=str(exc),
                        host_id=step.host_id,
                        purpose=step.purpose,
                    )
                    for step in batch.steps
                ],
                batch_status=BATCH_IN_DOUBT,
                transport=TRANSPORT_COMPOUND,
                error=str(exc),
                replaced_rpc_count=len(batch.steps),
                mutation_rpc_count=1,
            )
        except DawError as exc:
            message = str(exc)
            if "required capability" in message or "Unknown command" in message:
                return _execute_sequential(daw, batch, cancelled=cancelled)
            return _failed_before(batch, message, TRANSPORT_COMPOUND)
        results = _step_results_from_wire(batch, payload if isinstance(payload, dict) else {})
        status = _as_batch_status(
            (payload or {}).get("batch_status") or _classify_batch(results)
        )
        if str(status) == "success":
            status = _classify_batch(results)
        return MutationBatchResult(
            batch_id=str((payload or {}).get("batch_id") or batch.batch_id),
            project_identity=str(
                (payload or {}).get("project_identity") or batch.project_identity
            ),
            step_results=results,
            batch_status=status,
            transport=TRANSPORT_COMPOUND,
            error=(payload or {}).get("error"),
            replaced_rpc_count=len(batch.steps),
            mutation_rpc_count=1,
        )
    return _execute_sequential(daw, batch, cancelled=cancelled)


def _execute_sequential(
    daw: Any,
    batch: MutationBatch,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> MutationBatchResult:
    results, in_doubt = run_ordered_steps(
        batch.steps,
        lambda step: _apply_sequential_step(daw, step),
        cancelled=cancelled,
    )
    rpc_count = sum(1 for row in results if row.status in {STATUS_APPLIED, STATUS_UNKNOWN})
    return MutationBatchResult(
        batch_id=batch.batch_id,
        project_identity=batch.project_identity,
        step_results=results,
        batch_status=_classify_batch(results, in_doubt=in_doubt),
        transport=TRANSPORT_SEQUENTIAL,
        error="IN_DOUBT" if in_doubt else None,
        replaced_rpc_count=len(batch.steps),
        mutation_rpc_count=rpc_count,
    )


def _send_silent(row: dict[str, Any]) -> bool:
    value = row.get("value") if row.get("value") is not None else row.get("level")
    try:
        return abs(float(value or 0.0)) < 1e-6
    except (TypeError, ValueError):
        return False


def _host_label(host: dict[str, Any]) -> str:
    return str(host.get("name") or host.get("key") or host.get("index") or "")


def compile_prepare_hosts(
    hosts: list[dict[str, Any]],
    *,
    inventory: list[dict[str, Any]],
    baselines: dict[int, dict[str, Any]],
    project_identity: str,
    targets: dict[int, str] | None = None,
    expected_state_tokens: dict[str, Any] | None = None,
    expected_project_path: str = "",
    expected_project_name: str = "",
    batch_id: str | None = None,
) -> MutationBatch:
    """Compile PREPARE mutations for capture hosts. Main is a sidecar, not mutated here."""
    if len(hosts) > CAPTURE_HOST_LIMIT:
        raise MutationProtocolError(
            "CAPTURE_HOST_LIMIT",
            f"prepares at most {CAPTURE_HOST_LIMIT} source hosts; Main sidecar is not a MutationBatch target",
        )
    batch_id = batch_id or new_batch_id()
    by_track = {int(row["track_index"]): row for row in inventory if "track_index" in row}
    steps: list[MutationStep] = []
    seq = 0
    for host in hosts:
        host_index = int(host["index"])
        host_id = _host_label(host)
        tap = by_track.get(host_index) or {}
        device_index = tap.get("device_index")
        target_name = str(
            (targets or {}).get(host_index)
            or host.get("target_name")
            or host.get("target")
            or ""
        )
        target = {
            "track_index": host_index,
            "host_id": host_id,
            "target_name": target_name,
        }
        if device_index is not None and tap.get("device_on_param_index") is not None:
            seq += 1
            steps.append(
                MutationStep(
                    step_id=f"{batch_id}:{host_id}:tap_on:{seq}",
                    operation=OP_SET_DEVICE_PARAMETER,
                    target_ref=target,
                    arguments={
                        "track_index": host_index,
                        "device_index": int(device_index),
                        "parameter_index": int(tap["device_on_param_index"]),
                        "value": 1.0,
                    },
                    purpose="TAP_PARAMETER",
                    host_id=host_id,
                )
            )
        if device_index is not None and tap.get("rec_param_index") is not None:
            seq += 1
            steps.append(
                MutationStep(
                    step_id=f"{batch_id}:{host_id}:tap_rec:{seq}",
                    operation=OP_SET_DEVICE_PARAMETER,
                    target_ref=target,
                    arguments={
                        "track_index": host_index,
                        "device_index": int(device_index),
                        "parameter_index": int(tap["rec_param_index"]),
                        "value": 0.0,
                    },
                    purpose="TAP_PARAMETER",
                    host_id=host_id,
                )
            )
        seq += 1
        steps.append(
            MutationStep(
                step_id=f"{batch_id}:{host_id}:input:{seq}",
                operation=OP_SET_TRACK_INPUT_ROUTING,
                target_ref=target,
                arguments={
                    "track_index": host_index,
                    "target_name": target_name,
                    "preferred_channel": PREFERRED_INPUT_CHANNEL,
                    "routing_type": "",
                    "routing_channel": "",
                },
                purpose="ROUTING",
                host_id=host_id,
            )
        )
        seq += 1
        steps.append(
            MutationStep(
                step_id=f"{batch_id}:{host_id}:monitor:{seq}",
                operation=OP_SET_TRACK_MONITORING,
                target_ref=target,
                arguments={"track_index": host_index, "monitoring": "in"},
                purpose="MONITORING",
                host_id=host_id,
            )
        )
        seq += 1
        steps.append(
            MutationStep(
                step_id=f"{batch_id}:{host_id}:output:{seq}",
                operation=OP_SET_TRACK_OUTPUT_ROUTING,
                target_ref=target,
                arguments={
                    "track_index": host_index,
                    "routing_type": "",
                    "routing_channel": "",
                    "routing_candidates": list(OUTPUT_OFF_CANDIDATES),
                },
                purpose="OUTPUT",
                host_id=host_id,
            )
        )
        baseline = baselines.get(host_index) or {}
        for row in baseline.get("sends") or []:
            if _send_silent(row):
                continue
            seq += 1
            steps.append(
                MutationStep(
                    step_id=f"{batch_id}:{host_id}:send:{seq}",
                    operation=OP_SET_SEND_LEVEL,
                    target_ref=target,
                    arguments={
                        "track_index": host_index,
                        "send_index": int(row.get("send_index", 0)),
                        "level": 0.0,
                    },
                    purpose="ROUTING",
                    host_id=host_id,
                )
            )
    return MutationBatch(
        batch_id=batch_id,
        project_identity=project_identity,
        expected_state_tokens=dict(expected_state_tokens or {}),
        steps=steps,
        expected_project_path=expected_project_path,
        expected_project_name=expected_project_name,
        kind=KIND_TEMPORARY_CAPTURE_HOST,
    )


def compile_restore_hosts(
    hosts: list[dict[str, Any]],
    *,
    baselines: dict[int, dict[str, Any]],
    project_identity: str,
    expected_state_tokens: dict[str, Any] | None = None,
    expected_project_path: str = "",
    expected_project_name: str = "",
    batch_id: str | None = None,
) -> MutationBatch:
    """Restore from durable baselines. Host groups are independent of each other."""
    batch_id = batch_id or new_batch_id()
    steps: list[MutationStep] = []
    seq = 0
    for host_i, host in enumerate(hosts):
        host_index = int(host["index"])
        host_id = _host_label(host)
        baseline = baselines.get(host_index) or {}
        target = {"track_index": host_index, "host_id": host_id}
        first_for_host = True

        def _add(purpose: str, operation: str, arguments: dict[str, Any]) -> None:
            nonlocal seq, first_for_host
            seq += 1
            steps.append(
                MutationStep(
                    step_id=f"{batch_id}:{host_id}:{purpose}:{seq}",
                    operation=operation,
                    target_ref=target,
                    arguments=arguments,
                    purpose="RESTORE",
                    independent=host_i > 0 and first_for_host,
                    host_id=host_id,
                )
            )
            first_for_host = False

        incoming = baseline.get("input") or {}
        prev_in = str(incoming.get("input_routing_type") or "")
        if prev_in:
            _add(
                "input",
                OP_SET_TRACK_INPUT_ROUTING,
                {
                    "track_index": host_index,
                    "routing_type": prev_in,
                    "routing_channel": str(incoming.get("input_routing_channel") or ""),
                },
            )
        outgoing = baseline.get("output") or {}
        prev_out = str(outgoing.get("output_routing_type") or "")
        if prev_out:
            _add(
                "output",
                OP_SET_TRACK_OUTPUT_ROUTING,
                {
                    "track_index": host_index,
                    "routing_type": prev_out,
                    "routing_channel": str(outgoing.get("output_routing_channel") or ""),
                },
            )
        monitoring = str(
            (baseline.get("monitoring") or {}).get("monitoring")
            or (baseline.get("monitoring") or {}).get("value")
            or ""
        )
        if monitoring:
            _add(
                "monitor",
                OP_SET_TRACK_MONITORING,
                {"track_index": host_index, "monitoring": monitoring},
            )
        for row in baseline.get("sends") or []:
            level = row.get("value") if row.get("value") is not None else row.get("level")
            _add(
                "send",
                OP_SET_SEND_LEVEL,
                {
                    "track_index": host_index,
                    "send_index": int(row.get("send_index", 0)),
                    "level": float(level or 0.0),
                },
            )
    return MutationBatch(
        batch_id=batch_id,
        project_identity=project_identity,
        expected_state_tokens=dict(expected_state_tokens or {}),
        steps=steps,
        expected_project_path=expected_project_path,
        expected_project_name=expected_project_name,
        kind=KIND_TEMPORARY_CAPTURE_HOST,
    )


def prepare_compile_ready(inventory: list[dict[str, Any]], hosts: list[dict[str, Any]]) -> bool:
    """Compound prepare needs tap parameter indices already in inventory (no extra RPC)."""
    by_track = {int(row["track_index"]): row for row in inventory if "track_index" in row}
    if not hosts:
        return False
    for host in hosts:
        tap = by_track.get(int(host["index"])) or {}
        if tap.get("device_index") is None:
            return False
        if tap.get("device_on_param_index") is None:
            return False
        if tap.get("rec_param_index") is None:
            return False
    return True
