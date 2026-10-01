# SESSION_SAVE_CAPABILITY_V1

## Terminal status

```text
SESSION_SAVE_CAPABILITY_V1 = BLOCKED_LIVE_API_UNAVAILABLE
```

This is an explicit environment/API boundary, not a simulated success.

## Audit performed

Host: Windows, 2026-09-27

Installed Live environment discovered read-only:

- `C:\ProgramData\Ableton\Live 12 Trial`
- Live user environments under `C:\Users\lsper\AppData\Roaming\Ableton\Live 12.4.5` and `Live 12.4.6`
- local bundled Python resources under `Live 12 Trial\Resources\Python`

Remote Script evidence:

- the vendored bridge is `vendor/abletonmcp_remote_script/AbletonMCP/__init__.py`;
- its handshake advertises `session.read`, `session.transport`, musical typed
  capabilities, and capture capabilities;
- it does **not** advertise `session.save`;
- its command dispatcher contains no typed `save` operation;
- it does contain read-only session-path and modified-state probes, but those
  are not a save implementation;
- the installed User Remote Scripts directories contain no alternate
  `AbletonMCP` implementation that supplies save.

Core protocol evidence:

- `DawAdapter.save_session()` and `AbletonTcpAdapter.save_session()` existed;
- before this audit, `save` was incorrectly classified as
  `session.transport`;
- it is now classified as `session.save`;
- because `session.save` is not advertised, strict capability negotiation
  fails closed before dispatch.

Local API evidence:

- the installed Live Python resources expose test/mock API bytecode, but no
  verifiable `song.save()`, `application.save()`, `save_live_set()`, or
  equivalent supported save method was found;
- no method name is inferred from memory or added speculatively.

## Safety result

The existing persistence policy remains authoritative:

```text
SafeWrite VERIFIED
    != musical KEEP
    != saved on disk
```

The current candidate remains `PENDING / CANDIDATE_PENDING`. A KEEP request
fails closed with `KEEP_PERSISTENCE_FAILED` and does not mark the candidate
kept when `session.save` is unavailable.

No keyboard automation, Ctrl+S simulation, UI automation, shell workaround,
arbitrary Python/eval, Max hack, Save As path, or second transaction system was
introduced.

## Required future boundary

This milestone can only become `VERIFIED` after an installed Ableton/Remote
Script environment provides a documented, callable, typed save operation that
can be capability-negotiated and verified by Live readback plus working-copy
filesystem evidence. Until then, the truthful product state is:

```text
SAFEWRITE_TRANSACTION    = VERIFIED when readback passes
MUSICAL_DECISION         = PENDING until human KEEP/DISCARD
PERSISTENCE              = CANDIDATE_PENDING
MUSICAL_KEEP             = BLOCKED_LIVE_API_UNAVAILABLE
```
