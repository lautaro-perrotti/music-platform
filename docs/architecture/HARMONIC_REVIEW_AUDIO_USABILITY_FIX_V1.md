# HARMONIC_REVIEW_AUDIO_USABILITY_FIX_V1

This is a listening-artifact repair only. It does not recalculate harmonic
evidence and does not touch Ableton.

The repaired package:

- identifies the original immutable reference WAV as `FULL_CONTEXT_REFERENCE`;
- verifies its hash against the persisted stem-analysis provenance;
- derives tempo from the persisted source-QN span and source duration;
- makes the coordinate rule explicit:
  `source_local_qn = project_qn - source_capture_start_qn`;
- creates `window_01_context.wav` through `window_08_context.wav` with up to
  one bar of listening context;
- applies only constant review gain to target approximately `-3 dBFS` peak;
- audits peak, RMS, dBFS, nonzero ratio, signal, and audibility for every
  context/OTHER/BASS copy;
- verifies relative paths referenced by the static HTML.

`HUMAN_AUDIBILITY` intentionally remains `PENDING`: valid files and signal
statistics do not equal a human listening verdict.
