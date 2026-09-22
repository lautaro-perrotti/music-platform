# External source and separation boundary v1

This boundary handles a user-authorized external audio asset without turning
the source into a hidden project or a second Ableton write path.

```text
explicit import locations
        ↓ exact-one discovery (otherwise abstain)
immutable source copy + hash + rights/provenance
        ↓
specialist separator provider (isolated runtime)
        ↓
validated stem manifest
        ↓ only after source/rights/technical checks
Music Analyzer / EvidenceGraph / working-copy import
```

## Current provider audit

`bs-roformer-infer` is the selected adapter boundary for the first specialist
separation attempt. Its current upstream documents an inference-only,
SHA-256-verified model registry, a recommended six-stem BS-RoFormer-SW model,
and configurable external model storage. The adapter does not download model
bytes itself and reports `PROVIDER_UNAVAILABLE` until the runtime is installed.

The original Meta Demucs repository is archived and explicitly no longer
maintained, so it is not the primary new integration. `audio-separator` remains
a possible alternate runtime, but is not silently substituted and is not a
quality certification.

## Safety rules

- Discovery searches only Downloads/Desktop and explicitly configured import
  directories; multiple recent files produce `SOURCE_SELECTION_REQUIRED`.
- Source bytes are copied and hash-checked before analysis. The original is
  not edited.
- Unknown rights remain unknown. No watermark removal or rights claim is made.
- Separation and analysis are read-only and have no Ableton access.
- Weights belong outside git and should use the worker's external model cache.
