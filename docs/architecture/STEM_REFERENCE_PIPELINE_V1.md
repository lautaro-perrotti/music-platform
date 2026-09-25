# STEM_REFERENCE_PIPELINE_V1

This milestone enriches the frozen `ReferenceAnalysis` with deterministic,
local stem observations. It is read-only and has no Ableton access.

```text
immutable reference audio
  -> replaceable local StemProvider
  -> DRUMS / BASS / VOCALS / OTHER artifacts
  -> existing deterministic DSP
  -> same ReferenceAnalysis QN timeline
  -> StemReferenceAnalysis
```

The first worker validation used the already-installed local `demucs-infer`
runtime with cached `htdemucs_ft` checkpoints. The BS-RoFormer runtime remains
available as a provider, but its checkpoint could not load on this worker's
available host memory. Demucs GPU loading exceeded the worker's 6 GB VRAM, so
the accepted run used Demucs CPU with one job.

`TECHNICALLY_VALID` only means the files are readable, finite, duration-aligned
and analyzable. It does not verify perceptual purity, bleed, artifacts or
musical usability. Human stem review remains a separate gate.

Artifacts are cached by source hash, provider/model and analysis configuration.
The persisted contract contains provenance, hashes, dimensions, QN alignment,
factual observations and limitations. The original source is copied
immutably, never edited, and the run records `MODEL/API CALLS = 0` and
`MUSICAL WRITES = 0`.
