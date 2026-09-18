# PRODUCER_RUNTIME_V1

Status: **VERIFIED / FROZEN**. Reopen only for a reproducible bug.

Capture batching remains frozen at `max_batch_sources = 2`. `PRE_ROLL_QN = 16`.
`RPC_OPTIMIZATION_V2` is **VERIFIED / FROZEN** (see `docs/architecture/RPC_OPTIMIZATION_V2.md`).

## Principle

Agent = intent. Runtime = execution.

```
Agent / Astra / UI
        ↓
    Producer API
        ↓
      Task
        ↓
   Task Compiler
        ↓
 Execution Graph
        ↓
 Graph Optimizer
        ↓
 Scheduler
        ↓
 Capability Registry
        ↓
 Providers
        ↓
 Ableton / DSP / Models / Filesystem / future tools
```

Public call:

```python
from copilot.runtime import Producer
result = producer.analyze_project(project)
```

CLI (thin adapter, no duplicated orchestration):

```
python -m copilot.cli analyze-project "<folder>"
```

`producer-analyze` remains as a low-level debug command.

AGENT HIGH-LEVEL COMMAND COUNT: **1**

## What this milestone does not do

Does not reopen PERFORMANCE_OPTIMIZATION_V1. Does not widen capture hosts.
Does not pipeline concatenated TCP JSON. Does not implement References,
Music Flamingo, mixing, mastering, or other future tasks — they must register
as capabilities on this runtime.

Deferred: `RUNTIME_LAUNCH_PATH_OPTIMIZATION` (launcher sleep / `PROBE_BACKOFF`).
`ABLETON_MUTATION_PROTOCOL_V1` is **VERIFIED / FROZEN**. Sequential fallback
remains for older Remote Scripts.
Do not reopen read-path RPC squeezing. Next physical capture win is N-host M4L topology, not a shorter pre-roll.
