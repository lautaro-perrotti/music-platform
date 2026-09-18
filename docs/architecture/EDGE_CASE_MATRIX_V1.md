# EDGE_CASE_MATRIX_V1

Date: 2026-09-17
Method: each row was checked against source. `HANDLED` means there is a code
path with a distinct typed outcome. `PARTIAL` means the case is detected but the
response is incomplete or the classification is lossy. `UNHANDLED` means no code
path addresses it.

Priority = severity × likelihood × recoverability. **P1** = fix next, **P2** =
schedule, **P3** = accept for now.

---

## Ableton

| case | status | evidence | priority |
| --- | --- | --- | --- |
| Not installed | HANDLED | `detect_ableton()` → `BLOCKED_BY_ENVIRONMENT`; `doctor` reports it | — |
| Wrong version | PARTIAL | Handshake records `protocol_version`/`bridge_version` (`ableton_tcp.py:120`) and `vendor_meta.py` pins a checksum, but no minimum-version gate. A newer Live with a changed control-surface API would fail per-command, not at handshake. | P2 |
| Already open with the right project | HANDLED | `ableton_launcher_v1.py:50-67` returns `launch="already_open"` after `paths_match`/`names_match` **and** a snapshot check | — |
| Already open with the *wrong* project | HANDLED | `PROJECT_MISMATCH` / `ORIGINAL_SET_OPEN`; `working_copy_policy_v1` refuses to operate on the original set | — |
| Frozen UI / modal dialog blocking | PARTIAL | A background thread polls `dismiss_live_blocking_dialogs` every 0.7 s during launch (`ableton_launcher_v1.py:88-98`) with `classify_live_dialog` and `recovery_action`. **Only during launch.** A dialog appearing mid-capture is not detected; the RPC simply times out. | P2 |
| TCP listening but zombie | HANDLED | `ZOMBIE_PORT` is a distinct status, inferred from timeout/disconnect on the handshake (`session_ready_v1.py:165`), and `PROBE_TIMEOUTS` fails it in 2 s rather than 30 s. Covered by `test_session_ready_v1.py` | — |
| Disconnect during a read | HANDLED | `DawError("socket disconnect")`; never converted to a musical verdict | — |
| Disconnect during a write | HANDLED | `WriteInDoubt(command_type, request_id)` — the correct `IN_DOUBT` outcome; `test_write_indoubt.py`, `test_protocol_hardening.py` | — |
| Disconnect during a capture | **PARTIAL** | The `except Exception` handler attempts `_restore_host_full`, but that itself issues RPCs on the dead socket and will fail. The result is `CAPTURE_FAILED` with `restore.ok = False`, which is honest, but the host is left mis-routed and nothing schedules a repair on reconnect. `live_state.py` proves reconnect + `PersistentObjectRef` re-resolution works — it is just not wired to capture recovery. | **P1** |
| Project changed during an operation | HANDLED | `PROJECT_STATE_TOKEN` / `AUDIBLE_STATE_TOKEN` / `TARGET_STATE_TOKEN`, `revalidate_project` → `STALE_PLAN`, and `identity_after_capture` records a per-capture identity match (`producer_analyze_v1.py:443`) | — |
| Project closed mid-operation | PARTIAL | Detected on the next RPC as a disconnect; same unrepaired-routing gap as above | P1 |
| Response arrives for a different request | HANDLED | `request_id` echo is verified and mismatch raises `ProtocolError` (`ableton_tcp.py:966`) | — |
| Truncated / invalid JSON from the script | HANDLED | `ProtocolError("truncated packet")`, `test_protocol_hardening.py::test_invalid_and_truncated_json` | — |
| Server abandons a command before the client does | **UNHANDLED** | Remote script gives up at 10 s (`response_queue.get(timeout=10.0)`); client waits up to 30 s (`TimeoutPolicy.large_operation`). The client can block 20 s for a reply nobody will send. | P2 |

---

## Files

