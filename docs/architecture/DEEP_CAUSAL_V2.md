# DEEP_CAUSAL_V2

Status: `VERIFIED / FROZEN`

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

Deterministic tests cover known-causal, ambiguous, negative-direction,
intentional-silence, stale generation, mixed identity, invalid path, parallel
direct/return paths, a separate control path, and the EvidencePack adapter.
All outputs assert `NO_WRITE` / `MUSICAL_WRITES = 0`.

The controlled working copy supplied a real EvidencePack, a real
MusicAnalysisPack, a live read-only SessionState graph and existing factual
event rows. The pipeline produced:

```
8 observed events
96 graph nodes
11 audio paths
1 control path
88 generated candidates
18 candidates with source before/during/after measurements
all final grades honest: COINCIDENT or CAUSALITY_UNRESOLVED
MUSICAL_WRITES = 0
```

The unresolved results are expected: the available real evidence contains
source-level measurements but no matched Main event measurement sufficient to
claim propagation. No causal relationship was fabricated. `regression-v1`
passed 34/34. The full suite retains the three documented Live-environment
failures when Live is intentionally open with `AI Test` present.
