# PERFORMANCE_AND_ROBUSTNESS_AUDIT_V1

Date: 2026-09-17
Scope: `<repo-root>` @ `3caa792` (branch `main`)
Method: source tracing plus direct measurement against the live Ableton session
on `127.0.0.1:9877`, plus 85 reasoning audits and recorded RPC counters already
present in `logs/`.

| Verdict | Status |
| --- | --- |
| ARCHITECTURE_AUDIT | **VERIFIED** |
| LUCAS_BRANCH_AUDIT | **INCOMPLETE — no such branch exists** (see `LUCAS_BRANCH_INTEGRATION_AUDIT_V1.md`) |
| PERFORMANCE_BASELINE | **VERIFIED** |

Capture cost is measured, not estimated: per-phase timings come from the durable
capture journals (`logs/capture_journal/*.jsonl`) of a real 3-source run, and
end-to-end wall clock from artifact `created_at`/`completed_at`. One 16.6 s
window inside the recording phase remains unattributed and is called out as
such in §1.

Artifacts produced:
`logs/performance_baseline_v1.json`, `logs/wait_inventory_v1.json`,
`logs/runtime_execution_graph_v1.json`, `logs/critical_path_v1.json`,
`logs/performance_report_v1.json`.

> **Canonical baselines now live in `docs/architecture/baselines/`** (committed),
> because `logs/` is gitignored runtime state. That directory also holds
> `producer_analyze_batching_v1.json`, the measured before/after for
> `SOURCE_CAPTURE_BATCH_V1`. `performance-report` and `--trace-performance`
> regenerate the live measurements on demand.
>
> Related fragility found the hard way: `logs/evidence_pack_v1.json` is a
> **single mutable slot** that every `producer-analyze` overwrites, and
> `test_persisted_pack_hash_and_scripted_ie` pinned a `pack_id` in it. A traced
> run on a different project destroyed the frozen `pack_afe4d6403ee4` pack (not
> recoverable — `logs/` is unversioned and surviving artifacts record only the
> id and payload hash). The test now reads a committed fixture under
> `fixtures/frozen/`, which a product run cannot clobber, and skips with an
> explicit reason until that fixture is re-seeded. `EXPECTED_PACK_ID` was
> deliberately **not** changed to match whatever is on disk.

### Regression baseline

| | collected | passed | failed |
| --- | --- | --- | --- |
| before this pass | 432 | 429 | 3 |
| after this pass | 460 | 457 | 3 |

The same three tests fail before and after, and all three fail *because Ableton
is running on this machine*:

- `test_detect_and_cli.py::test_cli_probe_never_uses_mock` — asserts exit code 2
  (blocked); the probe legitimately succeeds and returns 0.
- `test_detect_and_cli.py::test_cli_slice1_never_falls_back_to_mock` — same.
- `test_live_probe.py::test_real_ableton_is_blocked_without_live` — **a test
  bug, not an environment issue.** When Live *is* reachable the test calls
  `adapter.disconnect()` and then `adapter.health()`, which raises
  `DawError("Not connected")`. This test cannot pass in a Live-present
  environment regardless of product behaviour.

Nothing in this pass caused a regression. `pytest` was not installed in `.venv`
(only the runtime dependencies were), so the declared `dev` extra was installed
to obtain a baseline at all.

---

## 0. The one number that reframes everything

Every Ableton Remote Script round trip costs **0.368 s on average, regardless of
payload**.

60 consecutive `health_check` calls — a command that does nothing — produced
latencies in exactly four buckets:

```
0.2133  0.3196  0.4265  0.5335     spacing = 0.1067 s, samples between buckets = 0
n=60  min 0.213  p50 0.426  mean 0.368  p95 0.427  max 0.428
```

That spacing is Live's main-thread scheduler tick. The vendored remote script
marshals every command onto the audio/UI thread
(`schedule_message(0, main_thread_task)` then `response_queue.get(timeout=10.0)`,
`vendor/abletonmcp_remote_script/AbletonMCP/__init__.py:1460`), so a round trip
costs 2–5 ticks of scheduling before any work happens.

Corroboration from the same session:

| call | payload | p50 |
| --- | --- | --- |
| `get_session_info` | 196 B | 0.427 s |
| `get_capture_topology` | 174,365 B | 0.632 s |

**174 KB costs 0.2 s more than 196 bytes.** Bytes are not the cost. Round trips
are. Every optimisation proposal below is ranked by how many RPCs it removes,
not how much JSON it shrinks.

---

## 1. Top bottlenecks (measured, ranked by seconds)

