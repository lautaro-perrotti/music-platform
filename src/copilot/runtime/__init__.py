"""Producer Runtime public surface.

    from copilot.runtime import Producer
    result = producer.analyze_project(project)
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Producer",
    "AnalyzeProjectRequest",
    "AnalyzeProjectResult",
    "TaskKind",
    "TaskRequest",
    "TaskResult",
    "TaskStatus",
    "compile_analyze_project",
    "discover_environment",
    "ensure_ableton_ready",
]


def __getattr__(name: str) -> Any:
    if name in {
        "Producer",
        "compile_analyze_project",
    }:
        from copilot.runtime.producer import Producer, compile_analyze_project

        mapping = {
            "Producer": Producer,
            "compile_analyze_project": compile_analyze_project,
        }
        return mapping[name]
    if name in {
        "AnalyzeProjectRequest",
        "AnalyzeProjectResult",
        "TaskKind",
        "TaskRequest",
        "TaskResult",
        "TaskStatus",
    }:
        from copilot.runtime.contracts import (
            AnalyzeProjectRequest,
            AnalyzeProjectResult,
            TaskKind,
            TaskRequest,
            TaskResult,
            TaskStatus,
        )

        mapping = {
            "AnalyzeProjectRequest": AnalyzeProjectRequest,
            "AnalyzeProjectResult": AnalyzeProjectResult,
            "TaskKind": TaskKind,
            "TaskRequest": TaskRequest,
            "TaskResult": TaskResult,
            "TaskStatus": TaskStatus,
        }
        return mapping[name]
    if name in {"discover_environment", "ensure_ableton_ready"}:
        from copilot.runtime.environment_autonomy_v1 import (
            discover_environment,
            ensure_ableton_ready,
        )

        return {
            "discover_environment": discover_environment,
            "ensure_ableton_ready": ensure_ableton_ready,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
