# ABLETON_MUTATION_PROTOCOL_V1

Compound **temporary capture-host** mutations. Not musical production writes.

Status: **VERIFIED / FROZEN** on Groove Rider Live after the running Remote
Script advertised `compound.temporary_mutation`. Sequential fallback remains
for older Control Surfaces.

Do not infer support from Ableton version. Do not send `execute_mutation_batch`
just to discover it.

## Live measurement (`LIVE_COMPOUND_MUTATION_VALIDATION_V1`)

CS Off/On does **not** reimport `AbletonMCP`. A Live process start is required
for hello to advertise the capability.

Groove Rider after that start:

```
wall 130.53 s          (was 167.55 s)
RPC 34 / 16.20 s       (was 109 / 54.54 s)
MODEL 47.28 s          (was 58.70 s; report separately)
INTRINSIC_AUDIO 51.15 s (was 46.28 s)
ORCHESTRATION 15.65 s  (SESSION_READY 11.40 s after launch)
MUSICAL WRITES 0
fallback used = false
```

`execute_mutation_batch` × 4 (prepare/restore × 2 passes). Sequential
`set_device_parameter` / `set_track_*` for host prepare/restore = 0.

Evidence: `logs/live_compound_validation_v1/`.

## Contract

Python persists rollback + journals **before** any Live mutate.

Then, if handshake has `compound.temporary_mutation` v1:

1. one `execute_mutation_batch` (ordered whitelist)
2. per-step `APPLIED | FAILED | NOT_ATTEMPTED | UNKNOWN`
3. one `READ_CAPTURE_HOSTS_STATE` / `get_tracks_info` verify

Otherwise: existing sequential `set_*`. Same MutationBatchResult shape.

Batch status: `COMPLETE | PARTIAL_FAILURE | FAILED_BEFORE_EXECUTION | IN_DOUBT`.

Never `success=true` for a multi-step write.

Remote Script does **not** roll back. Core remains recovery authority.

Whitelist only:

- `SET_TRACK_MONITORING`
- `SET_TRACK_INPUT_ROUTING` (`target_name` + `preferred_channel` allowed)
- `SET_TRACK_OUTPUT_ROUTING` (`routing_candidates` allowed)
- `SET_DEVICE_PARAMETER`
- `SET_SEND_LEVEL`

No eval, no LOM expressions, no LLM method names, no musical EQ/MIDI/automation.

## Safety

- `PROJECT_MISMATCH` → fail closed, zero steps
- disconnect/timeout after dispatch → `IN_DOUBT`, never blind resend
- host 1 applied / host 2 failed is visible; later dependent steps `NOT_ATTEMPTED`
- restore host groups are independent of each other
- per-host journals keep `batch_id` + `step_ids`
- `MUSICAL WRITES` remain 0
- max 2 source hosts + Main sidecar (unchanged M4L topology)

`LIVE_PARTIAL_FAILURE` and `LIVE_DISCONNECT_IN_DOUBT` were
`NOT_EXECUTED_FOR_SAFETY`. Tests/mock remain the evidence for those branches.
