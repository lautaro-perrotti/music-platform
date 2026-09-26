# ARCHITECTURE_CONSOLIDATION_V1 — audit checkpoint

Date: 2026-09-26  
Status: **AUDIT COMPLETE / NO BEHAVIORAL REFACTOR YET**

This document is an inventory of the current musical-production architecture.
It does not introduce a new authority, change SafeWrite, change State Trust,
or delete any verified capability.

## Target product shape

```text
REFERENCE / PROJECT
        ↓
PERCEPTION / EVIDENCE
        ↓
CANONICAL MUSIC MODEL (not yet introduced as a new runtime authority)
        ↓
PRODUCER DECISION + USER INTENT
        ↓
MusicPlan
        ↓
ProductionCompiler
        ↓
SafeWriteExecutor
        ↓
Ableton
        ↓
authoritative readback / preview / human decision
```

The current implementation already has a coherent execution boundary. The
consolidation gap is above that boundary: several valid musical artifacts are
now projected through a thin canonical product-facing view, while the domain
artifacts remain authoritative for their own facts and inferences.

## Factual inventory

| Component | Purpose | Input | Output | Authority / state | Consumers | Product role | Overlap |
|---|---|---|---|---|---|---|---|
| `ReferenceAnalysisPack` | Factual full-mix/reference analysis: energy, spectrum, structure candidates, groove/chroma/timbre facts and evidence refs | `AudioAnalysisInput`, observations, captured or file WAVs | `copilot.schemas.reference_analysis.ReferenceAnalysisPack` / analyzer evidence | Derived, read-only, stateless builder | `music_analyzer`, Astra context builders and downstream evidence consumers | Internal perception artifact | Overlaps with other perception artifacts only at the evidence boundary; it does not contain symbolic bass/harmony truth |
| `MusicAnalysisPack` | Current shared evidence handoff for local/reference/project audio analysis | `AudioAnalysisInput`, `ReferenceAnalysisPack`, DSP observations, source evidence | `copilot.schemas.music_analysis.MusicAnalysisPack` | Derived, read-only, stateless pack | Canonical view, reference-variation integration and Evidence/Astra paths | Internal evidence source; exposed through the canonical view | Current broadest factual pack; not a replacement for symbolic MIDI-derived artifacts |
| `StemReferenceAnalysis` | Source/separation pipeline metadata, alignment, stems and technical separation observations | Immutable source WAV, separation provider/cache, timeline metadata | `copilot.music_source.stem_reference.StemReferenceAnalysis` | Derived, read-only, persisted | `musical_understanding_v1`, stem review and benchmark flows | Internal source-analysis artifact | Can be confused with `MusicAnalysisPack`; its job is source/stem provenance, not the canonical musical model |
| `MusicalUnderstanding` | Deterministic symbolic/audio musical extraction: note candidates, rhythm, phrases, pitch material, drums and tonal hypotheses | Stem/reference audio, `StemReferenceAnalysis`, optional authoritative MIDI | `copilot.schemas.musical_understanding.MusicalUnderstanding` | Derived, read-only, stateless analysis result | `BassMusicalModel`, harmonic analysis and comparison tests | Internal perception artifact | Its pitch/rhythm fields overlap conceptually with the broad pack but have different evidence granularity |
| `BassNoteEvidence` | Typed bass-note evidence alias (`BassPitchEvent`) | MIDI/audio note extraction | Individual note events with onset, duration, pitch, confidence/evidence | Derived facts; no independent authority | `MusicalUnderstanding`, `BassMusicalModel`, harmonic analysis | Internal evidence primitive | Not a competing model; should remain a leaf evidence type |
| `BassMusicalModel` | Symbolic bass interpretation: pitch material, intervals, rhythmic cells, motifs and phrases | Reconciled `MusicalUnderstanding` / authoritative MIDI evidence | `copilot.schemas.bass_musical_model.BassMusicalModel` | Derived, read-only, persisted artifact | `HarmonicUnderstanding`, reports and future producer/reference planners | Internal musical-model substructure | Candidate submodel of a future canonical `MusicModel`; currently exposed as a standalone artifact |
| `HarmonicUnderstanding` | Ranked chord/tonality/harmonic-rhythm and bass↔harmony relationships | `BassMusicalModel`, chroma from optional technical stem, evidence refs | `copilot.schemas.harmonic_understanding.HarmonicUnderstanding` | Derived, read-only, persisted artifact; explicit abstention | Human review package and harmonic reports | Internal musical-model substructure | Candidate submodel of a future canonical `MusicModel`; must not promote technical stem purity to truth |
| `TrackSpec` | Validated user/provider musical intent: BPM, key, sections, roles, energy, palette, constraints and targets | User prompt or typed planner payload | `copilot.producer.track_spec.TrackSpec` | Intent, not measured truth; stateless validated contract | `ArrangementEngine`, Astra planner and MusicPlan construction | Product-facing producer intent | May look like a second model, but it is intentionally not perception; boundary must remain explicit |
| `ArrangementEnginePlan` / `ArrangementDecision` | Derives structural producer decisions from `TrackSpec` sections: timeline, role deltas, contrast, tension/release and groove focus | `SectionSpec` / legacy `Section` values | `copilot.musicplan.arrangement_engine.ArrangementEnginePlan` | Derived intent, stateless, no DAW writes | `astra_plan`, arrangement builders and MusicPlan paths | Internal producer-decision artifact | Overlaps with `TrackSpec` section data; `TrackSpec` is input intent, this is derived decision metadata |
| `ProducerState` | Persistent operational ledger for producer iterations, observations, decisions, issues and stop reason | Session/project identity plus producer events | `copilot.producer.state.ProducerState` persisted by `ProducerStateStore` | Stateful planning memory; not a transaction journal and not a DAW authority | Autonomous producer Alpha and producer runtime flows | Internal runtime state | Can be mistaken for musical truth; it records what the producer did, not what the reference contains |
| Reference-to-variation integration | Bridges accepted reference evidence/interpretation to one bounded new variation | `MusicAnalysisPack`, persisted interpretation, MIDI/reference features and user request | `VariationPlan`/`MusicPlan`-compatible actions and preview workflow | Derived planning adapter; no direct DAW write | Studio service → compiler | Product-facing vertical slice | Currently bypasses some richer bass/harmonic artifacts; this is a concrete consolidation seam, not a reason to duplicate them |
| `MusicPlan` | Canonical executable producer intent and action vocabulary | Producer decision, Lucas plan, variation planner, generated asset import | `copilot.schemas.musicplan.MusicPlan` | Plan contract; not a writer and not authoritative Live state | `ProductionCompiler`, gates, SafeWrite | Product-facing execution boundary | Correctly distinct from `TrackSpec`: intent is translated into executable typed actions |
| `ProductionCompiler` | Validates a `MusicPlan` against authoritative `SessionState` and compiles it into one SafeWrite intent | `MusicPlan`, `SessionState`, State Trust tokens | `ProductionCompileResult` / `MutationIntent` | Stateless compiler; never writes | Lucas/Core integration, Alpha, Studio, mixing/mastering | Core authority boundary | No competing compiler should be added |
| `SafeWriteExecutor` | Executes typed mutation intent with durable pre-state, authoritative readback, rollback and `IN_DOUBT` handling | Compiled `MutationIntent`, DAW adapter and journal | SafeWrite result, readback and rollback evidence | **Only musical write authority** | All certified production paths | Core execution authority | Frozen; no consolidation work should alter its semantics |
| Capture / preview | Captures project/reference audio and exposes reviewable artifacts | Ableton session or offline audio, capture contracts and working-copy state | WAVs, evidence/provenance, preview artifact records and HTML review packages | Capture is observational; preview is derived; both are read-only for musical state | Analyzer, Alpha, Studio, human review | Product-facing observation/review | Must remain separate from musical writes and from perception truth claims |

