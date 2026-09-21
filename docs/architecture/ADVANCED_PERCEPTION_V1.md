# ADVANCED_PERCEPTION_V1

Advanced perception is a read-only provider boundary above the existing
`MusicAnalysisPack`. It adds observations to the EvidenceGraph; it does not
replace Physical DSP, Music Analyzer, EvidenceGraph, Astra, or producer
planning.

```text
MusicAnalysisPack
        |
        +-- local-music-analyzer provider (available)
        +-- CLAP embedding provider (availability-gated)
        +-- semantic-ear provider (availability-gated)
        |
        +--> PerceptionObservation[]
        +--> EvidenceGraph fusion
        +--> ReferenceIntentBundle (read-only)
```

The local provider exposes existing Analyzer facts through a replaceable
provider contract and adds bounded structural observations such as
`section.non_template_boundary`. It does not invent source identities or
convert observations into production instructions.

Unavailable CLAP or semantic-ear providers produce explicit provider
limitations. The deterministic embedding stub is not treated as semantic.
Contradictory provider observations remain `CONTRADICT` in fusion output;
the system never selects one as ground truth merely to make a result pretty.

`ReferenceIntentBundle` stores distinct `REFERENCE_STATE_TOKEN` identities,
optional role/feature bindings, provenance, and conflicts. It never merges
facts from different references and never contains raw audio.

Current honest terminal state on this machine:

```text
ADVANCED_PERCEPTION_V1 = PROVIDER_LIMITED
```

The local provider is real and tested against the real Analyzer output. CLAP
and a semantic-ear model are not installed/configured, so semantic similarity
and semantic source/function descriptions remain unavailable.
