# PERFORMANCE_POLICY.md

Binding for work that touches Live, capture, or Astra.
Principle doc: `docs/architecture/ABLETON_RPC_COST_PRINCIPLE.md`.

## Budget

One Ableton round trip ≈ **0.4–0.47 s** under load. Design for **count**, not
payload size. 174 KB topology vs 196 B session info is ~0.2 s, not 10×.

## Attribute every second

Required categories:

| Category | Meaning |
|---|---|
| `INTRINSIC_AUDIO` | Real-time playback we cannot compress without changing observation |
| `ABLETON_RPC` | Remote Script round trips |
| `MODEL` | Provider HTTP |
| `LOCAL_CPU` | DSP, parse, hash |
| `LOCAL_IO` | Files, journals |
| `WAIT` | Sleeps, probes (not playback) |
| `ORCHESTRATION` | Graph glue |

Target: `UNATTRIBUTED ≈ 0`. Current Groove Rider AnalyzeProject meets this.

## Three frontiers — never mix in one milestone

1. **Ableton mutations** — compound temporary host writes, per-step results.
2. **Astra** — routing, evidence compression, model choice. Grounding unchanged.
3. **Intrinsic audio** — more simultaneous hosts / one pass. Not a shorter pre-roll.

A slower Astra run must not hide a successful Ableton optimization. Report
capture/mutation RPC separately from total wall.

## Reads (frozen policy)

- Reuse a value only when project identity + domain generation + tokens match.
- Timestamp is never freshness authority.
- After mutation, required fields get a **fresh** consolidated readback.
- Prefer bounded bulk contracts (`get_tracks_info`, host-state snapshot) over
  `get_everything`.
- Do not pipeline Live RPCs. Local independent CPU may overlap one in-flight RPC
  only if measured overlap is worth the complexity (do not add for 20 ms).

## Writes

- Durable pre-state first.
- Compound request may reduce round trips. It may not reduce observability.
- Sequential fallback remains. Handshake advertises compound support.
- Do not batch musical production writes through the observation MutationBatch.

## Capture time

```
passes × (region_s + pre_roll_s) ≈ intrinsic audio
```

Groove Rider: 2 × (15.24 + 7.62) ≈ 45.7 s. That is expected. N-host topology is
the lever. `PRE_ROLL_QN = 16` is frozen.

## Launch path

`RUNTIME_LAUNCH_PATH_OPTIMIZATION` is separate. Do not recertify it by killing
the user's current Live session.

## Stop conditions

109 RPC after V2 is not a mandate to chase 0. Remaining writes and verifications
are safety. Do not squeeze reads for hundredths of a second.