The reference measurement is `ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1`
(2026-09-16, `VERIFIED`): **165.0 s wall clock to capture 3 sources** over a
32 qn region whose audio is **11.497 s** long.

```
                                   measured      of which intrinsic
total run                            165.0 s          34.5 s  (21%)
per capture pass (3 samples)   37.9 / 39.9 / 39.4 s   11.5 s  (29%)
  PREPARED  -> RECORDING       11.1 / 11.6 / 10.1 s      0 s   setup overhead
  RECORDING -> FINALIZING      28.2 / 28.3 / 27.8 s   11.5 s   16.6 s unattributed
  FINALIZING-> VERIFIED         0.10 / 0.04 / 0.04 s     0 s   copy+hash: free
inter-pass restore gap                6.1 / 6.3 s        0 s
run head/tail (preflight, inventory, diagnostic, acceptance)  ~35 s
```

**79 % of that run was implementation, not music.** Overhead is 3.8× the audio
it exists to observe, and ~27.5 s of it is paid *per source*.

1. **Serial per-source capture, one real-time playback pass each.**
   `producer_analyze_v1.py:421` loops `bounded_targets` and calls
   `capture_source_post_mixer_ref` once per source; each iteration costs a
   measured **~39 s pass + ~6.2 s restore gap** of which only 11.5 s is audio.
   Collapsing 3 passes into 1 removes ~90 s from a 165 s run.
2. ~~**16.6 s per pass unattributed inside the `RECORDING` phase.**~~
   **RESOLVED on 2026-09-17 by a traced run — and the hypothesis in this
   section was WRONG.** This audit suspected
   `wait_until_wav_shared_readable` was consuming much of its 12.0 s budget.
   Measured `waited_s` across six passes: **0.00024–0.00038 s**. WAV handle
   release is effectively instantaneous and capture is nowhere near its
   finalization timeout. The actual per-pass attribution is 15.24 s of region
   audio, **7.62 s of pre-roll playback** (`PRE_ROLL_QN = 16.0`,
   `arrangement_seek.py:22`), ~8–10 s of arm/stop/restore/routing RPCs, and
   ~0.06 s of local I/O. Unattributed is now ~0. No new instrumentation was
   needed: `capture_parallel_pass` already decomposed its own phases and every
   caller discarded `timings`; propagating them into the capture result and the
   `FINALIZING` journal record was the whole fix.

   Note on the pre-roll: it is **not pure overhead**. 16 qn is musical settling
   time for reverb tails, delays and sidechain envelopes before the observed
   region. Shortening it changes what the capture sounds like and would corrupt
   the evidence. The minimum safe value is an empirical question, not a
   constant to trim.
3. **Astra, p50 50.3 s / p95 112.1 s / max 163.5 s** (85 recorded runs). Fully
   provider-bound — see §9. Confirmed end to end: `FIRST_AUTONOMOUS_MUSICAL_
   IMPROVEMENT_V1` took 107.0 s of which 73.2 s was the model;
   `AUTONOMOUS_MUSICAL_EVALUATION_V2` took 100.0 s of which 57.7 s was the model.
4. **~10.9 s of capture setup per pass** (`PREPARED → RECORDING`), which at
   0.368 s/RPC is roughly 30 round trips of routing, send silencing,
   `OFF_MIX_GRAPH` verification and recorder construction.
5. **`_snapshot_host` is 5 RPCs and runs twice per capture**
   (`source_audio_trace.py:146`, again inside `_restore_host_full` (`:165`)) —
   `get_track_info`, `get_track_monitoring`, `get_track_sends`,
   `get_track_input_routing`, `get_track_output_routing`. Four of the five
   fields are already in the topology snapshot.
6. **N+1 `get_track_info`, measured in recorded runs.** `live3r_trust` = **667
   RPCs** (152 `get_track_info`, 145 `get_device_parameters`) ≈ 245 s of pure
   scheduler latency. `live3r_perf3` = 393 RPCs (181 `get_track_info`).
   The production-shaped `live3r_prod` does the comparable job in **37 RPCs**
   using one `get_capture_topology`. The fix already exists in the codebase.
7. **`project_ready` runs the identical per-host `get_track_info` loop twice**
   — `project_ready_v1.py:102` (via `_live_host_infos`) and again at
   `:104-108`. Both are redundant with the snapshot taken at `:95`.
8. **Two full snapshots per `project_ready`** (`:75` and `:95`) at 0.654 s each.
9. **Ableton launch path: 6.5 s of unconditional sleep** —
   `ableton_launcher_v1.py:73` (1.5 s), `:211` (2.0 s), `:244` (3.0 s after the
   process is already confirmed gone).
