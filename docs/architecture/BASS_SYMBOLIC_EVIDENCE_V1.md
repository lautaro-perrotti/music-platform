# BASS_SYMBOLIC_EVIDENCE_V1

This correction makes bass note evidence source-aware and onset-conditioned.
It does not freeze tonal understanding by itself.

## Source policy

The analyzer accepts an optional persisted `MIDI_READ_ONLY_V1` pack. It uses
exact MIDI notes only when the project identity and persisted track/device
fingerprint reconcile. A matching display name or track index alone is not
authority. If reconciliation fails, the report records the concrete reason
and uses the cached BASS stem as audio-only evidence.

## Audio fallback

The fallback separates onset detection from pitch estimation:

1. detect local bass onsets with the installed deterministic onset analyzer;
2. analyze a post-attack region for each onset with pYIN;
3. preserve repeated same-pitch attacks as separate candidates;
4. mark low-confidence or unstable candidates `UNKNOWN`;
5. derive intervals and tonal hypotheses only from reliable notes.

The prior Rose Bass run produced 26 contiguous pYIN runs, but only 4 passed
the old 60 ms duration gate. The new run produced 38 onset candidates and 13
note regions, with 3 reliable notes and 10 explicit unknowns. Tonality remains
`INSUFFICIENT_EVIDENCE`; that is the correct result for this audio artifact.

No model/API calls, Ableton writes, or new source separation are involved.

