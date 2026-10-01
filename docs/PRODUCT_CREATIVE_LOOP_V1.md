# PRODUCT_CREATIVE_LOOP_V1

This branch consolidates the existing musical evidence into the first product
loop. It does not add an analyzer, provider, bridge, transaction system, or
write authority.

## Dependency map

```text
reference pack + reconciled Ableton MIDI + existing understanding
        [AUTHORITATIVE, READ-ONLY]
                         |
                         v
CanonicalMusicModelViewV2
        [DERIVED, READ-ONLY]
                         |
                         v
VariationIntent / MusicalVariationIntent
        [DERIVED CREATIVE INTENT, NO_WRITE]
                         |
                         v
existing reference-bound variation planner
        [DERIVED PLAN]
                         |
                         v
MusicPlan -> ProductionCompiler -> SafeWriteExecutor
        [EXECUTION-ONLY]
                         |
                         v
Ableton working copy -> authoritative readback -> preview
                         |
                         v
Studio review: KEEP / REGENERATE / DISCARD
```

The harmonic review HTML, analyzer reports, and persisted evidence packs stay
available as `DEBUG / HUMAN_VALIDATION` surfaces. They are not a second
product loop and are not required as the primary user interface.

## Canonical product path

1. Select the analyzed Rose Bass reference and region.
2. Reuse cached, identity-bound understanding.
3. Project it into `CanonicalMusicModelViewV2`.
4. Derive one deterministic `VariationIntent`.
5. Reuse the existing reference-bound variation planner.
6. Run symbolic validation before any write.
7. Compile through `ProductionCompiler`.
8. Execute only through `SafeWriteExecutor` in the working copy.
9. Verify track, clip, notes, arrangement, and device readback.
10. Capture an isolated preview with `HAS_SIGNAL` and duration checks.
11. Present the musical summary and allow `KEEP`, `REGENERATE`, or `DISCARD`.

## Traceability contract

The persisted variation provenance records:

- source reference/project and region;
- source and generated event counts;
- source-not-copied result;
- per-event source event, preserved properties, changed properties, and pitch
  support;
- symbolic validation result;
- canonical view and variation intent snapshots;
- source artifact hashes.

No new pitch or harmony certainty is invented. If harmonic evidence is
ambiguous, that limitation remains in the intent and user-facing summary.

## Acceptance inherited and extended on this branch

The controlled Rose Bass revalidation from
`docs/CANONICAL_VARIATION_REAL_REVALIDATION_2026-09-27.md` already verified the
real Ableton boundary, readback, preview, and rollback. This branch extends
that proven path with explicit symbolic validation, per-event traceability,
and persisted product-review summary fields. No new Live run is required to
prove a path that has already been executed and rolled back cleanly.

```ini
MODEL_API_CALLS = 0
UNSAFE_WRITES = 0
NEW_ANALYZERS = 0
WRITE_AUTHORITIES = 1
ORIGINAL_PROJECT_MUTATIONS = 0
```

The result is a bounded creative slice, not a claim of complete musical
quality. Human listening remains authoritative.