10. **`PROBE_BACKOFF = (1, 2, 4, 8)`** (`session_ready_v1.py:34`). A refused
   local TCP connect fails in microseconds, so an 8 s sleep can overshoot
   readiness by up to 8 s for no saving.
11. **`connect()` costs 0.534 s** (p50) because the `protocol_hello` handshake
    is itself a round trip. Workflows that connect more than once pay it again.

**Not bottlenecks** (measured, listed so nobody optimises them): sha256 of a
5.4 MB capture = 4.8 ms; full WAV decode = 4.6 ms; `compute_fullmix_observation`
= 22 ms; hashing the entire 118 MB capture corpus ≈ 0.11 s. Filesystem, hashing
and DSP are free at this scale.

---

## 2. Top architectural fragilities

1. **Cancellation does not exist as a concept.** No signal handling, no
   cancellation token, no cooperative abort anywhere in `src/`. An operation can
   only be killed, never asked to stop at a safe point. The two worst
   consequences are fixed below, but the general capability is still missing:
   nothing can abort a 50 s Astra call or a 12-minute project launch.
2. ~~**Ctrl-C during a capture leaves the session mutated.**~~ **FIXED in this
   pass.** `capture_source_post_mixer` caught `except Exception`, which does not
   catch `KeyboardInterrupt` (a `BaseException`), so `_restore_host_full` never
   ran and the capture host kept its rewritten input routing, zeroed sends and
   changed monitoring — in the read-only `producer-analyze` path, which a user
   would reasonably expect to be safe to interrupt. This was the
   highest-severity finding in the audit. See Stage 1 in §15.
3. ~~**Nothing stops the transport on interrupt.**~~ **FIXED in this pass.**
   `capture_parallel_pass`'s `finally` disarmed taps but never called
   `stop_playback`, so Ctrl-C during the real-time region sleep left Live
   playing.
4. **Nested deadlines do not compose.** `project_folder_import_v1.py:117` waits
   120 s; `ableton_launcher_v1.py:32` defaults to **720 s** consumed in 20 s
   chunks; each probe carries its own 2 s connect and 8 s read timeout. One user
   action can block for 12 minutes with no aggregate budget.
5. **Client and server timeouts disagree.** The remote script abandons a command
   at 10 s (`response_queue.get(timeout=10.0)`); the client waits up to 30 s
   (`TimeoutPolicy.large_operation`). The client can wait 20 s for a reply that
   was already given up on.
6. **The asset cache is dead code.** `audio/asset_cache.py` is well designed —
   keyed on project identity, scoped audible token, region, view, signal point,
   capture protocol, sample rate and analyser version — and has **zero call
   sites** outside its own tests. The machinery for the single biggest win in
   the system exists and is unwired.
7. **`cli.py` is 2,600 lines of one `argparse` positional plus ~35 flags**, with
   milestone behaviour selected by boolean flags on `production-write`
   (`--audible-effect-verification`, `--autonomous-musical-evaluation-v3`, …).
   Orchestration is coupled to argument parsing.
8. **Milestone modules duplicate orchestration.** `live3r*.py` (7 modules),
   `audible_effect_verification{,_v2}.py`, `autonomous_musical_evaluation_v{2,3}.py`
   each re-implement prepare → route → capture → verify → restore → persist.
9. **Capture lifecycle state is implicit.** The `CaptureJournal` records
   `PREPARED/RECORDING/FINALIZING/VERIFIED/FAILED` — a real state machine — but
   the surrounding code tracks progress in local booleans and list mutations
   (`routing_mutations`), so a crash between journal records leaves a state the
   code cannot name. There is no `IN_DOUBT` capture state to match the
   `WriteInDoubt` concept the write path already has.
10. **Frozen test inputs live in mutable runtime state.** `logs/` holds both
    throwaway run output and artifacts that verified milestones are pinned
    against. Running the product invalidates the regression suite. Fixed for the
    evidence pack (`fixtures/frozen/`); the pattern should be swept for.
11. **The Windows console breaks on non-ASCII output.** Found while building the
    tracer: `print()` of box-drawing characters raises `UnicodeEncodeError`
    under cp1252. Any future report that renders a tree must stay ASCII.

---

## 3. Waits and timeouts

Full inventory: `logs/wait_inventory_v1.json` (37 sites, AST-derived, each
classified against the call graph).

**The brief's hypothesis is not supported.** The hot path is not full of
arbitrary sleeps:

| classification | sites |
| --- | --- |
| ARBITRARY_DELAY | 25 |
| POLLING_INEFFICIENCY | 7 |
| REQUIRED_EXTERNAL_LATENCY | 2 |
| RESOURCE_LOCK | 2 |
| BACKOFF | 1 |

