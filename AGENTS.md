# Project contract — Ableton copilot

This file is repository truth for agents working in `music-platform`.
It is engineering law, not a ticket board.

Longer maps live next to it:

- `docs/CURRENT_STATE.md` — measured now
- `docs/ARCHITECTURE.md` — packages and allowed dependency direction
- `docs/CAPABILITY_MAP.md` — what exists, what is frozen, what is forbidden
- `docs/SAFETY_INVARIANTS.md` — fail-closed rules that survive optimization
- `docs/PERFORMANCE_POLICY.md` — how to spend seconds
- `docs/DEFINITION_OF_DONE.md` — when a milestone may say VERIFIED
- `docs/ROADMAP_100.md` — sequenced work on this runtime, not a second product

Factory sharing rules (skills, kernel of the factory itself) live in `ia-factory`.
This repo is the product. Tenancy data, API keys, and client `.als` files never enter git.

## Architecture

```
CLI / agent intent
    → copilot.runtime (Producer, graph, capabilities, OperationContext)
        → copilot.audio (capture, DSP, project-ready, journals)
        → copilot.daw (AbletonTcpAdapter, protocol, State Trust)
        → copilot.reasoning (Astra: prompt / HTTP / parse / grounding)
        → copilot.schemas (EvidencePack, SessionState, diagnosis)
```

Allowed direction: runtime orchestrates; it does not reimplement capture/DSP/Astra.
`DawAdapter` is the only Live mutation surface. Models never call Live.

Public analysis API:

```python
from copilot.runtime import Producer
result = producer.analyze_project(project)
```

CLI: `python -m copilot.cli analyze-project "<folder>"`.
`producer-analyze` is a debug path.

## Domain rules

- Core knows state. Astra proposes. No musical fact exists because the model said it.
- Analyze is read-only on the audible mix unless the human explicitly requests a write path.
- Temporary capture-host routing is not a musical write. It must still restore exactly.
- `MUSICAL WRITES` on AnalyzeProject is 0.
- `PRE_ROLL_QN = 16` is frozen. Do not shorten it for wall time.
- Capture width is discovered. TapProtocol 3 = 2 sources + Main. TapProtocol 4 ≤ 8 sources + Main. Do not hardcode project-specific host counts.
- A newly created Live track is not a Copilot capture host. Pool capacity counts only `HOST_AVAILABLE` after canonical parked state is applied and freshly verified. Live `Ext. In` + `Main` is initialization input, not rollback baseline.
- Alignment claim is `LIMITED ±52 ms`. Do not advertise sample accuracy.
- Constant tempo only. Tempo automation is fail-closed.
- `PROJECT_MISMATCH` / `TARGET_AMBIGUOUS` / `IN_DOUBT` fail closed. No best-guess identity.
- One JSON TCP request, one JSON response. Never concatenate protocol frames.
- No speculative concurrent Live RPCs. Local work may overlap an in-flight RPC only if it does not depend on the result.
- Remote Script receives a whitelist of typed operations. No eval, no LOM expressions, no LLM-supplied method names.

## Frozen (reopen only for a reproducible bug)

| Milestone | Meaning |
|---|---|
| `LIVE_SESSION_READINESS_V1` | Port ≠ session. Handshake + identity required. |
| `CAPTURE_BATCHING_V1` | 2 sources/pass + Main. Slot 0 reserved. |
| `PRODUCER_RUNTIME_V1` | One high-level AnalyzeProject call. |
| `RPC_OPTIMIZATION_V2` | Freshness domains + bulk reads. Stop squeezing reads. |
| `ABLETON_MUTATION_PROTOCOL_V1` | Compound temporary-host mutations. Sequential fallback kept. |
| `CAPTURE_HOST_BASELINE_V1` | Canonical parked state. Live new-track defaults are not baseline. |
| `CAPTURE_SCALABILITY_V2` | Discovered pool; Groove Rider 4-source one-pass + Main. |
| `EVIDENCE_SYSTEM_V2` | EvidenceGraph above immutable packs. Fusion keeps contradictions. Timestamp is not freshness. |
| `PHYSICAL_DSP_V2` | Factual measurement layer. No mix-quality judgments. |
| `SAFE_WRITE_FOUNDATION_V2` | Generic PLAN→readback→KEEP/ROLLBACK. `SET_TRACK_VOLUME` only certified musical action. |
| `FOUNDATION_INTEGRATION_CHECKPOINT_V1` | DSP + Evidence + Safe Write in one repo state. AnalyzeProject stays read-only. |
| `ASTRA_REASONING_V2` | Evidence-native diagnosis over EvidenceView. Astra interprets; it is never measurement authority. |
| `PRE_ROLL_QN` | 16 quarter notes. |

Deferred: `RUNTIME_LAUNCH_PATH_OPTIMIZATION`. Do not kill the user's Live session to measure it.

## Coding conventions

- Prefer wrapping a canonical function over copying it into the runtime.
- Capture journals stay per-host. Compound transport must not collapse them.
- `ProjectReadView` is operation-scoped. Do not share mutable authoritative views across unrelated analyses.
- Timestamp is never freshness authority. Generations + project identity + tokens are.
- Vendor Remote Script changes require handshake capability ads. Do not infer support from Live version. Keep sequential fallback.

## Testing

```
python -m pytest
```

Gate is the non-environmental suite. Three Live-environment tests fail when Live is up with leftover `AI Test` / when a probe expects Live down. Do not “fix” them by mocking success.

Precision parity is mandatory for capture/RPC/mutation optimizations: same project, region, identities, routing, monitoring, sends, devices, captures, classifications, terminal. Timing metadata may differ. Astra wording may differ if the grounded status is unchanged.

## Performance

Budget **one Live round trip ≈ 0.4–0.47 s**. Design for round-trip count, not payload size.

Attribute every second: `INTRINSIC_AUDIO`, `ABLETON_RPC`, `MODEL`, `LOCAL_CPU`, `LOCAL_IO`, `WAIT`, `ORCHESTRATION`. Target `UNATTRIBUTED ≈ 0`.

Do not mix these frontiers in one milestone:

1. Ableton mutations (compound temporary host writes)
2. Astra provider latency (routing / evidence / model choice)
3. Intrinsic playback (needs more simultaneous hosts, not less pre-roll)

## Persistence

Durable pre-state and journals exist **before** Live mutation. Core owns rollback. Remote Script reports per-step reality. Do not build a second transaction system inside Max/Remote Script.

## APIs

Ableton protocol is versioned by handshake capabilities (`session.read`, future `COMPOUND_TEMPORARY_MUTATION`, …). Unknown commands must not be sent just to discover them if capability negotiation already says no.

## Security

Localhost only (`127.0.0.1:9877`). No secrets in git. Working-copy policy refuses originals (`pista.als` and equivalents). Do not operate on an unsaved/untitled set as if it were a durable project identity.

## Generated code

Do not hand-edit generated evidence packs to make a run look verified. Runtime > logs > docs when they disagree; then fix the stale layer.

## Commands

| Intent | Command |
|---|---|
| Analyze current or imported project | `python -m copilot.cli analyze-project "<folder>"` |
| Doctor / readiness | `python -m copilot.cli doctor` |
| Capability matrix | `python -m copilot.cli capabilities` |
| Offline regression | `python -m copilot.cli regression-v1` |
| Tests | `python -m pytest` |
