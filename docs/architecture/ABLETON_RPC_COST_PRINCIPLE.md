# Architectural principle: every Ableton round trip costs ~0.37–0.47 s

Status: **binding for all new features touching Live**
Measured: 2026-09-17, live session on `127.0.0.1:9877`, vendored AbletonMCP
remote script, Live running with a 32-track project.

## The measurement

60 consecutive `health_check` calls — a command that does no work — landed in
exactly four latency buckets:

```
0.2133   0.3196   0.4265   0.5335      spacing 0.1067 s, samples between buckets: 0
n=60   min 0.213   p50 0.426   mean 0.368   p95 0.427
```

Under load, in a traced `producer-analyze` (237 round trips), the mean rose to
**0.467 s**.

The spacing is Live's main-thread scheduler tick. The remote script marshals
every command onto the audio/UI thread — `schedule_message(0, main_thread_task)`
then `response_queue.get(timeout=10.0)`
(`vendor/abletonmcp_remote_script/AbletonMCP/__init__.py:1460`) — so a round trip
costs 2–5 ticks of scheduling before any work happens.

Payload size is irrelevant at this scale:

| call | payload | p50 |
| --- | --- | --- |
| `get_session_info` | 196 B | 0.427 s |
| `get_capture_topology` | 174,365 B | 0.632 s |

**174 KB costs 0.2 s more than 196 bytes.**

## The rule

> Budget **one round trip ≈ 0.4 s**. Design for the number of round trips, never
> for the size of the payload.

Corollaries, in the order they matter:

1. **No N+1 APIs.** A loop of the shape `for track: rpc(track)` costs
   `N × 0.4 s`. Across 30 tracks that is 12 seconds for data one batched read
   already returns. Measured precedent: the `live3r_trust` runner spent **667
   RPCs** (152 `get_track_info`, 145 `get_device_parameters`) where the
   production-shaped `live3r_prod` did comparable work in **37** by calling
   `get_capture_topology` once.
2. **Prefer one wide read to several narrow ones.** `get_capture_topology`
   already returns, per track: `devices`, `sends`, `monitoring`,
   `input_routing_type/channel`, `output_routing_type/channel`, `arm`, `mute`,
   `solo`, `volume`, `panning`, `clip_slots`, `taps`. Almost every
   `get_track_info` / `get_track_sends` / `get_track_*_routing` call in the
   codebase re-fetches a field the last snapshot already holds.
3. **Do not fragment the snapshot.** Splitting it into routing / device / MIDI
   snapshots multiplies round trips. This transport rewards fewer, wider reads.
4. **Distinguish the two kinds of read explicitly.**
   `AUTHORITATIVE_FRESH_READ` before a mutation or a verification;
   `SAFE_CACHED_READ` — served from the last snapshot — for descriptive data.
   Default to fresh so the safe choice is what you get by saying nothing. State
   Trust is never bypassed to save a round trip.
5. **Do not pipeline the socket.** The remote script runs
   `json.loads(buffer)` on the whole accumulated buffer, so two concatenated
   requests never parse and the connection hangs until `CLIENT_TIMEOUT`. One
   request in flight, request-id echo checked. This is a safety property.
6. **Batch the musical act, not the socket.** The real win in
   `producer-analyze` was not concurrency: it was recording several sources in
   one transport pass instead of one pass per source. Sequencing, not
   parallelism.

## Applying it to planned features

| feature | N+1 trap | batched shape |
| --- | --- | --- |
| MIDI read/write | `get_clip_notes` per clip per track | one topology read for clip slots, then notes only for the clips actually in the region |
| Device inspection | `get_device_parameters` per device | topology already lists devices; fetch parameters only for the device being acted on |
| Routing changes | read-modify-write per track | one snapshot, compute the whole plan, then only the writes that change something |
| Sample insertion | browser query per candidate | one browser scan, cached by the browser's own state token |
| Automation | envelope read per parameter | region-scoped batch read; do not walk every parameter |

## Enforcement

The adapter already counts round trips (`tcp_counts`, `snapshot_calls`,
`track_info_sites`) and `copilot.perf` now times them. New Live-facing work
should assert an RPC budget in its tests using `tcp_stats()` or
`rpc_breakdown(trace)`, so an N+1 regression shows up as a number rather than as
a slow afternoon.

Run `python -m copilot.cli producer-analyze --trace-performance` (or
`production-write --trace-performance`) to get a per-command breakdown of any
real run.