Of the 25 arbitrary delays, **19 are in `--lab` runners** (`live3r_trust`,
`tap_trust`, `live22`, `arrangement_seek`, `verify_rec_close_releases_handles`)
and 4 more are in one-time tap-install paths guarded by an existing-device check
(`live_capture.py:764,777`, `batch_capture.py:114,128`) so they do not fire on a
warm project. **On a warm `producer-analyze` the fixed-sleep budget is
approximately zero.**

Credit where due: the capture finalization waits are already the pattern the
brief asks for — condition-driven, bounded, typed failure:

- `wait_until_wav_shared_readable` (`live_capture.py:415`): 50 ms poll, 12 s
  deadline, fails as `CAPTURE_FINALIZATION_TIMEOUT` and explicitly *not* as
  `TAP_STALE_ARMED`.
- frame-stability wait (`live_capture.py:1422`): 50 ms poll, fails as
  `SHORT_CAPTURE` / `ZERO_BYTE` / `CANNOT_CREATE_OUTPUT`.
- transport-start wait (`batch_capture.py:911`): 20 ms poll, 2 s deadline.
- `wait_for_session` (`session_ready_v1.py:179`): bounded backoff with injectable
  clock and sleep.

**Keep all of these.** The genuine offenders are the launch path (§1.8) and the
backoff overshoot (§1.9).

---

## 4. Safe parallelisation

| candidate | verdict | why |
| --- | --- | --- |
| Multi-source capture in **one** playback pass | **SAFE — and the biggest win** | `capture_parallel_pass` already accepts a `recorders` list and arms several taps in one pass. The infrastructure exists; `producer_analyze` simply calls it once per source instead of once per region. |
| `compute_fullmix_observation` / `compute_lowend_features` across captures | SAFE_PARALLEL | Pure functions over immutable WAVs. But measured at 22 ms — **not worth it**. |
| File hashing | SAFE_PARALLEL | 4.8 ms per file. Not worth it. |
| ALS parse concurrent with capture | SAFE_PARALLEL | Independent input; modest gain. |
| Astra call overlapped with terminal snapshot / artifact persistence | SAFE_PARALLEL | The final `snapshot` and `_persist` do not feed the prompt. Saves ~1 s against 50 s — marginal. |
| Ableton **mutations** | **UNSAFE_PARALLEL** | One socket, one in-flight request, request-id echo checking, `SessionMutationLock`. Do not touch. |
| Pipelining several JSON requests on one socket | **UNSAFE** | The remote script does `json.loads(buffer)` on the whole accumulated buffer (`__init__.py:187`). Two concatenated objects never parse and the connection deadlocks until `CLIENT_TIMEOUT`. |
| Transport control, state-token commits, rollback writes | UNSAFE_PARALLEL | Serialisation is the safety property. |

Conclusion: parallelism is **not** the lever here. Batching one capture pass over
many sources is, and it is a sequencing change, not a concurrency change.

---

## 5. Caching

Measured: caching deterministic DSP saves ~22 ms and is worthless.
**The only cache worth having is the capture asset cache — and it already exists.**

`audio/asset_cache.py` keys on `project_identity + scoped audible_token + region
+ source + view + signal_point + capture_protocol + sample_rate +
core_capture_version`, verifies stored provenance, and requires the journal to be
`VERIFIED`. It has zero production call sites.

Important nuance, measured: re-captures of the same source across runs are **not
bit-identical** (`Filter_Kick`, `Filtered_Bassline`, `High_String`,
`Open_Hi_Hat` each appear twice with different audio hashes). So a
*content-hash* cache would never hit. A *state-keyed* cache — exactly what
`asset_cache` implements — would, because the audible state is unchanged. Reuse
must therefore stay keyed on state, never on audio content.

| candidate | expected saving | invalidation | risk |
| --- | --- | --- | --- |
| Capture assets via existing `AssetCache` | **~15 s + ~35 RPC per reused source**; a fully warm repeat run saves ~167 s | audible token change, journal not `VERIFIED`, file missing, protocol/analyser version bump | Medium — must not bypass State Trust. The existing key already encodes everything required. |
| `last_track_infos` as an explicit `SAFE_CACHED_READ` | ~0.37 s per avoided RPC, dozens per workflow | new snapshot or any mutation | Low, if callers needing freshness opt in explicitly |
| ALS hash → parsed arrangement | small; ALS is parsed 2–4× per run in `analyze_region_v1.py:974,1009,1021,1109` | ALS sha256 | Low |
| FullMix / LowEnd DSP | 22 ms | audio sha + analyser version | Not worth it |

---

## 6. Ableton RPC problems

