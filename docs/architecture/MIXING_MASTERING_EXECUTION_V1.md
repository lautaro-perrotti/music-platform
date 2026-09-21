# MIXING_MASTERING_EXECUTION_V1

## Scope

Core-owned execution of one Lucas mix iteration and one Lucas master iteration:

```text
Lucas strategy
  -> MusicPlan
  -> ProductionCompiler
  -> SafeWriteExecutor
  -> authoritative readback
  -> rollback or KEEP decision
```

Lucas planning and musical strategy remain unchanged. This boundary does not
add a second DAW writer, a second transaction authority, or new SafeWrite
action kinds.

## Implemented boundary

- Track volume uses canonical `SET_TRACK_VOLUME`.
- Device loads use canonical `LOAD_DEVICE`.
- Parameter changes use canonical `SET_DEVICE_PARAMETER`.
- Main is exposed to the compiler as a Core-owned synthetic target while all
  mutations still delegate to the existing `DawAdapter` and SafeWrite journal.
- Unsupported, ambiguous, stale, or over-large operations are deferred without
  a DAW mutation.
- A failing post-apply analysis/critique callback fails closed and rolls back
  every action already applied in that phase.
- The bounded runner executes one mix phase and one master phase; it never
  starts an optimizer loop.

## Verification

### Offline

- Mixing/mastering tests: pass.
- ProductionCompiler and SafeWrite foundation tests: pass.
- Covered cases: track + Main execution, stable target resolution, ambiguous
  target deferral, unsupported operation deferral, and post-apply failure
  rollback.

### Ableton working copy

`PROJECT_READY=VERIFIED` on the controlled development working copy:

- persisted project identity present;
- 34 tracks observed;
- terminal clean;
- no open journals or transactions.

The bounded execution smoke verified:

- 2 mix actions compiled and read back;
- 2 master actions compiled and read back;
- `direct_lucas_writes=0`;
- write authority `SafeWriteExecutor`;
- mix rollback verified;
- master rollback verified;
- project identity and track count unchanged;
- working-copy baseline restored.

The real Main capture was silent (`rms=0`, `peak=0`) before and after the
iteration. Therefore the audio recapture, DSP comparison, and Lucas critique
could not produce valid evidence in this run. This milestone is not marked
fully verified until the controlled working copy produces a non-silent Main
capture and the same bounded loop completes with read-only post-analysis.

## Current status

```text
MIXING_MASTERING_EXECUTION_V1
= EXECUTION_BOUNDARY_VERIFIED
= REAL_AUDIO_VALIDATION_BLOCKED_BY_SILENT_MAIN
```

No musical state was intentionally kept by this validation.
