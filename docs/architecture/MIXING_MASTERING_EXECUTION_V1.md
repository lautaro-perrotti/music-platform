# MIXING_MASTERING_EXECUTION_V1

## Scope

Core-owned execution of one Lucas-compatible mix iteration and one
Lucas-compatible master iteration:

```text
grounded mix/master intent
  -> MusicPlan
  -> ProductionCompiler
  -> SafeWriteExecutor
  -> authoritative readback
  -> rollback or KEEP decision
```

Lucas planning and musical strategy remain unchanged. This boundary does not
add a second DAW writer, a second transaction authority, or new SafeWrite
action kinds.

## Strategy provenance

The bounded real Ableton validation in this milestone used an explicitly
constructed Core validation strategy fixture. It was not produced by
`build_plan_from_prompt` or `run_lucas_planner`. Therefore this milestone
certifies the execution and audio-verification boundary, not autonomous Lucas
mix/master planning. `AUTONOMOUS_PRODUCER_ALPHA_V1` must prove the complete
reference/project/sample/intent path and use real Lucas planner output.

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

The first validation exposed a concrete bug: the mix/master helper always
requested `0..16` beats, while the controlled working copy's first active
arrangement material began later. Live and the tap were healthy; that region
was simply silent. The fix is a generic evidence-backed active-region selector
using persisted arrangement clip spans intersected with authoritative mute/solo
state. It does not use project names or fixed song positions.

After the fix, the selector chose a 16-beat region with 100% clip coverage and
19 active tracks. Real Main captures were non-silent:

- pre: RMS `0.190420`, peak `0.483027`;
- after mix: RMS `0.176206`, peak `0.483027`;
- after master: RMS `0.190414`, peak `0.483027`.

The mix change therefore had a measurable compatible Main consequence
(`delta RMS=-0.014214`). Master readback and rollback also passed. Physical
DSP, Music Analyzer, and Advanced Perception all processed the real WAV; CLAP
was available and returned a read-only observation.

The configured Astra critique provider (`gpt-6-astra`) timed out on the real
critique request. No verdict was invented and both phases were rolled back.
Core has bounded provider failover around the unchanged Lucas critique
contract, and that failover is covered by focused tests. On this host only one
compatible provider was discoverable, so the offline MIX and MASTER closure
attempts both ended with typed `CRITIQUE_PROVIDER_UNAVAILABLE` /
`MODEL_TIMEOUT`; no alternate provider was available to try. Critique remains
provider-limited; it does not invalidate the verified execution/audio boundary.

The post-audio physical DSP, Music Analyzer, and CLAP-based Advanced
Perception observations were independently verified as read-only. The
Music Flamingo semantic-ear provider remains unavailable.

## Current status

```text
MIXING_MASTERING_EXECUTION_V1
= VERIFIED / FROZEN

LUCAS_POST_CHANGE_CRITIQUE_PROVIDER
= PROVIDER_LIMITED

MIX_STRATEGY_PROVENANCE
= CONTROLLED_FIXTURE

MASTER_STRATEGY_PROVENANCE
= CONTROLLED_FIXTURE
```

No musical state was intentionally kept by this validation.
