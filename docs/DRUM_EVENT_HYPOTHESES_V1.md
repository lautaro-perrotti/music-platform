# Drum event hypotheses V1

`copilot.audio.drum_events_v1` reuses the existing RMS-flux transient detector
and attaches local spectral measurements to each event. The bounded role
rules emit only:

- `KICK` when 20–150 Hz energy is at least 0.60 of measured spectral energy
  and spectral centroid is at most 300 Hz;
- `CLOSED_HAT` when 2–12 kHz energy is at least 0.85, 20–150 Hz energy is at
  most 0.05, and centroid is at least 3.5 kHz;
- `UNKNOWN` otherwise.

These are deterministic, uncalibrated heuristics. The numerical `confidence`
is deliberately `null`; rule scores are not probabilities. Every inferred
role carries its rule ID, feature evidence and limitations. Stem purity is not
proven by a role hypothesis. Human correction is stored separately from the
machine hypothesis and becomes the event's effective role without deleting
the original inference; the correction increments an event revision and
appends a change record.

Synthetic detector calibration is separately recorded as
`SYNTHETIC_PROTOTYPES_ONLY`. It is not applied to the real source's onset
times; source microtiming remains provisional until source-specific validation
exists. Tempo, meter and grid origin remain independent hypotheses.
