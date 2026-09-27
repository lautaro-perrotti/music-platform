# MULTI_ELEMENT_CREATIVE_LOOP_V1

This milestone adds the first evidence-bound composition bundle for a real
multi-element variation. It does not add a new analyzer and it does not write
to Ableton by itself.

## Current contract

```text
reference evidence
  -> BASS transformed MIDI
  -> DRUMS transient-timed support
  -> HARMONIC selected-chord support when a reviewed harmonic artifact exists
  -> one read-only MultiElementVariationBundle
```

The bundle is deliberately explicit about evidence quality:

- Bass uses the existing authoritative MIDI-bound variation path.
- Drums use transient locations only. The generated MIDI pitch is a generic
  percussion placeholder; the system does not claim it knows kick/snare/hat
  identity from a transient-only source.
- Harmonic notes are emitted only from selected chord hypotheses with supported
  pitch classes. Missing or ambiguous harmonic evidence remains
  `INSUFFICIENT_EVIDENCE`.

## Safety boundary

The bundle is `NO_WRITE` and contains no Ableton calls, no model calls, and no
new measurement authority. The next execution slice must compile each role
through the existing `ProductionCompiler -> SafeWriteExecutor` authority and
must provide a grouped preview before this milestone can claim real execution.

```ini
MULTI_ELEMENT_CREATIVE_LOOP_V1 = PLAN_CONTRACT_IMPLEMENTED
REAL_ABLETON_EXECUTION         = PENDING
HUMAN_MUSICAL_REVIEW           = PENDING
MODEL_API_CALLS                = 0
NEW_ANALYZERS                  = 0
```
