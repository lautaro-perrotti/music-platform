# MULTI_VARIATION_V1

This milestone extends the existing Rose Bass variation slice from one
candidate to a bounded request of `1`, `3`, or `5` candidates. It reuses the
same reference evidence, canonical musical model, VariationIntent,
ProductionCompiler, SafeWriteExecutor, preview capture, and review state.

## Contract

```text
one persisted Rose Bass reference
        -> one request: variations = 1 | 3 | 5
        -> deterministic variation strategy per index
        -> independent MIDI track / arrangement / preview
        -> independent KEEP / DISCARD
```

The first strategy is unchanged from the previously validated slice. The
additional strategies are deterministic and evidence-bound:

1. secondary onset movement within bars;
2. alternate earlier secondary onsets;
3. half-beat movement plus rotation of evidenced pitch assignment;
4. reverse evidenced pitch assignment plus alternate onsets;
5. reduced density plus rotated evidenced pitch assignment and larger onset
   movement.

No pitch outside the source evidence is introduced. Every candidate is checked
for bounded duration, valid MIDI range, source-supported pitches, phrase
length, and non-identical event signature before compilation. The generated
record persists the strategy, symbolic result, source-not-copied result, and
per-event traceability.

## Safety

Each candidate remains an independent Copilot-owned track and uses the same
single write authority:

```text
VariationPlan -> ProductionCompiler -> SafeWriteExecutor -> Ableton
```

There is no batch transaction or second journal authority. A later candidate
does not rewrite an earlier one. `KEEP` and `DISCARD` remain per variation.

## Validation status

Offline tests verify that all five strategies are deterministic, distinct, and
source-bound, and that a multi-variation request produces the expected indexed
records. The previous real one-variation Ableton run remains the execution
baseline. A new 5-candidate Live run must be performed only after machine
storage is safe; the last measured host had approximately 252 MB free on C:,
which is not an acceptable condition for a multi-preview run.

```ini
MULTI_VARIATION_V1 = IMPLEMENTED / REAL_VALIDATION_PENDING_STORAGE
MODEL_API_CALLS = 0
NEW_ANALYZERS = 0
WRITE_AUTHORITIES = 1
```