- Every call costs 2–5 scheduler ticks. **Count is the only lever.**
- `snapshot()` already degrades correctly: topology (1 RPC) → `get_tracks_info`
  (3 RPC) → serial N+1 (N+3). This session uses the topology tier. Good design,
  already in place.
- `get_capture_topology` returns per track: `devices`, `sends`, `monitoring`,
  `input_routing_type/channel`, `output_routing_type/channel`, `arm`, `mute`,
  `solo`, `volume`, `panning`, `clip_slots`, `taps`. **Almost every
  `get_track_info` / `get_track_sends` / `get_track_*_routing` call in the
  codebase is re-fetching a field the last snapshot already holds.** There are
  41 `get_track_info` call sites.
- The right pattern already exists in three places —
  `daw.last_track_infos.get(idx) or daw.get_track_info(idx)`
  (`batch_capture.py:277`, `live_capture.py:1675,1694`) — and is not used
  elsewhere.
- No `TCP_NODELAY` on the socket (`ableton_tcp.py:105`). Negligible next to a
  107 ms quantum; mentioned only for completeness.
- Freshness must not be weakened silently. Introduce an explicit distinction:
  `track_info(index, fresh=True)` for authoritative reads before a mutation or a
  verification, `fresh=False` for descriptive reads served from the last
  snapshot. Default to `fresh=True` so the safe choice is the one you get by
  saying nothing.

---

## 7. Snapshot architecture

**Do not fragment it.** Measured: one `get_capture_topology` costs 0.632 s and
returns everything, while the fields it contains cost 0.37–0.43 s *each* to
re-fetch individually. Splitting into `RoutingSnapshot` / `DeviceSnapshot` /
`MidiSnapshot` would multiply round trips — precisely the wrong direction on
this transport. The one real change worth making is the opposite: **use the
snapshot you already took** instead of re-reading fields from it.

`include_notes=True` adds one `get_clip_notes` round trip (0.962 s vs 0.654 s).
`producer_analyze` already passes `include_notes=False` throughout. Correct.

---

## 8. Filesystem and audio I/O

Measured on a 5,376,088 byte / 15.24 s stereo 44.1 kHz capture:

| operation | p50 |
| --- | --- |
| `sha256_file` (whole-file `read_bytes`) | 4.8 ms |
| streaming sha256, 1 MB chunks | 5.2 ms |
| `soundfile.read` full | 4.6 ms |
| `soundfile.info` | 0.15 ms |
| `compute_fullmix_observation` | 22.3 ms |

Corpus: 22 files, 118 MB. Hashing all of it ≈ 0.11 s.

**No action.** `sha256_file` reading whole files into memory
(`file_hash.py:14`) is a memory-shape concern at 5 MB, not a latency one;
streaming was *slower* in this measurement. The repeated ALS
`read_bytes()` + sha256 (2–4× per run in `analyze_region_v1.py` and
`external_causal_context_v1.py`) is worth tidying for clarity, not for speed.
Append-only journals and durable artifact writes must keep their current
semantics — they cost milliseconds.

---

## 9. Model pipeline cost

From 85 recorded reasoning audits:

```
model_s        min 25.9   p50 50.3   p95 112.1   max 163.5   mean 57.8
prompt_build_s p50 0.00029   max 0.0104
validation_s   p50 0.00107   max 0.0250
local share of the Astra phase: 0.0027 %
```

The brief's worry — "do not report ASTRA = 80 s if 20 s is local serialization" —
**does not apply to this codebase.** `pipeline.py:132-199` already separates
`prompt_build_s`, `model_s`, `validation_s` and `total_s` with `perf_counter`,
and local work is under 0.01 s. There is no local optimisation available in the
Astra path.

The real levers, in order:

1. **`COPILOT_REASONING_EFFORT=high`** (`.env`). This is the single largest
   model-latency dial in the system. Measuring `medium` on the existing fixture
   suite would quantify the trade honestly.
2. **Call count.** `producer_analyze` makes exactly one Astra call — already
   correct. `session-run1-astra-r2` makes one per region.
3. **Overlap.** Nothing else runs while the 50 s call blocks.

Context efficiency was *not* confirmed as a problem: `build_prompt` is
sub-millisecond, so prompt size is not a local cost. Whether the pack contains
redundant evidence is a **token-cost and grounding question**, not a latency one,
and should be answered by token accounting rather than by timing. Do not strip
evidence refs for speed — the measurement gives no speed justification.

---

## 10. Do we need a new execution framework?

**Answer: B — a small orchestration kernel is justified. Not C.**

