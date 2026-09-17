# LIVE_SESSION_READINESS_V1

STATUS: **VERIFIED / FROZEN**.

Do not retune handshake, probe timeouts, State Trust, or capture to “improve”
readiness. Reopen only for a reproducible bug.

## What “Live available” means

A listening TCP port is **not** ready.

Ready requires, in order:

1. TCP connect
2. `protocol_hello` with a matching `request-id`
3. lightweight snapshot (`get_session_info` + project path/name)
4. valid project identity
5. `SESSION_READY`

Otherwise the status is `LIVE_UNAVAILABLE`, `ZOMBIE_PORT`, or `SESSION_NOT_READY`.
Close the connection. Retry with bounded backoff (1/2/4/8s). Queue no writes.

If the open set changed during a disconnect, prior plans are `STALE_PLAN`.
Rediscover project + tokens before continuing.

## Fixture result (not a musical claim)

- `project-ready` = `VERIFIED` (observation plumbing)
- `producer-analyze` = `INSUFFICIENT_EVIDENCE` because the arrangement is empty

That is plumbing + honest abstention. It is not musical generalization.

## Runtime

`copilot.daw.session_ready_v1` (`FROZEN = True`).
Tests: `tests/test_session_ready_v1.py`.
