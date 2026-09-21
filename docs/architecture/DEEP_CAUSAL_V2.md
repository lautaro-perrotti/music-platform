# DEEP_CAUSAL_V2

Status: `FOUNDATION / NOT VERIFIED`

This milestone adds a deterministic, read-only causal-evidence evaluator on
top of existing EvidencePack, MusicAnalysisPack, capture and routing facts.
It does not capture audio, call Ableton, invoke a provider, or authorize a
musical write.

## Graph boundary

The evaluator keeps the measured audio path separate from sidechain/control
paths:

```
SOURCE → DEVICES → POST_MIXER → GROUP/NESTED_GROUP → SEND/RETURN → MAIN

SIDECHAIN / CONTROL PATH  ≠  AUDIO PATH
```

`CausalContext` contains typed nodes, paths, before/during/after measurements,
candidate relationships, counterevidence, project identity, tokens and
generation. `CausalEvidence` records the deterministic checks and their
provenance.

## Grades

The evaluator supports `COINCIDENT`, `COMPATIBLE_WITH_CAUSE`,
`WEAK_CAUSAL_SUPPORT`, `STRONG_CAUSAL_SUPPORT` and
`CAUSALITY_UNRESOLVED`. Temporal precedence, directional change, path
validity, intermediate propagation, persistence and counterevidence are
separate fields. Identity/token/generation mismatches fail closed.

The existing `external_causal_context_v1` remains the source-specific adapter
for MIDI/routing context. `deep_causal_v2` consumes that kind of evidence; it
does not replace capture or DSP.

## Current validation

Deterministic tests cover a known causal chain, ambiguous evidence, stale
generation, mixed identity, invalid path, a separate control path, and the
EvidencePack adapter. All outputs assert `NO_WRITE` / `MUSICAL_WRITES = 0`.

This is not yet `VERIFIED`: real EvidencePack replay and the full regression
gate still need to be run before claiming closure.