Not because the code is verbose, but because three specific capabilities are
missing and cannot be retrofitted per-milestone without repeating the bug in
§2.2 eleven more times:

1. **Cancellation with guaranteed cleanup.** A capture must restore routing,
   disarm taps, stop the transport and write a terminal journal record on
   `BaseException`, not just `Exception`.
2. **Composable deadlines.** A child operation must inherit the remaining
   budget, not start a fresh 30 s of its own.
3. **Spans.** Delivered in this audit (`copilot/perf`).

This is consistent with the existing `docs/architecture/TARGET_ARCHITECTURE.md`
and changes none of its boundaries: Core still owns state, measurement,
authorization, execution and verification; models remain slots; `MEASURE !=
DIAGNOSE != DECIDE != ACT != IMPROVE` is untouched. The kernel below sits
*underneath* that boundary as plumbing for the Core, not as a new decision
maker. Note that `TARGET_ARCHITECTURE.md` already says "never run an LLM in the
Ableton audio callback" — this audit adds the measured counterpart: *everything*
that crosses the Remote Script pays 106.7 ms of that thread's scheduling, which
is why RPC count is the dominant cost.

Proposed kernel — **four concepts, no new dependencies**:

```
OperationContext   operation_id, project_identity, deadline, cancel_token, trace
Deadline           remaining() -> float; child budgets are min(parent, request)
CleanupScope       push(fn); runs LIFO on BaseException, records failures
PipelineStep       preconditions -> execute -> verify -> cleanup, typed StepResult
```

Explicitly rejected (Phase 25): Celery, Temporal, Prefect, Ray, Airflow,
LangGraph. This is a local-first, single-user, single-socket, Windows-first
application whose concurrency requirement is *zero*. A workflow engine would add
a broker, a scheduler and failure semantics of its own to a system whose hardest
constraint is "one TCP socket to Live, strictly serialised". `contextlib`,
`contextvars`, `dataclasses` and `time.perf_counter` cover it. `tenacity` was
considered for retry and rejected: the retry decisions here are
domain-specific (`PROJECT_MISMATCH` is not retryable without state
reconciliation; an unknown mutation result is `IN_DOUBT` and must never be
blindly repeated) and belong in the existing taxonomy, not in a generic library.

---

## 11. Failure taxonomy and retry

The codebase already has more typed failure than the brief assumes:
`ReasoningFailure`, `StateTrustError` with `CACHE_MISS`/`CACHE_STALE`,
`WriteInDoubt`, `ProtocolError`, `AudioCaptureError` with codes
(`CAPTURE_FINALIZATION_TIMEOUT`, `SHORT_CAPTURE`, `ZERO_BYTE`,
`CAPTURE_ROUTING_UNSUPPORTED`, `AUDIBLE_MIX_RISK`), and the fail-closed rule
that infrastructure failure never becomes `INSUFFICIENT_EVIDENCE` is honoured at
`producer_analyze_v1.py:496` (a reasoning exception yields
`DIAGNOSIS_UNSTABLE`, not abstention).

What is missing is a **retry classification** attached to those codes:

| class | examples |
| --- | --- |
| RETRYABLE_TRANSIENT | socket disconnect on a read, `CAPTURE_FINALIZATION_TIMEOUT` on first attempt |
| RETRYABLE_AFTER_STATE_REFRESH | stale track index, `CACHE_STALE` |
| NON_RETRYABLE | `LLM_GROUNDING_VIOLATION`, `CAPTURE_ROUTING_UNSUPPORTED`, `AUDIBLE_MIX_RISK` |
| REQUIRES_USER_ACTION | `PROJECT_MISMATCH`, `ORIGINAL_SET_OPEN`, `LIVE_UNAVAILABLE` |
| IN_DOUBT | `WriteInDoubt` — never blindly repeated; already correct |

`pipeline.py:152` retries schema parsing up to `SCHEMA_ATTEMPTS`. That is the
only retry loop in the reasoning path and it is bounded. HTTP 429 is currently
mapped to `MODEL_UNAVAILABLE` with no `Retry-After` handling
(`provider.py:189`) — worth typing separately.

---

## 12. Instrumentation delivered

`src/copilot/perf/` — a new package alongside the ones listed in
`TARGET_ARCHITECTURE.md`, importing nothing from `daw/`, `audio/` or
`reasoning/` at module scope so instrumentation can be used from anywhere
without dragging in the pipeline it measures:

