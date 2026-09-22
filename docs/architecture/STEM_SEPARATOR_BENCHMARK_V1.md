# Stem Separator Benchmark V1

The separator benchmark treats a model output as a candidate, not as a
production stem:

```text
immutable source
  -> separator provider
  -> StemCandidateSet
  -> objective validation
  -> anonymous listening bundle
  -> human stem-by-stem selection
  -> SelectedStemSet (future)
  -> ProductionCompiler / SafeWrite (future)
```

`TECHNICALLY_VALID` means only that the file is readable, finite, aligned in
duration, and usable for objective diagnostics. It does not mean clean,
isolated, musical, or commercially usable. Core never creates a subjective
quality score and never selects a winner.

The benchmark keeps runtime/code licensing separate from checkpoint/weights
licensing. Unknown or unasserted weight terms remain explicit in the
checkpoint manifest and are not certified for commercial distribution.

Broad comparison targets are `drums`, `bass`, `other`, and `vocals`.
Taxonomy is not forced into six artificial stems; specialist extraction is a
later phase. Ground-truth SDR/SIR/SAR/SI-SDR are unavailable for a song whose
original hidden stems are not known.

The listener-facing bundle contains only anonymous `candidate_A`...
identifiers. The mapping and gain-only normalization record are private and
stored separately. Ableton remains `HOLD` until a human creates a
`StemSelectionDecision`; this benchmark has zero musical Ableton writes.

