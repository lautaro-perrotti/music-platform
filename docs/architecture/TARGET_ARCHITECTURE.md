# Target Architecture

## Core / LLM boundary

The LLM reasons and proposes. The Core knows real state, measures, authorizes,
executes, and verifies. No measurable musical fact exists because the LLM said
it. No change is successful because the LLM said it worked.

Ableton Live remains the musical engine. The Core owns session state, capture,
provenance, identity, transactions, and verification. GPT, Gemini, and other
models interpret and propose. They never own measurements and never execute
Live mutations directly.

KEEP / ADJUST / ROLLBACK is a Core policy result, not a model claim.

Do not start LIVE-4, automatic EQ, MIDI correction, or extra plugins until
capture provenance, identity, revision, and verification are consolidated.

---

Local-first copilot. Models are slots. The product is the loop.

```
USER INTENT
    -> SESSION STATE
    -> AUDIO OBSERVATION
    -> AGENT REASONING
    -> MUSIC PLAN
    -> DAW ACTIONS (typed tools only)
    -> NEW SESSION STATE
    -> NEW AUDIO OBSERVATION
    -> VERIFICATION
    -> USER
```

Never run an LLM in the Ableton audio callback.

```
ABLETON AUDIO THREAD
    -> buffer / tap          (BUILD; not in current bridges)
    -> worker
    -> feature extraction
    -> MusicObservation
    -> Agent
    -> command queue
    -> Ableton (main thread / Remote Script)
```

---

## Packages

```
src/copilot/
  runtime/      Producer Runtime (AnalyzeProject and future tasks)
  daw/          DawAdapter, Ableton TCP wrap, mock, identities
  schemas/      SessionState, MusicObservation, MusicPlan, AgentTransaction
  agent/        typed tools, transactions, vertical slices
  audio/        measure, offline render, compare
  models/       ModelRouter slots
```

---

## SessionState

Canonical, DAW-independent. Track identity is `stable_id`, never a raw index.

```
SessionState
  revision
  transport { tempo, time_signature, playing, position }
  tracks[]
    stable_id, name, role
    mixer { volume, pan, mute, solo, arm }
    clips[] { stable_id, slot_index, notes[] }
    devices[] { stable_id, parameters[] }
  selection
```

Index is a *current* locator resolved at apply time.

---

## MusicObservation

```
source, region, timestamp
signal { duration, rms, peak, lufs, crest, centroid, bass_ratio, stereo_width }
claims[] { MEASURED | INFERRED | HYPOTHESIS, confidence }
tempo_bpm / key   # only if confidence floors pass
```

The agent must not invent missing fields. Slice 2 leaves `key = None`.

---

## MusicPlan

```
goal, target, constraints
musical_intent
actions[] { operation, target, params }
validation_rules[]
expected_effect
```

Reasoning is not Ableton. Execution is `DawAdapter`.

---

## Agent tools (explicit)

Implemented now:

- `get_session_snapshot`
- `create_midi_track`
- `create_midi_clip`
- `replace_clip_notes`
- `set_mixer_volume`
- `set_device_parameter`

Next:

- `get_track` / `get_clip` / `get_clip_notes`
- `get_devices` / `get_device_parameters`
- `get_mixer_state`
- `create_automation`
- `capture_audio_segment` / `measure_track` / `render_selection`
- `begin_transaction` / `commit_transaction` / `rollback_transaction`

No `eval`, no shell, no raw LOM.

---

## Transactions

```
AgentTransaction
  transaction_id, user_intent, session_revision
  actions[] {
    target_stable_id, target_locator_at_apply, target_fingerprint,
    target_name_at_apply, operation, before, after, inverse
  }
  verification
  status: PLANNED | APPLIED | VERIFIED | ROLLED_BACK | FAILED | ROLLBACK_CONFLICT
```

Rollback: `stable_id` → current SessionState → validate fingerprint → current locator → inverse. Name is not identity. If the target is not unique: `ROLLBACK_CONFLICT` and no writes. Not Live's global undo.

---

## ModelRouter

```
conversation_model
planning_model
audio_caption_model
asr_model              Qwen3-ASR-0.6B
embedding_model        laion/larger_clap_music
music_generation_model ACE-Step v15-base
transcription_model    basic-pitch
separation_model       htdemucs
```

All replaceable. None of them own SessionState.

---

## Lab evidence (2026-09-14)

Source: `logs/live3r_perf2.json`, `logs/live3r_perf3.json`,
`devices/Copilot Audio Tap.maxpat`, deployed `.amxd`.

- Same WAV twice → same diagnosis. Instability is capture/alignment, not DSP.
- Single-tap off-main path works: Kick → Post Mixer → Copilot Capture →
  Monitor In → Sends Only → WAV. Other tracks were not rerouted.
  Through-master isolate was not used.
- `Sends Only` is not yet `OFF_MAIN`. The Remote Script can read send levels
  (`get_send_level`); the Python adapter does not wrap that read. Capture
  currently treats Sends Only as off-main without requiring sends at −∞.
- Slot exists in the source `.maxpat` and in the rebuilt `.amxd`
  (SHA-256 `a3c161ca36fa28897ebde3e54dc12aade16207e1b253e40eee8640d475a3c314`).
  Live instances still expose only `Device On` and `Rec`. Runtime inspection
  is the contract, not the source file.
- `stable_id` is session-scoped (`IdentityRegistry`). Kick was
  `trk_1d09da36d3b5` in PERF-2 and `trk_7fd288cbf602` in PERF-3. Restart
  mints a new id. Ambiguity does not yet fail closed as `IDENTITY_AMBIGUOUS`.
- `session.revision` is reported as `0` in both PERF logs. It is not yet a
  mutation fence.
- DSP is ~0.11–0.15 s. PERF-2 single-tap wall time was 71 s (capture 52 s);
  `get_track_info` was 83 of 158 TCP calls. Orchestration, not DSP, is the
  remaining cost.
- Context-removal views are diagnostic tests, not default PASS 1 observations.

## Next slice only

Tap protocol v2 must be proven in Live (`Rec`, `Slot`, `TapProtocol`) before
two-tap or PASS 1. Do not rebuild diagnosis, ModelRouter, or CI lab in the
same change.

---

## Out of scope (this phase)

Mobile, multiplayer, marketplace, plugin ecosystem, training foundation models, autonomous mastering, voice cloning, artist imitation, multi-DAW, Kubernetes, multi-tenant.
