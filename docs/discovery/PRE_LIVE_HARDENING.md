# PRE-LIVE Hardening Gate

**Date:** 2026-09-13  
**Classes:** `PRELIVE_VERIFIED` ≠ `LIVE_VERIFIED`

Ableton is still absent. Nothing here is `LIVE_VERIFIED`.

---

## Gate

| Item | Status |
| --- | --- |
| PRE_LIVE_HARDENING | `VERIFIED` |
| LIVE_1 | `BLOCKED_BY_ENVIRONMENT` |

Missing: Ableton Live installation.

---

## Report

| Area | Status | Evidence |
| --- | --- | --- |
| STATE CONSISTENCY | `PRELIVE_VERIFIED` | `revision` follows observed `state_hash`. Identical snapshots keep the revision. External insert/rename/clip/param/delete bump it. Playback cursor does not. Float jitter does not. |
| IDENTITY SAFETY | `PRELIVE_VERIFIED` | Rollback: `stable_id` → fingerprint → current locator. Duplicate names, rename, insert, clip add, same-device twins, random mixes: resolve uniquely or `ROLLBACK_CONFLICT`. After restart, incarnation mismatch ignores `stable_id` as authority. |
| TRANSACTION SAFETY | `PRELIVE_VERIFIED` | Fault after each slice write ends `ROLLED_BACK` / `ROLLBACK_CONFLICT` / `FAILED`, never `VERIFIED`. Residual is journaled. |
| CRASH RECOVERY | `PRELIVE_VERIFIED` | Append-only `logs/agent_journal.jsonl` with flush+fsync. Crash A `PLANNED` → `RECOVERY_REQUIRED`. B `SENT` → `IN_DOUBT`. C `APPLIED` → `RECOVERY_REQUIRED`. D rollback started → `ROLLBACK_REQUIRED`. No automatic destructive replay. |
| NETWORK FAILURE | `PRELIVE_VERIFIED` | Invalid/truncated JSON, wrong `request_id`, drop/timeout, disconnect: no `VERIFIED`. Write timeout → `IN_DOUBT`, not `FAILED`. |
| IDEMPOTENCY | `PRELIVE_VERIFIED` | Tools classified. `command_id` + `expected_revision` journaled before side effect. No Remote Script dedup. Lost ack: read-back; retry only if postcondition is absent. Ambiguous → stay `IN_DOUBT`. |
| CONCURRENCY | `PRELIVE_VERIFIED` | `SessionMutationLock` serializes writes. Two writers with the same captured revision: one applies, the other sees stale revision. |
| PROTOCOL COMPATIBILITY | `PRELIVE_VERIFIED` | `protocol_hello` + capabilities. Missing capability fails before write. Vendor meta + contract tests. No auto-update. |
| INPUT VALIDATION | `PRELIVE_VERIFIED` | NaN/Inf/null/string/range rejected before DAW. Pitch canonical `60`. Time as beats/bars; 4/4, 3/4, 6/8 tested. |
| LOCAL SECURITY | `PRELIVE_VERIFIED` | Bind/connect `127.0.0.1` only. Wildcard refused. No extra auth: `LOCALHOST_TRUST_BOUNDARY`. |
| OBSERVABILITY | `PRELIVE_VERIFIED` | Structured write logs: txn/command/session/target/locator/revisions/operation/result. Journal is the durable trace. |

---

## Decisions

**Idempotency:** Copilot-layer reconciliation only. The vendored script does not deduplicate commands. A `NON_IDEMPOTENT_WRITE` may be retried only after a read proves the postcondition is absent.

**Revision:** Version of normalized observed state, not our command count. User, controller, or agent mutations all count.

**IN_DOUBT:** A sent write whose acknowledgement was lost is never converted to `FAILED`. Reconcile or stay uncertain.

**Local trust:** Loopback only. Session token deferred until after the first real Live verification.

---

## Still `LIVE_REQUIRED`

Actual LOM semantics, Remote Script lifecycle, timing, object invalidation, Live undo, device/plugin behavior, performance, audio capture.

---

## Canonical path (unchanged)

```
python -m copilot.cli detect
python -m copilot.cli install-script
python -m copilot.cli probe
python -m copilot.cli slice1
python -m copilot.cli undo
```

Mock is explicit (`mock-slice1` / tests). Live commands never fall back.