- `trace.py` — `ExecutionSpan`, `PerformanceTrace`, `OperationMetrics`,
  `active_trace()`, `span()`. Monotonic `perf_counter` durations, `process_time`
  CPU, wall clock persisted for correlation only. Exclusive time per category so
  totals do not double-count nesting. Categories: `INTRINSIC_AUDIO`,
  `ABLETON_RPC`, `MODEL`, `LOCAL_CPU`, `LOCAL_IO`, `WAIT`, `ORCHESTRATION`.
  Spans record cancellation (`BaseException`) and re-raise; `classify_error`
  describes the *mechanism* and can never return a musical verdict.
- `ableton.py` — `instrumented_rpc(adapter)`, an instance-scoped context manager
  that times every `_command` and always restores the original, including when
  the body raises. Same arguments, same return value, same exceptions.
- `report.py` — the benchmark driver.

CLI: `python -m copilot.cli performance-report [--offline] [--repeats N]
[--live-host H] [--live-port P]`, added to `CANONICAL_COMMANDS`, writing
`logs/performance_report_v1.json`. Read-only: it issues Ableton *reads* only,
and reports capture and Astra cost from recorded evidence rather than re-running
them (`OBSERVED_FROM_EVIDENCE`), so it is safe to run while the user is working
in Live.

Note it does **not** share the eval server's `--port` flag; the shared `--host`/
`--port` across unrelated commands was itself a defect (it silently pointed the
first version of this report at port 8765).

Sample output against the live session:

```
PERFORMANCE_REPORT_V1
  ableton connect+handshake   median 0.534415s
  snapshot (no notes)         median 0.653703s via topology (1.0 rpc)
  scheduler quantum           ~0.108737s per round trip (reduce RPC COUNT, not bytes)
    health_check               0.425983s
    get_capture_topology       0.748961s
  astra (recorded n=85)       median 50.308135s max 163.506285s; local share 0.0027%
  read-only survey            1.38687s over 3 rpc (1.374972s in Ableton) across 32 tracks
  dsp fullmix                 median 0.017616s on 15.238s audio
```

---

## 13. Top quick wins

Ranked by measured saving ÷ risk. Items 1–3 are measured; 4–6 are structural.

| # | change | current cost | expected saving | risk | regression needed |
| --- | --- | --- | --- | --- | --- |
| 1 | ~~**Capture N sources in one playback pass.**~~ **DONE — `SOURCE_CAPTURE_BATCH_V1`.** Ceiling is **2 sources per pass**, not N: two host tracks exist and the Max device's slot selector has three outlets (slot 0 = Main). | 254.22 s traced, 4 passes, 237 RPC | **measured −59.37 s (−23.4%)**: 194.85 s, 2 passes, 191 RPC. Capture-side saving 64.3 s (Astra was 4.9 s slower in the after run). | Medium — preserved: `OFF_DIRECT_MAIN` per source, per-source journals to `VERIFIED`, fail-closed on any restore failure | `tests/test_source_capture_batch_v1.py` (21 tests) |
| 2 | ~~Fix `except Exception` → `finally` in `capture_source_post_mixer` and stop the transport in `capture_parallel_pass`'s `finally`.~~ **DONE — see Stage 1 in §15.** | session left mutated on Ctrl-C | correctness, not seconds | **Low** | `tests/test_capture_cancellation_v1.py` |
| 2b | ~~**Record `waited_s` from `wait_until_wav_shared_readable`**~~ **DONE.** `_pass_timings` now propagates the full pass decomposition into the capture result and the `FINALIZING` journal record. | 16.6 s per pass unexplained | attribution: unattributed now ~0 | **None** | covered by the batch suite |
| 3 | **Wire `AssetCache` into `capture_source_post_mixer_ref`.** | full re-capture every run | ~45 s per reused source; ~130 s on a warm repeat of the measured run | Medium — State Trust | Cache hit/miss, audible-token invalidation, stale-journal refusal |
| 4 | **Serve `get_track_info`/`get_track_sends`/routing reads from `last_track_infos`** via an explicit `fresh=` flag; delete the duplicate host loop at `project_ready_v1.py:104-108`. | ~0.37 s per avoided RPC; 10 RPC per capture in `_snapshot_host` alone | ~3.7 s per capture pass; ~11 s on the measured 3-source run | Medium — freshness | Assert authoritative reads still happen before mutation and verification |
| 5 | **Cap `PROBE_BACKOFF` at 1.5 s.** | up to 8 s overshoot | up to 7 s per launch | Low | Existing `session_ready` tests inject `sleep`/`clock` |
| 6 | **Replace the launcher's 1.5 s / 2.0 s / 3.0 s sleeps with bounded condition waits.** | 6.5 s unconditional | ~5 s per import | Low–Medium — Windows handle release | Import/launch tests |

Deliberately **not** recommended, on measured grounds: streaming hashes, caching
DSP, shrinking evidence payloads for speed, splitting the snapshot, parallel DSP,
`TCP_NODELAY`.

