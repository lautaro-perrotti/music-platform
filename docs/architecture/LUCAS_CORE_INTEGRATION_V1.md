# LUCAS_CORE_INTEGRATION_V1

Status: `VERIFIED / FROZEN` on the controlled Groove Rider working copy.

This milestone connects the existing Lucas producer surface to Core's typed,
fail-closed execution boundary. It does not modify Lucas-owned musical logic.

## Ownership boundary

Lucas owns:

- `MusicPlan` construction and producer intent;
- sample-library retrieval and selection;
- arrangement/strategy metadata;
- advisory critique.

Core owns:

- reference/project/sample typed handoff contracts;
- project identity and state-token validation;
- explicit certified-action bounding;
- `ProductionCompiler`;
- `SafeWriteExecutor`, journals, readback, rollback and `IN_DOUBT` semantics;
- Ableton access and post-write analysis.

The stable Lucas entry points are `build_plan_from_prompt`,
`SampleSetContext`, and `critique_track`. The Core adapter invokes the planner
through `run_lucas_planner` and the advisory critic through
`run_lucas_critique`, without giving Lucas a DAW object or write authority.
`build_plan_from_prompt` is Lucas-owned code; Astra is an internal provider used
by that Lucas function, not a Core-side bypass.

## Typed handoff

`copilot.schemas.lucas_integration` defines `UserIntent`, `ReferenceContext`,
`SampleSetBoundary`, `ProjectContext`, `StyleContext`, and `LucasProducerInput`.
Reference and project tokens must be distinct and every context is `NO_WRITE`.
`build_reference_context` carries section, energy, low-end, groove, provider,
provenance, and limitation facts from the immutable Core pack.

`bound_plan_to_certified_actions` keeps the complete Lucas plan traceable. It
accepts only an explicit bounded subset and records all deferred or unsupported
actions; it never silently drops them.

## Real validation

The final live run used the controlled working copy and the real configured
Astra provider:

- Ableton `PROJECT_READY`: passed; 34 tracks before and after rollback;
- Lucas planner: Astra used, 52 actions returned;
- bounded execution: `CREATE_TRACK` + matching Lucas `SAMPLE_LOAD` accepted;
- compiler: both actions compiled;
- SafeWrite: both actions reached `KEEP`, authoritative readback matched;
- sample URI: resolved to the working copy's Live browser URI;
- execution writes: 2 musical writes attempted and 2 verified (`CREATE_TRACK`,
  then sample load), followed by 2 rollback mutations;
- post-write `Producer.analyze_project`: `SUCCEEDED`,
  `post-analysis MUSICAL WRITES = 0` (analysis is read-only);
- Lucas critique: advisory `improve`, no invented issue list;
- rollback: both transactions `ROLLED_BACK`, terminal identity unchanged;
- original project: untouched; only the manifest-backed working copy was used.

The real bridge loads samples as Simpler devices on MIDI tracks. When the
selected Lucas sample intent was paired with an audio-track plan, the Core
validation mapped the temporary execution host to MIDI/Simpler while preserving
the Lucas sample selection and action identity. This is an execution adapter,
not a change to Lucas's plan or musical logic.

## Action vocabulary boundary

Lucas's existing producer plan uses `SAMPLE_LOAD`. The frozen Core producer
execution surface is named `LOAD_SAMPLE`. The `ProductionCompiler` accepts the
Lucas plan action and emits the canonical SafeWrite mutation
`action_type="LOAD_SAMPLE"`; no second SafeWrite operation or second journal is
created. `SAMPLE_LOAD` is therefore an existing Lucas input vocabulary, while
`LOAD_SAMPLE` is the Core execution vocabulary.

## Frozen scope

The milestone does not add buses, routing expansion, MIDI composition,
automation, arbitrary plugins, or new DAW mutations. Deferred actions remain
visible in the bounded-plan report. Reopen only for a reproducible integration
bug.