## What is already coherent

The production write path has one authority:

```text
Lucas / Producer intent
        ↓
MusicPlan
        ↓
ProductionCompiler
        ↓
SafeWriteExecutor
        ↓
Ableton readback + rollback
```

The audit found no justification to create another compiler, journal,
transaction manager, DAW writer, or protocol layer. SafeWrite, State Trust,
rollback and the Ableton protocol remain frozen.

The factual analysis boundary is also clear at the lowest level:

```text
audio / MIDI / project state
        ↓
read-only analyzers and evidence packs
        ↓
Astra may interpret grounded evidence
```

The model is not measurement authority, and the human review package keeps
harmonic correctness pending until it is actually listened to.

## Actual consolidation risks

### 1. Multiple valid musical roots

The repository currently has a broad factual `MusicAnalysisPack` and separate
symbolic artifacts for `MusicalUnderstanding`, `BassMusicalModel`, and
`HarmonicUnderstanding`. These are not duplicate algorithms, but they are
parallel product-visible concepts. Nothing currently enforces one root object
that references all of them.

### 2. Evidence and interpretation are adjacent but not uniformly nested

`MusicAnalysisPack` is the main Astra handoff. The bass and harmonic artifacts
are richer symbolic derivatives, but their relationship to the broad pack is
mostly by persisted files and typed inputs rather than one aggregate model.
This makes it possible for a future producer path to consume one artifact and
silently omit another.

