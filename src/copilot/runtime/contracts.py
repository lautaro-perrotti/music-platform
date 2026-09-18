"""Public task contracts. Intent in, product result out. No sequencing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    IN_DOUBT = "IN_DOUBT"


class TaskKind(StrEnum):
    ANALYZE_PROJECT = "AnalyzeProject"
    ANALYZE_REFERENCE = "AnalyzeReference"
    COMPARE_TO_REFERENCE = "CompareToReference"
    INDEX_SAMPLE_LIBRARY = "IndexSampleLibrary"
    FIND_SAMPLES = "FindSamples"
    PRODUCE_TRACK = "ProduceTrack"
    ARRANGE_TRACK = "ArrangeTrack"
    MIX_PROJECT = "MixProject"
    MASTER_PROJECT = "MasterProject"
    REPAIR_PROJECT = "RepairProject"
    FINISH_PROJECT = "FinishProject"
    APPLY_PLAN = "ApplyPlan"
    VERIFY_RESULT = "VerifyResult"
    ROLLBACK = "Rollback"


DEFAULT_ANALYZE_GOALS = (
    "ENERGY_STRUCTURE",
    "KICK_BASS_RELATIONSHIP",
    "SOURCE_ACTIVITY",
)


@dataclass
class TaskRequest:
    """Caller intent. Must not contain implementation sequencing."""

    kind: TaskKind
    project: str | None = None
    scope: str = "default"
    constraints: dict[str, Any] = field(default_factory=dict)
    reasoning_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "project": self.project,
            "scope": self.scope,
            "constraints": dict(self.constraints),
            "reasoning_enabled": self.reasoning_enabled,
        }


@dataclass
class AnalyzeProjectRequest(TaskRequest):
    region_preference: str | None = None
    start_qn: float | None = None
    end_qn: float | None = None
    goals: tuple[str, ...] = DEFAULT_ANALYZE_GOALS

    def __init__(
        self,
        project: str | None = None,
        *,
        scope: str = "default",
        region_preference: str | None = None,
        start_qn: float | None = None,
        end_qn: float | None = None,
        reasoning_enabled: bool = True,
        goals: tuple[str, ...] | list[str] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            kind=TaskKind.ANALYZE_PROJECT,
            project=project,
            scope=scope,
            constraints=constraints or {},
            reasoning_enabled=reasoning_enabled,
        )
        self.region_preference = region_preference
        self.start_qn = start_qn
        self.end_qn = end_qn
        self.goals = tuple(goals) if goals is not None else DEFAULT_ANALYZE_GOALS

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "region_preference": self.region_preference,
                "start_qn": self.start_qn,
                "end_qn": self.end_qn,
                "goals": list(self.goals),
            }
        )
        return payload


@dataclass
class TaskResult:
    status: TaskStatus
    kind: TaskKind
    reason: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)
    rpc: dict[str, Any] = field(default_factory=dict)
    terminal: dict[str, Any] = field(default_factory=dict)
    musical_writes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "kind": self.kind.value,
            "reason": self.reason,
            "payload": self.payload,
            "performance": self.performance,
            "rpc": self.rpc,
            "terminal": self.terminal,
            "MUSICAL WRITES": self.musical_writes,
            "NO WRITE": self.musical_writes == 0,
        }


@dataclass
class AnalyzeProjectResult(TaskResult):
    project_identity: str | None = None
    analysis_scope: str = "default"
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    diagnosis: dict[str, Any] | None = None
    confidence: str | None = None
    limitations: list[Any] = field(default_factory=list)
    next_evidence_requests: list[str] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    high_level_command_count: int = 1

    def __init__(
        self,
        *,
        status: TaskStatus,
        reason: str | None = None,
        project_identity: str | None = None,
        analysis_scope: str = "default",
        evidence_summary: dict[str, Any] | None = None,
        diagnosis: dict[str, Any] | None = None,
        confidence: str | None = None,
        limitations: list[Any] | None = None,
        next_evidence_requests: list[str] | None = None,
        gate: dict[str, Any] | None = None,
        performance: dict[str, Any] | None = None,
        rpc: dict[str, Any] | None = None,
        terminal: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        musical_writes: int = 0,
    ) -> None:
        super().__init__(
            status=status,
            kind=TaskKind.ANALYZE_PROJECT,
            reason=reason,
            payload=payload or {},
            performance=performance or {},
            rpc=rpc or {},
            terminal=terminal or {},
            musical_writes=musical_writes,
        )
        self.project_identity = project_identity
        self.analysis_scope = analysis_scope
        self.evidence_summary = evidence_summary or {}
        self.diagnosis = diagnosis
        self.confidence = confidence
        self.limitations = limitations or []
        self.next_evidence_requests = next_evidence_requests or []
        self.gate = gate or {}
        self.high_level_command_count = 1

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "task": "AnalyzeProject",
                "project_identity": self.project_identity,
                "analysis_scope": self.analysis_scope,
                "evidence_summary": self.evidence_summary,
                "diagnosis": self.diagnosis,
                "confidence": self.confidence,
                "limitations": self.limitations,
                "next_evidence_requests": self.next_evidence_requests,
                "gate": self.gate,
                "AGENT_HIGH_LEVEL_COMMAND_COUNT": self.high_level_command_count,
            }
        )
        return payload


@dataclass
class TaskContext:
    """Mutable blackboard for one compiled task. Not a public API."""

    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value
