# PROJECT_PERSISTENCE_AND_SAVE_POLICY_V1

## Status

`IMPLEMENTED / BLOCKED_LIVE_API_UNAVAILABLE`

The Studio now keeps three independent facts separate:

1. `SafeWrite` transaction outcome (`VERIFIED`, `ROLLED_BACK`, `IN_DOUBT`, ...)
2. human musical decision (`PENDING`, `KEPT`, `DISCARDED`)
3. working-copy disk persistence (`CANDIDATE_PENDING`, `IN_SYNC`, `LIVE_DIRTY`,
   `SAVE_FAILED`, `UNKNOWN`, ...)

A verified Live mutation is never reported as a durable musical `KEEP` by
itself. A candidate is persisted as `READY + PENDING + CANDIDATE_PENDING` and
the Studio exposes disk/path/hash/mtime evidence without saving automatically.

## Current boundary

`DawAdapter.save_session()` existed as a Python surface, but the vendored
Remote Script does not advertise or implement a typed `session.save` command.
The protocol now classifies `save` as `session.save` instead of incorrectly
classifying it as `session.transport`. Since the capability is absent, a real
Ableton `KEEP` fails closed with `KEEP_PERSISTENCE_FAILED` and preserves the
candidate as reviewable `READY/PENDING`.

No keyboard automation, second bridge, arbitrary LOM, or direct file rewrite is
used to bypass this boundary.

## Verified behavior

- controlled working-copy policy is checked before save;
- original/untitled projects are rejected;
- save requires the advertised typed capability;
- save acknowledgement is followed by path, identity, file, hash and Live
  state readback;
- rollback marks the musical decision discarded and reconciles disk evidence;
- candidate and project persistence state are durable in the existing Studio
  store, not in a second Live transaction journal;
- no LLM/API calls are involved.

## Remaining closure

To close this milestone, the existing Remote Script boundary must gain an
authoritative, typed `session.save` capability supported by the installed Live
environment. Until then the truthful terminal state is:

```text
SAFEWRITE_TRANSACTION       = VERIFIED when readback passes
MUSICAL_DECISION            = PENDING until human KEEP/DISCARD
WORKING_COPY_PERSISTENCE    = CANDIDATE_PENDING
MUSICAL_KEEP                = BLOCKED_SAVE_CAPABILITY
```