| case | status | evidence | priority |
| --- | --- | --- | --- |
| Locked WAV (writer still holds it) | HANDLED | `wait_until_wav_shared_readable`, 50 ms poll / 12 s deadline, distinct `CAPTURE_FINALIZATION_TIMEOUT`; plus `_file_locked` retry on copy (`live_capture.py:560,583`) | — |
| Empty / zero-byte WAV | HANDLED | `AudioCaptureError("ZERO_BYTE", …)` (`live_capture.py:1462,1833`) | — |
| Delayed file release | HANDLED | Same bounded wait. But see the audit's §1.2: the 12 s budget may be nearly exhausted in practice and `waited_s` is discarded, so we cannot currently tell. | P1 (observability) |
| Short / truncated capture | HANDLED | `SHORT_CAPTURE` with a frame-stability requirement (2 consecutive equal frame counts) | — |
| Non-finite samples | HANDLED | `CAPTURE_QUALITY_WARNING` on `np.isfinite` failure (`live_capture.py:1447`) | — |
| Cannot create output | HANDLED | `CANNOT_CREATE_OUTPUT` | — |
| Path collision between passes | HANDLED | Every filename carries a `pass_id` (`uuid4().hex[:12]`) | — |
| Disk full | **UNHANDLED** | No `ENOSPC` handling anywhere. A full disk surfaces as a bare `OSError` from `shutil.copy2` or `write_text`, and a partially written evidence artifact can result (`_persist` uses plain `write_text`, not the atomic replace used by `perf/trace.py:persist`). | P2 |
| Permission denied | **UNHANDLED** | No `PermissionError` handling in `src/` outside the new `perf/trace.py`. Relevant on Windows where Live may hold the prefs tree. | P2 |
| File deleted mid-analysis | PARTIAL | `sha256_file` returns `None` for a missing file and `_provenance_ok` reports `"file missing"` — but `_provenance_ok` lives in the dead `asset_cache`. Live analysers call `path.is_file()` first, then read, so a delete in that window raises a bare `OSError`. | P3 |
| Unicode paths | PARTIAL | All I/O passes `encoding="utf-8"` explicitly, which is right. But **console output breaks**: `print()` of non-ASCII raises `UnicodeEncodeError` under cp1252 — hit twice in this repo's history (the stash comment at `production-write`, and the tracer during this audit). A project path with non-Latin-1 characters would break report printing. | P2 |
| Long Windows paths (>260 chars) | **UNHANDLED** | No `\\?\` prefixing, no long-path awareness. Ableton project folders nest deeply and capture filenames embed region id + track name + pass id, so this is reachable: `aasi_v1_<region>_<TrackName>_<12 hex>.wav` inside a user's Desktop project tree. | P2 |

---

## Project topology

| case | status | evidence | priority |
| --- | --- | --- | --- |
| No tracks | HANDLED | `select_activity_region` falls back to `AUTO_0_32` with `why="fallback_empty_arrangement"` | — |
| Huge track count | PARTIAL | Measured fine at 32 tracks (one 174 KB topology read, 0.63 s). `SOURCE_BUDGET = 4` bounds capture targets so cost does not scale with track count. But the topology payload grows linearly and there is no cap or streaming; at ~500 tracks this is ~2.7 MB per snapshot. | P3 |
| Nested groups | HANDLED | Group children are tagged via `clip.group` when the ALS is parsed; group material is attributed to the parent (`generic_source_isolation_v1.py:32-51`) | — |
| Return tracks | HANDLED | `track.role in {"return","master"}` is excluded from capture targets (`:72`) | — |
| Group without a Main path | HANDLED | `_verify_off_mix_graph` requires an `OFF_MIX_GRAPH`/`OFF_DIRECT_MAIN` claim and raises `CAPTURE_ROUTING_UNSUPPORTED` otherwise; `AUDIBLE_MIX_RISK` if the host still routes through Main | — |
| Missing plugins | **UNHANDLED** | No detection. A track with an unloadable device reports devices from the topology but capture would produce silence, classified as `NO_SIGNAL` rather than "device missing" — a misleading musical conclusion from an infrastructure fault. | P2 |
| Disabled / deactivated devices | PARTIAL | `set_tap_enabled` manages the *tap* device's on-state. A user device being off is visible in the topology but not interpreted. | P3 |
| Frozen tracks | **UNHANDLED** | No `is_frozen` check. A frozen track cannot be re-routed, so `route_host_post_mixer` would fail with an opaque `DawError` rather than a typed "target not capturable". | P2 |
| Muted tracks | HANDLED | Excluded from eligibility (`capture_ok = … and not muted`) | — |
| Empty clips / no material in region | HANDLED | `NO_MATERIAL` vs `HAS_MATERIAL` vs `UNKNOWN`, with `UNKNOWN` handled distinctly rather than collapsed | — |
| Overlapping clips | HANDLED | `clips_overlap_region` counts hits; overlap does not break attribution | — |
| Looped MIDI | PARTIAL | ALS arranger clips are read; loop expansion is not modelled, so a looped clip's material outside its first iteration may not be counted | P3 |
| Automation | **UNHANDLED** | Not read at all. Automation is a plausible cause of an audible change the system would attribute elsewhere. Worth stating as a known evidence limitation rather than a bug. | P3 |
| Tempo changes within a region | **PARTIAL — guard exists but is not wired into the production path** | `assert_constant_tempo` (`live_capture.py:1139`) probes 3 points across the region and raises the typed `TEMPO_AUTOMATION_UNSUPPORTED` — correct, fail-closed. It is called from `batch_capture.py:518`, `live_capture.py:1748` and two lab runners, but **not** from `producer_analyze` → `capture_source_post_mixer` → `capture_parallel_pass`, which receives `tempo` as a parameter sourced from `float(session.transport.tempo)` (`producer_analyze_v1.py:433`) with no verification. `record_s` is then computed from that scalar (`batch_capture.py:932-936`), so a tempo change inside the region silently mis-sizes the capture window. Two secondary notes: a 3-point probe is a sample, not a proof (a ramp returning to the same value at all three points passes), and the probe costs 6 RPCs and moves the transport (restored in `finally`). | **P1** |
| Unusual meter | PARTIAL | `set_signature` exists and is exercised for 4/4, 3/4, 6/8 in a lab runner; region maths is in quarter notes so meter is mostly irrelevant, but no test covers region selection under a non-4/4 signature | P3 |

---

## Model

| case | status | evidence | priority |
| --- | --- | --- | --- |
| Timeout | HANDLED | `ProviderError(MODEL_TIMEOUT)`; explicitly **not** `INSUFFICIENT_EVIDENCE` | — |
| HTTP 429 | **PARTIAL** | Mapped to `MODEL_UNAVAILABLE` with the body truncated to 400 chars (`provider.py:189`). No `Retry-After` parsing, no distinct rate-limit class, no budgeted retry. | P2 |
| Invalid JSON | HANDLED | `_parse_output` → `MODEL_OUTPUT_INVALID` after `SCHEMA_ATTEMPTS` | — |
| Schema-valid but ungrounded | HANDLED | `validate_reasoning` → `LLM_GROUNDING_VIOLATION`, confidence capping, `request_decisions` recorded in the audit | — |
| Partial response | HANDLED | Falls into the JSON/schema path | — |
| Network disconnect | HANDLED | `URLError` → `MODEL_UNAVAILABLE`, distinct from timeout | — |
| Slow response | HANDLED | 180 s ceiling (`ASTRA_TIMEOUT_S`); measured max 163.5 s, so the real p-max sits close to the ceiling | P3 (watch) |
| Duplicate request / retry | HANDLED | Only the schema-parse loop retries, bounded; `raw_hash` and `output_hash` are recorded so a duplicate is identifiable | — |
| Human labels not locked | HANDLED | `AstraPrelockBlocked` before any provider call | — |

---

## Operations

| case | status | evidence | priority |
| --- | --- | --- | --- |
| **User cancel (Ctrl-C) mid-capture** | **HANDLED — fixed in this pass** | Was `UNHANDLED`: `capture_source_post_mixer` caught `except Exception`, which does not catch `KeyboardInterrupt`, so routing, sends and monitoring stayed mutated and the transport kept playing. Now a `settled` sentinel makes a `finally` invoke `_cancel_cleanup` on `BaseException` only — stop transport, restore host, terminal journal record (`FAILED` / `CANCELLED_BY_USER`, an already-recognised terminal status). `capture_parallel_pass` stops the transport in its `finally` behind a `transport_stopped` guard. `tests/test_capture_cancellation_v1.py`. | — |
| User cancel elsewhere (Astra call, project launch, snapshot) | **UNHANDLED** | No signal handling anywhere in `src/`. A 50 s Astra call and a launch wait of up to 720 s cannot be interrupted at a safe point, only killed. `producer_analyze` itself has no `finally`, so an interrupt between captures skips artifact persistence (harmless today, but there is no cooperative abort). | P1 |
| Process crash | HANDLED | `DurableJournal` + `CaptureJournal` + `agent_transactions.json`; `capture-journal-recover` command; `recovery.py` classifies `VERIFIED` / `RECOVERY_REQUIRED` with explicit `known`/`unknown` fields | — |
| Restart recovery | HANDLED | `capture_journal_recovery.py` re-hashes staged WAVs; journal scan runs on CLI start (observed in the test log at `cli.py:527`) | — |
| Concurrent CLI invocations | **UNHANDLED** | `SessionMutationLock` is an in-process `threading.RLock`; it cannot serialise two `python -m copilot.cli` processes. No lockfile, no `O_EXCL`, no named mutex. Two concurrent `producer-analyze` runs would fight over the same capture host, the same staging WAV paths and the same evidence artifacts. | **P1** |
| Duplicate import | HANDLED | `working_copy_manager_v1` + `project_folder_resolver_v1`; `test_project_folder_import_v1.py` | — |
| Stale working copy | HANDLED | `evaluate_working_copy` → refuses to operate; `WORKING_COPY_CANDIDATE` | — |
| Stale journals | HANDLED | The recovery scan reports 13 open transactions in the current tree with per-transaction `known`/`unknown` — the design intent, working | — |
| Cleanup failure | PARTIAL | `_restore_host_full` returns `ok:false` with an `errors` list and the capture is marked `ROUTING_RESTORE_FAILED` — honest, but nothing retries the restore or blocks further captures on the same host | P2 |
| Retry after uncertain write | HANDLED | `WriteInDoubt` is never blindly repeated; the documented rule is that a retry of a `NON_IDEMPOTENT` write is allowed only after a read proves the postcondition absent (`vendor/.../VENDOR_META.json`) | — |

---

## Priority summary

**P1 — fix next**

1. ~~Ctrl-C during capture leaves the session mutated and the transport
   playing.~~ **DONE in this pass** — see the Operations table.
2. Concurrent CLI invocations are unserialised — add a filesystem lock scoped to
   the project identity.
3. `assert_constant_tempo` is not called on the `producer-analyze` capture path,
   so a tempo change inside the region silently mis-sizes the capture window.
   The guard already exists — this is a wiring fix, not new work.
4. Disconnect during capture leaves the host mis-routed with no scheduled
   repair, even though reconnect + ref re-resolution already works.
5. Persist `waited_s` from the WAV finalization wait so the 16.6 s hole in the
   recording phase can be attributed.

**P2 — schedule**

Client/server timeout disagreement; HTTP 429 as a distinct retryable class;
missing plugins and frozen tracks as typed non-capturable targets; disk full and
permission denied; long Windows paths; non-ASCII console output; mid-capture
modal dialogs; minimum Live version gate; unretried restore failures.

**P3 — accept for now**

Automation not read; looped-MIDI expansion; non-4/4 region selection coverage;
huge track counts; file deleted mid-analysis; deactivated user devices; Astra
p-max approaching the 180 s ceiling.

None of the remaining P1 items require the execution kernel proposed in the main
audit. Item 5 is a one-line persistence change; item 2 is a lockfile. Item 1 is
already done. A general cancellation capability (aborting Astra or a launch) is
the one P1 that genuinely wants the kernel.
