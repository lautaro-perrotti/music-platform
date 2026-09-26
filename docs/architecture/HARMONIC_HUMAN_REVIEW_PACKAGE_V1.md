# HARMONIC_HUMAN_REVIEW_PACKAGE_V1

This package is a read-only handoff from harmonic inference to a human
listener. It does not change `HARMONIC_UNDERSTANDING_V1` and it cannot certify
musical correctness by itself.

The builder reuses the exact persisted harmonic windows, validates available
source hashes, and creates deterministic local slices:

```text
window_01_master.wav
window_01_other.wav
window_01_bass.wav
window_01_master_context.wav
...
window_08_*.wav
```

It also writes:

- `harmonic_sanity_check_v1.json` — typed review contract;
- `harmonic_sanity_check_v1.txt` — concise listening checklist;
- `harmonic_sanity_check_v1.html` — static local review page.

Every window starts with `human_verdict = PENDING`. Allowed human verdicts are
`ACCEPT`, `PLAUSIBLE_AMBIGUOUS`, `WRONG`, `UNKNOWN_CORRECT`, and
`UNKNOWN_SHOULD_RESOLVE`. The builder never selects one.

The package records analysis and listening regions separately. Context padding
is for listening only and never changes the original evidence boundaries.
Global tonality remains whatever the harmonic artifact says; no key or mode is
forced during review preparation.
