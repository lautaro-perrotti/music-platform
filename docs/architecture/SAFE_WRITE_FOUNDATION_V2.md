# SAFE_WRITE_FOUNDATION_V2

Status: **VERIFIED** on mock Live (`MockAbletonAdapter`). This is the generic
production-write foundation. It is **not** capture-host
`ABLETON_MUTATION_PROTOCOL_V1`. It does **not** certify EQ, compression, MIDI,
or arrangement actions. `SET_TRACK_VOLUME` remains the only certified musical
action.

AnalyzeProject stays read-only (`MUSICAL WRITES = 0`). This milestone exists
for `producer-run` / `production-write` / `TransactionManager`.

## Contract split

| Surface | Kind | Allowed mutations |
|---|---|---|
| `ABLETON_MUTATION_PROTOCOL_V1` | `TEMPORARY_CAPTURE_HOST` | routing / monitoring / tap / sends |
| `SAFE_WRITE_FOUNDATION_V2` | `PRODUCTION_MUSICAL` | certified musical writes only |

Do not put a musical production write in a capture-host `MutationBatch`.
Do not treat a capture-host batch as a musical transaction.

## Canonical lifecycle

```
PLAN
  → reconcile project
  → reconcile targets
  → validate preconditions
  → persist durable rollback state
  → journal PREPARED
  → execute
  → authoritative readback
  → reconcile
  → verify
  → KEEP
OR ROLLBACK → authoritative readback → exact verification
```

Durable pre-state and journals exist **before** any Live mutation. Core owns
rollback. Remote Script reports per-step reality. There is no second
transaction system inside Max / Remote Script.

## Typed contracts

`MutationIntent`, `MutationTarget`, `MutationPrecondition`,
`MutationExecution`, `MutationReadback`, `MutationVerification`,
`MutationRollback`, `MutationResult`.

Unknown write outcome is `IN_DOUBT`. Never treat it as `FAILED`. Never blind
retry.

## Failure states (fail-closed)

`TARGET_NOT_FOUND` `TARGET_AMBIGUOUS` `PROJECT_MISMATCH` `STALE_PLAN`
`PRECONDITION_FAILED` `EXECUTION_FAILED` `PARTIAL_FAILURE` `IN_DOUBT`
`READBACK_MISMATCH` `VERIFICATION_FAILED` `ROLLBACK_FAILED` `CANCELLED`
`SUPERSEDED`

## Action certification

The generic executor does **not** auto-certify musical actions.
`CERTIFIED_PRODUCTION_ACTIONS = {SET_TRACK_VOLUME}`.
Uncertified types may be planned for compound rollback graphs; they cannot
execute. `APPLY_VOCABULARY` stays `SET_TRACK_VOLUME` only.

## Compound rollback foundation

Rollback order is dependency-aware. Reverse apply order is the default.
`depends_on` undoes the dependent step first. Steps marked
`NOT_INDEPENDENTLY_REVERSIBLE` cannot be undone in isolation. This is the
rollback foundation for future production plans — not an EQ/comp implementation.

## Recovery

`classify_journal` remains the recovery classifier. `PREPARED` without `SENT`
is incomplete work, not a verified write. `SENT` without a known outcome is
`IN_DOUBT`. Recovery never auto-mutates.
