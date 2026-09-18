# ARCHITECTURE.md

Current product architecture. Historical intent: `docs/architecture/TARGET_ARCHITECTURE.md`.
Runtime kernel: `docs/architecture/PRODUCER_RUNTIME_V1.md`.
Read model: `docs/architecture/RPC_OPTIMIZATION_V2.md`.
Temporary host writes: `docs/architecture/ABLETON_MUTATION_PROTOCOL_V1.md`.

## Layers (allowed direction)

```
copilot.cli                 thin adapters
        ↓
copilot.runtime             Producer, graph, registry, OperationContext,
                            ProjectReadView, freshness, host_state
        ↓
copilot.audio               capture, bootstrap, project-ready, DSP, journals
copilot.reasoning           Astra prompt / provider / parse / grounding
copilot.daw                 AbletonTcpAdapter, protocol, tokens, timeouts
copilot.schemas             SessionState, EvidencePack, diagnosis
        ↓
Ableton Live (Remote Script TCP 127.0.0.1:9877)
Max for Live Copilot Audio Tap (TapProtocol 3, slots 0/1/2)
```

Runtime **wraps** canonical audio/reasoning functions. It does not fork them.

Models never hold a Live socket. `DawAdapter` is the mutation surface.

## AnalyzeProject graph (canonical)

`PROJECT_RESOLUTION` → `SESSION_READY` → `PROJECT_SNAPSHOT` → `PROJECT_READY`
→ evidence requirements → `READ_ARRANGEMENT` → `CAPTURE_MAIN` → `CAPTURE_SOURCES`
→ `FULLMIX_ANALYSIS` → `LOWEND_ANALYSIS` → `EVIDENCE_FUSION` → `ASTRA_REASONING`
→ `TERMINAL_VERIFICATION`.

Optional later nodes on the same graph: `READ_MIDI`, `READ_ROUTING`, `READ_DEVICES`.
Future tasks (references, mix, master) register as capabilities. They do not get
a second orchestrator.

## Live protocol

- One JSON object per request, one JSON object per response.
- Remote Script may run several LOM reads/writes **inside one scheduler turn**
  and return one payload. That is a bulk/compound **operation**.
- Concatenating several JSON requests on the socket is forbidden.
- Handshake advertises capabilities. Unknown commands are not probed on the
  wire if negotiation already says unsupported.
- Round trip ≈ 0.4–0.47 s under load because of Live's main-thread scheduler.

## State

- `SessionState` is DAW-independent. Track identity is `stable_id` / persistent
  ref, never a raw index as identity.
- Tokens: `PROJECT_STATE_TOKEN`, `AUDIBLE_STATE_TOKEN`, `TARGET_STATE_TOKEN`.
  See `docs/core/STATE_TRUST.md`.
- `ProjectReadView` is **operation-scoped**. Freshness is per domain + generation,
  not one giant cache flag. Timestamp is diagnostic only.

## Capture topology (frozen physical shape)

```
Master  → Copilot Audio Tap slot 0  → _next.wav          MAIN_FINAL
Copilot Capture      slot 1         → _next_kick.wav     TRACK_POST_MIXER, OFF_MIX_GRAPH
Copilot Capture Bass slot 2         → _next_bass.wav     TRACK_POST_MIXER, OFF_MIX_GRAPH
```

Two sources per playback pass. More sources ⇒ more passes until M4L outlets and
host tracks exist. Do not fake N-way capture.

## Observation vs production writes

Temporary host routing/monitoring/tap Rec/Slot are observation mutations.
They require durable pre-state, journals, restore, and terminal verification.
They must not be counted as musical writes.

Musical EQ, MIDI, automation, arrangement edits are a **different** certification
track. Do not reuse an observation MutationBatch for them without a new milestone.

## Recovery authority

Python/Core: journals, rollback, `IN_DOUBT`, restore.
Remote Script: execute ordered steps, return per-step reality.
One authority. No hidden Max undo.
