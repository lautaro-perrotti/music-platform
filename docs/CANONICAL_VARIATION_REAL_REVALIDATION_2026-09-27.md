# CANONICAL_VARIATION_REAL_REVALIDATION_V1

Date: 2026-09-27. Controlled working copy: `pista_copilot_eval.als`.

## Result

The new code path was executed once in real Ableton:

```text
reference evidence
  -> CanonicalMusicModelViewV2
  -> MusicalVariationIntent
  -> MusicPlan
  -> ProductionCompiler
  -> SafeWrite
  -> Ableton
  -> authoritative readback
  -> isolated preview
  -> rollback
```

## Measured evidence

- Project identity: `3ca3fdc6c9c9db0e7e318ec5dc70d5a692935ec9493cdd32156e71f5327021d1`.
- Region: QN `160.0–192.0`, 8 bars, 167 BPM.
- Source: 23 reliable MIDI events in the region.
- Generated: 21 MIDI notes in Ableton.
- Two near-simultaneous source events were merged into a playable monophonic
  line; 7 eligible secondary onsets were shifted by a quarter beat.
- The generated pitch-class material stayed within the evidenced source pitch
  material. Exact event sequence equality was `false`.
- SafeWrite terminal state: `VERIFIED / KEEP`; 4 expected writes and 4
  authoritative readbacks (track, device, notes, arrangement clip).
- Arrangement readback: `arr:21:160.000000000:192.000000000:`.
- Preview: `HAS_SIGNAL`, 11.497 s, RMS `-21.13 dBFS`.
- Rollback terminal state: `ROLLED_BACK` / `rollback_verified=true`.
- Post-run read-only snapshot: same project identity, 21 tracks, no test
  variation track, transport stopped.

## Interpretation

This verifies that the canonical model and variation intent survive the real
execution boundary without bypassing Core. It does **not** claim that the
variation is musically better, nor that a rhythmic cell is perceptually
preserved; that requires human listening and/or a separately certified
re-analysis. Melody remains `INSUFFICIENT_EVIDENCE`.

No LLM/API call was made during this revalidation. The original working copy
remained untouched by the final state because the generated material was
rolled back.
