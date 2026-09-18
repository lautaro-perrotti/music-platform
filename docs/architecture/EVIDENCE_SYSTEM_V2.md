# EVIDENCE_SYSTEM_V2

Canonical `EvidenceGraph` + fusion + freshness **above** immutable `EvidencePack`s.

Packs stay the on-disk artifact (`evidence_pack_v1.json`). This milestone does
not rewrite pack JSON. Runtime adapts a pack into typed nodes, then reasons
over the graph.

Timestamp is never freshness authority. Generations, project identity, and
state tokens from `FreshnessClock` / `state_tokens.py` are.

## Boundary

```
EvidencePack (immutable, existing JSON)
        ↓  adapter (graph_from_pack)
EvidenceGraph (nodes, deps, fusion, validity)
        ↓  EvidenceView (scoped)
Astra / future perception
```

Core knows state. Astra proposes. An LLM result may never masquerade as
`MEASUREMENT`.

## Node contract

Every node exports (compatibly with the older `identity` / `kind` keys):

- stable evidence id
- evidence type
- subject identity
- region / time scope
- source artifact / reference
- provider / analyzer and version
- project identity
- relevant state tokens
- provenance
- dependencies (exact upstream ids, or a freshness domain name)
- quality
- confidence **components** (not a single invented percentage)
- limitations
- freshness / validity

## Kinds

Existing pack kinds stay valid: `MEASUREMENT`, `FACT`, `LIMITATION`,
`STATE_TOKEN`, `SESSION_ENTITY`.

Added, carefully: `OBSERVATION`, `RELATIONSHIP`, `INTERPRETATION`, `DIAGNOSIS`.

Interpretive kinds are not comparable to measurements.

## Dependencies and freshness

Derived evidence names exact upstream ids. If upstream is stale, derived is
stale. Project mutation invalidates only dependent evidence.

Immutable audio analysis may remain valid when `artifact_hash` is present
(`invalidate_for_domains` and `apply_freshness` skip those nodes).

Cross-project: identity mismatch → reject / do not mix nodes. `switch_project`
drops the prior graph.

## Fusion

Several sources on the same question: `AGREE` / `PARTIALLY_AGREE` /
`CONTRADICT` / `NOT_COMPARABLE`. Contradictory nodes are kept. Fusion never
overwrites a source.

## Limitations

First-class, and they propagate to dependents:

`ALIGNMENT_LIMITED`, `MIDI_UNAVAILABLE`, `AUTOMATION_UNREAD`,
`ROUTING_UNRESOLVED`, `CAPTURE_FAILED`, `MODEL_UNAVAILABLE`, `SOURCE_MISSING`.

Pack-era `MIDI_UNREAD` aliases to `MIDI_UNAVAILABLE` in the graph. The pack
file is unchanged.

## Requirements

Goals already sketched: `ENERGY_STRUCTURE`, `HARMONIC_CONTEXT`,
`KICK_BASS_RELATIONSHIP`, `SOURCE_ACTIVITY`, `DEVICE_CAUSAL_CONTEXT`.

`requirements_for_goals` returns **minimum acquisition capabilities**, not an
execution sequence. `inventory_for_goals` reports existing / valid / stale /
missing. The agent does not sequence capture.

## View

`EvidenceView` is scoped (goals, region, subject, kind, question). It keeps
support, counterevidence, limitations, and provenance. It does not dump
project history and it does not invent nodes.

## Do not

- Rewrite capture, FullMix, or LowEnd.
- Start `ASTRA_REASONING_V2`.
- Treat timestamps as freshness.
- Hallucinate evidence when a provider fails — record a limitation.
