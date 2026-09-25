# MUSICAL_UNDERSTANDING_V1

This milestone converts the already-cached BASS and DRUMS artifacts from
`STEM_REFERENCE_PIPELINE_V1` into deterministic musical evidence. It is
read-only and does not call Astra, any other model, Ableton, or a provider.

The artifact contains:

- the authoritative reference timeline and raw onset timing alongside nearest
  quarter/eighth/sixteenth-grid positions;
- bass pitch events from the locally available `librosa.pyin` analyzer,
  including confidence and `UNKNOWN` events when the estimate is unstable;
- one normalized note-evidence shape is used for both authoritative
  `MIDI_READ_ONLY_V1` notes and onset-conditioned audio notes;
- ranked tonic/mode hypotheses, with `INSUFFICIENT_EVIDENCE` when the bass
  does not support a responsible collapse to one tonality;
- intervals, rhythmic density, timing deviation, periodicity candidates and
  structural eight-bar phrase comparisons;
- unlabeled drum transient events, pulse/subdivision facts and periodicity;
- descriptive bass/drums coincidence and displacement measurements;
- source hashes, analyzer provenance, and explicit `model_api_calls=0` and
  `musical_writes=0` invariants.

The implementation deliberately does not label individual drum instruments,
make aesthetic judgments, infer causality, or turn the result into a
`VariationPlan`. Stem reconstruction is also not treated as proof of
perceptual source purity; that remains a separate human-review concern.

When an ALS reference is supplied, the MIDI path is accepted only after the
persisted project and track identity reconcile. A display-name/index match is
not sufficient. The current Rose Bass artifact records the exact ALS path but
its persisted device fingerprint no longer matches that file, so this run
reports `MIDI_TRACK_IDENTITY_UNRESOLVED` and uses the audio fallback rather
than guessing the track.

Real artifact paths used for the first run are persisted outside the repo in
the existing runtime project under `stem_reference_v1/`.