### 3. Intent has two legitimate layers, but the distinction needs to stay explicit

`TrackSpec` expresses what the user wants. `ArrangementEnginePlan` derives
structural decisions from that intent. `MusicPlan` expresses executable typed
actions. These should not be collapsed into one giant schema, but the product
documentation should describe them as a deliberate sequence rather than three
competing producer brains.

### 4. Producer state is operational memory, not musical understanding

`ProducerState` stores iteration history, observations, decisions and stop
reasons. It must not become a second source of musical facts or a second
transaction system.

### 5. Reference-to-variation is the first concrete integration seam

The vertical slice already proves real reference → plan → SafeWrite → MIDI →
preview. Its next architectural improvement should consume a canonical
music-model view, not add another interpretation/track-spec/variation schema.

## Implemented consolidation boundary

Do not introduce a second orchestrator or rewrite existing artifacts. The
lowest-risk consolidation is the implemented **thin aggregate read model** in
`copilot.schemas.canonical_music_model` and
`copilot.music_model.canonical_view`:

```text
CanonicalMusicModelView
├── evidence_refs / provenance
├── timeline / structure
├── rhythm
├── pitch / notes
├── harmony
├── motifs / phrases
├── relationships
└── confidence / limitations
```

It references existing verified artifacts and projects only the fields needed
by the first producer consumer. It does not run analyzers, recalculate audio
facts, or select between conflicting hypotheses. It is read-only and is not a
new source of truth.

The first migrated consumer is `build_astra_producer_planner_context`: it now
receives `music_model` instead of the raw `reference_analysis` payload. A
token mismatch is rejected closed before the context is built.

## Explicit non-actions in this checkpoint

- No new `MelodyUnderstanding` implementation.
- No new harmonic/stem/pitch analysis.
- No model/API calls.
- No Ableton access or musical writes.
- No changes to SafeWrite, rollback, State Trust or protocol.
- No deletion or renaming of verified modules.
- No UI or provider work.

## Acceptance for this audit checkpoint

```text
INVENTORY_COMPLETE                 = YES
EXECUTION_AUTHORITY_COUNT          = 1
ANALYSIS_WRITE_COUNT               = 0
SAFEWRITE_SEMANTICS_CHANGED       = NO
VERIFIED_MODULES_DELETED           = NO
LLM/API_CALLS                      = 0
ABLETON_MUSICAL_WRITES             = 0
ARCHITECTURAL_REFACTOR             = THIN VIEW ONLY
FIRST CONSUMER MIGRATED             = ASTRA_PRODUCER_PLANNER_CONTEXT
NEXT JUSTIFIED MILESTONE             = HUMAN HARMONIC SANITY CHECK / THEN SECOND CONSUMER
```

