# MUSICAL_UNDERSTANDING_COMPLETION_P0

This milestone consolidates existing read-only musical evidence and connects
it to the already certified one-variation execution path. It does not add an
analyzer, a second truth model, an LLM call, or a write authority.

## Current evidence matrix

| Domain | Existing authority | Product status | Limitation |
|---|---|---|---|
| Bass notes / intervals | reconciled Ableton MIDI in `MusicalUnderstanding` | SUPPORTED when identity reconciliation passes | fail closed on stale or ambiguous identity |
| Bass motifs / phrases | `BassMusicalModel` over the same understanding artifact | SUPPORTED when events/phrases exist | structural similarity, not aesthetic judgment |
| Groove | `MusicalUnderstanding.drums` plus bass rhythmic structure | SUPPORTED or INSUFFICIENT_EVIDENCE | deterministic descriptive facts only |
| Bass ↔ drums | existing `BassDrumsRelationship` | SUPPORTED or INSUFFICIENT_EVIDENCE | relationship is descriptive, not causal |
| Melody | no authoritative melody source attached to this slice | INSUFFICIENT_EVIDENCE | no melody is invented from bass data |
| Arrangement | existing `ArrangementEnginePlan` / `TrackSpec` when supplied | SUPPORTED, EVIDENCE_ONLY, or NOT_AVAILABLE | producer intent is not audio measurement |
| Harmony | existing `HarmonicUnderstanding` / `MusicAnalysisPack` | unchanged | human sanity review remains separate |

## Integration boundary

```text
existing analyzers/artifacts (NO_WRITE)
        -> CanonicalMusicModelViewV2
        -> MusicalVariationIntent (deterministic, NO_WRITE)
        -> existing VariationPlan / MusicPlan
        -> ProductionCompiler
        -> SafeWriteExecutor
        -> Ableton readback / preview
```

The bounded creative operation is one 8-bar bass variation. It retains
authoritative evidenced pitches and moves eligible secondary onsets inside
their bars. The generated sequence must differ from the source; if no safe
change can be made, the planner rejects the operation instead of pretending a
variation exists.

## Truthful acceptance boundary

Automated tests prove the consolidated view is read-only, preserves evidence
references, exposes groove/motif/relationship domains, reports melody as
insufficient when no source exists, and reaches the existing variation planner
without bypassing compiler/SafeWrite.

`MUSICAL_UNDERSTANDING_COMPLETION_P0` may only be marked `VERIFIED` after a
real controlled-working-copy run records: source evidence, intent, generated
note count, a non-identical sequence comparison, SafeWrite/readback, preview,
rollback/KEEP semantics, zero direct writes, and untouched original state.
