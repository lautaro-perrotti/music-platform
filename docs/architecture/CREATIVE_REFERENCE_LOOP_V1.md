# CREATIVE_REFERENCE_LOOP_V1

Status: `IMPLEMENTED / NEEDS_REAL_3_VARIATION_REVALIDATION`

This milestone closes the product boundary for a bounded reference-to-creative
review loop. It reuses the existing reference-bound bass planner and execution
authority; it does not add an analyzer, provider, transaction system, or Live
save workaround.

## Current flow

```text
real reference MIDI evidence
  -> CanonicalMusicModelViewV2
  -> three deterministic reference-bound intents (A/B/C)
  -> VariationPlan / MusicPlan
  -> ProductionCompiler
  -> SafeWriteExecutor
  -> Ableton readback
  -> isolated preview capture
  -> human comparison
```

The three strategies are explicit and evidence-bound. They are not random
seeds and they never introduce pitches outside the authoritative source
material. Each record persists event traceability, preserved/changed
properties, symbolic validation, source digests, and `source_not_copied`.

## Musical selection versus disk persistence

The review action is `select`, not `keep`:

```text
SELECT A/B/C
  -> musical_decision = MUSICAL_ACCEPTED
  -> persistence_status = DISK_SAVE_REQUIRED
  -> musical_writes = 0
```

Selection does not call Ableton, delete candidates, or claim that the `.als`
file was saved. The existing `keep` action remains the durable persistence
boundary and continues to fail closed while no verified `session.save`
capability exists. `Reject all` is only a thin composition of the existing
Copilot-owned rollback actions.

## Verification

- Focused Studio/variation/persistence tests: passing.
- Full Python regression: passing; only existing warnings.
- `produce.js`: Node syntax check passing.
- Real three-candidate Ableton revalidation: still required before marking
  this milestone `VERIFIED`.
- Human musical quality decision: remains a product review boundary.

No model/API calls or Ableton mutations were made by the implementation
validation itself.
