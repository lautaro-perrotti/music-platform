# PRODUCER_EXECUTION_V1

Status: **MOCK_VERIFIED / LIVE_PENDING**.

This is an additive SafeWrite intent kind. It does not reopen or replace
`SAFE_WRITE_FOUNDATION_V2`; the legacy `PRODUCTION_MUSICAL` vocabulary remains
`SET_TRACK_VOLUME` only. Producer plans use the same core transaction manager,
journal, durable pre-state, authoritative readback, fail-closed `IN_DOUBT`, and
rollback authority.

## Minimum action set

```text
CREATE_TRACK
LOAD_SAMPLE
DUPLICATE_CLIP_TO_ARRANGEMENT
LOAD_DEVICE
SET_DEVICE_PARAMETER
SET_TRACK_VOLUME
```

The path is:

```text
MusicPlan → ProductionCompiler → MutationIntent(PRODUCER_EXECUTION_V1)
→ SafeWriteExecutor → DawAdapter → readback → KEEP / ROLLBACK / IN_DOUBT
```

The mock suite covers all six actions. Ableton certification remains pending
until the working-copy Live test verifies each action, its authoritative
readback, and its rollback. `AnalyzeProject` remains read-only.
