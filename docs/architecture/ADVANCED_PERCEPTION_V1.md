# ADVANCED_PERCEPTION_V1

Advanced perception is a read-only provider boundary above the existing
`MusicAnalysisPack`. It adds observations to the EvidenceGraph; it does not
replace Physical DSP, Music Analyzer, EvidenceGraph, Astra, or producer
planning.

```text
MusicAnalysisPack
        |
        +-- local-music-analyzer provider (available)
        +-- CLAP embedding provider (real, optional dependency)
        +-- semantic-ear provider (availability-gated / optional)
        |
        +--> PerceptionObservation[]
        +--> EvidenceGraph fusion
        +--> ReferenceIntentBundle (read-only)
```

The local provider exposes existing Analyzer facts through a replaceable
provider contract and adds bounded structural observations such as
`section.non_template_boundary`. It does not invent source identities or
convert observations into production instructions.

CLAP is implemented through the Transformers ClapModel boundary using the
laion/clap-htsat-unfused checkpoint, pinned by default to revision
`8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a`. It is loaded lazily, runs in
inference mode, and is cached per model/revision/device. Audio is decoded as float32,
folded to mono, resampled to 48 kHz, split into non-overlapping 10-second
windows, and L2-normalized. Long-audio scalar embeddings are the normalized
mean of window embeddings; window-level embeddings retain time provenance.
Audio/text comparisons require matching provider, model, revision, and
dimension; otherwise the result is NOT_COMPARABLE.

If CLAP or the semantic-ear provider is unavailable, the result contains an
explicit provider limitation. The deterministic embedding stub is never
treated as semantic.
Contradictory provider observations remain `CONTRADICT` in fusion output;
the system never selects one as ground truth merely to make a result pretty.

`ReferenceIntentBundle` stores distinct `REFERENCE_STATE_TOKEN` identities,
optional role/feature bindings, provenance, and conflicts. It never merges
facts from different references and never contains raw audio.

Current honest terminal state on this machine:

```text
ADVANCED_PERCEPTION_V1 = VERIFIED / FROZEN
```

The local Analyzer and CLAP providers are real and read-only. A controlled
project capture was analyzed with CLAP audio embeddings, real audio-to-audio
and audio-to-text comparisons, EvidenceGraph insertion, cache/provenance checks, and
MUSICAL_WRITES = 0. The optional semantic-ear provider remains unavailable;
that is an explicit limitation, not a fabricated description of source
function or prominence.

The selected checkpoint is recorded as Apache-2.0 in its model card. The
dependency is optional (pip install -e .[perception]); no model weights or HF
cache are committed to this repository.