---

## 14. Performance budgets

| class | target | applies to |
| --- | --- | --- |
| INTERACTIVE (< 1 s) | `doctor` local checks, `capabilities`, artifact reads | achievable |
| SHORT (1–5 s) | `snapshot`, `project-ready` | currently ~2–4 s; achievable after win #4 |
| OBSERVATION | region length × passes | `INTRINSIC_AUDIO_DURATION` — 3 × 11.5 s today, 1 × 11.5 s after win #1 |
| MODEL_BOUND | provider-dependent | p50 50 s, p95 112 s. **Do not set a target.** |
| LAUNCH | 20–40 s | Live start-up dominates; ~5 s recoverable |

Honest target, anchored on the one fully measured multi-source run
(3 sources, 32 qn, 165.0 s, no Astra):

| | measured today | after wins #1 + #4 | after #3 on a warm repeat |
| --- | --- | --- | --- |
| 3-source isolation | 165.0 s | ~70 s | ~35 s |
| of which intrinsic | 34.5 s | 11.5 s | 0 s (reused) |

Adding one Astra call puts a full `producer-analyze` at roughly **120 s** where
today it would be ~215 s, and ~50 s of the remainder is provider-bound and not
improvable locally.

---

## 15. Migration plan

**Stage 0 — done.** Instrumentation, `performance-report`, 20 new tests, this
audit. No behaviour changed.

**Stage 1 — correctness, no performance claim. DONE in this pass.**
Quick win #2 is implemented:

- `arrangement_active_source_isolation.capture_source_post_mixer` now carries a
  `settled` sentinel set on both the success and `except Exception` paths, and a
  `finally` that calls the new `_cancel_cleanup` only when neither ran — i.e.
  only on `BaseException`. `_cancel_cleanup` stops the transport, restores the
  host, and records a terminal journal entry, each step independent so one
  failure does not skip the others, and it never raises.
- `batch_capture.capture_parallel_pass` gained a `transport_stopped` sentinel and
  a guarded `stop_playback` in its `finally`, so an interrupt during the
  real-time region sleep — the longest window in the function — no longer leaves
  Live playing. The guard means the success path pays no extra RPC.
- Recovery semantics are **unchanged**: the cancel is recorded as `FAILED` with
  `error="CANCELLED_BY_USER"`, and `FAILED` was already in
  `capture_journal_recovery.TERMINAL_STATUSES`. No new status was introduced.
- `tests/test_capture_cancellation_v1.py` (8 tests) pins the contract, including
  that the interrupt still propagates and that cleanup cannot run on the normal
  paths.

**Stage 2 — measure the real thing.** Wrap `producer_analyze` and
`project_ready` in `active_trace` + `instrumented_rpc`. Run once under
authorisation. Replace the reconstructed figure in `logs/critical_path_v1.json`
with a measured one. **No optimisation before this.**

**Stage 3 — RPC reduction.** Quick win #4, behind `fresh=True` defaults.
Assert RPC counts in tests using the existing `tcp_stats()` counters so
regressions are caught numerically.

**Stage 4 — the big one.** Quick win #1, multi-source single-pass capture, with
per-source isolation proof retained.

**Stage 5 — cache.** Quick win #3, once Stage 4 has settled the capture shape.

**Stage 6 — kernel.** Extract `OperationContext` / `Deadline` / `CleanupScope`
only after Stages 1–5 have shown the same three needs three times. Migrate the
canonical envelope first; leave `--lab` runners untouched.

---

## 16. Expected performance improvements

| stage | operation | before | after | basis |
| --- | --- | --- | --- | --- |
| 1 | interrupt safety | session mutated | clean | correctness |
| 3 | `project-ready` | ~4 s | ~1.5 s | 6 fewer RPC × 0.37 s |
| 3 | per capture setup | ~10.9 s | ~7 s | `_snapshot_host` (10 RPC/pass) served from topology |
| 4 | 3-source isolation (measured run) | 165.0 s | ~70 s | one pass instead of three: removes 2×(11.5 s audio + 27.5 s overhead + 6.2 s restore) |
| 5 | warm repeat of the same run | ~165 s | ~35 s | capture reuse via the existing `AssetCache` key |
| — | Astra | 50.3 s p50 | unchanged | provider-bound; only `EFFORT` moves it |

Every figure above is derived from a measurement in
`logs/performance_baseline_v1.json` or `logs/critical_path_v1.json`. The
`producer-analyze` projection is the one figure that composes a measured
3-source isolation with a measured Astra p50 rather than being observed as a
single run; it is labelled as such.
