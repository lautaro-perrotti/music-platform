# RPC_OPTIMIZATION_V2

Status: **VERIFIED / FROZEN**. Reopen only for a reproducible bug.

Authoritative Live read model. Not “cache more.”

## Measured (Groove Rider, AnalyzeProject)

| | PERFORMANCE_OPTIMIZATION_V1 | PRODUCER_RUNTIME_V1 | RPC_OPTIMIZATION_V2 |
|---|---:|---:|---:|
| Wall | 254.22 s | 185.38 s | **167.55 s** |
| RPC count | 237 | 174 | **109** |
| RPC wall | 110.7 s | 82.70 s | **54.54 s** |

Precision parity vs Producer Runtime: **VERIFIED**. `MUSICAL WRITES = 0`. Terminal restored. `AUTO_36_68`. `INSUFFICIENT_EVIDENCE`. Alignment still `LIMITED ±52 ms`.

## What is frozen

- Freshness domains + generation invalidation (`src/copilot/runtime/freshness.py`)
- Operation-scoped `ProjectReadView` V2 (`src/copilot/runtime/rpc.py`)
- `READ_CAPTURE_HOSTS_STATE` via `get_tracks_info` (`src/copilot/runtime/host_state.py`)
- Snapshot reuse when `NO_CHANGES_REQUIRED` (no topology mutation)
- One JSON request / one JSON response. No concatenated TCP framing.
- No speculative Live concurrency.

109 RPC is not a mandate to chase 0. Remaining writes and post-mutation readbacks are safety.

## Do not

- Weaken State Trust, `PROJECT_MISMATCH`, journals, or post-mutation verification
- Shorten `PRE_ROLL_QN`
- Treat timestamps as freshness authority
- Pipeline Live RPCs
