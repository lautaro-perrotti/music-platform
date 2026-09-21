# PLATFORM_HARDCODE_AUDIT_V1

Date: 2026-09-21

Baseline: `205fd1c` (`MUSIC_ANALYZER_V1 = VERIFIED / FROZEN`)

## Result

`PLATFORM_HARDCODE_AUDIT_V1 = VERIFIED / FROZEN`

## Follow-up closure (2026-09-21)

The later controlled-working-copy validation closed the previous Live gate.
After the user dismissed the known Ableton Trial status modal, the correct
manifest-backed Groove Rider copy reached `SESSION_READY` and `PROJECT_READY`.
Real capture, EvidencePack generation, FullMix, and LowEnd completed. The
remaining `PROJECT_MISMATCH` was traced to capture-pool expansion: the run
provisioned `Copilot Capture 3` and `Copilot Capture 4`, changing the project
and audible tokens while preserving the same project identity. Runtime state
bookkeeping was updated only after validating that the topology delta was
exclusively those Copilot-owned hosts; arbitrary mismatches still fail closed.

The deterministic provider-failure regression then exercised timeout, rate-limit,
and parse-failure outcomes. A real Live run with an injected provider timeout
returned `DIAGNOSIS_UNSTABLE` / `ABSTAIN`, `PROJECT_MISMATCH` was absent,
terminal restoration was verified, journals and transactions were empty, and
`MUSICAL WRITES = 0`. Commit: `b27815a`.

The historical blocked attempt below remains as audit evidence; it is no longer
the current milestone status.

The audit covered active runtime code, the Ableton/M4L integration boundary,
capture topology, project onboarding, platform adapters, and static portability
guards. No user-project name, path, track index, genre, BPM, or fixed section
shape is used as authoritative runtime identity in the current producer/analyzer
path.

Scanned scope: 238 production Python files under `src/copilot`, 5 device/build
assets, 103 Python test files, and 56 documentation/baseline files. Production
entrypoints traced: `doctor`, `project-ready`, `producer-analyze`,
`analyze-project`, `regression-v1`, `ProductionCompiler`, `SafeWriteExecutor`,
`DawAdapter`, M4L provisioning, capture bootstrap, and Analyzer ingestion.
The audit used static search/AST checks, focused unit tests, metamorphic fixture
tests, runtime doctor/readiness evidence, and the prior real Live checkpoint.

The lifecycle follow-up identified and fixed a concrete defect: the launcher
could terminate Ableton by process name with `taskkill /F`, creating the crash
recovery state it then encountered on the next launch. The corrected path owns
the launched PID, requests normal shutdown, observes process exit, and keeps
force termination as a last resort for that PID only.

## Finding inventory

Deduplicated findings, rather than raw literal matches:

| Priority | Found | Fixed | Accepted/documented | Blocked/unresolved |
|---|---:|---:|---:|---:|
| P0 | 0 | 0 | 0 | 0 |
| P1 | 5 | 4 | 0 | 1 |
| P2 | 12 | 0 | 10 | 2 |
| P3 | 18 | 0 | 18 | 0 |

The five P1 items are: load/readback fixed sleeps (FIXED), counted bootstrap
retry (FIXED), false Main-capability fallback (FIXED), forced process-name
termination (FIXED), and post-change real Live revalidation (BLOCKED by the
later handshake timeout). No unresolved P0 or Core P1 code finding remains.
The blocked item is an environment gate, not silently counted as a pass.

Every finding is assigned one of these dispositions: `FIXED`,
`ACCEPTED_CONTRACT`, `ACCEPTED_INTERNAL_IDENTITY`, `FIXTURE_ONLY_CONFIRMED`,
`SAFE_DEFAULT_CONFIRMED`, `HEURISTIC_DOCUMENTED`,
`LUCAS_OWNED_NOT_MODIFIED`, `THIRD_PARTY_LIMITATION`, or `BLOCKED`.

## Repository-wide class matrix

| Audit class | Result / disposition |
|---|---|
| Fixture/project/legacy track names | Legacy probes and baselines only; `FIXTURE_ONLY_CONFIRMED`. Active readiness is generic. |
| Sample names and filenames | Display/provenance metadata only; `HEURISTIC_DOCUMENTED`. |
| User display names as authority | State Trust uses persistent refs/tokens; duplicate names fail closed; `FIXED`. |
| Track/device/clip/scene indices | Locators only; stable refs and readback are authoritative; `ACCEPTED_CONTRACT`. |
| Fixed counts and budgets | Explicit bounded resource/safety limits; `SAFE_DEFAULT_CONFIRMED`. |
| Absolute paths/usernames/OS code | Host discovery and platform adapters; static guard passes; `FIXED`. |
| Ableton/Remote Script versions and object types | Handshake/capability and vendor boundary; `THIRD_PARTY_LIMITATION` where unportable. |
| Routing/device/parameter names and URIs | LOM/device contracts with readback; managed tap identity is internal; `ACCEPTED_CONTRACT`. |
| Roles, genre, BPM, 4/4, bar length | No genre truth; BPM/signature defaults are explicit fallback values, never measurement authority; 32 bars are measurement only; `HEURISTIC_DOCUMENTED`. |
| Sample rate/channel/duration/alignment | Audio metadata and bounded tolerances are observed/documented; `ACCEPTED_CONTRACT`. |
| Capture hosts, slots, TapProtocol | Copilot-owned namespace and protocol; collision detection/lifecycle/readback present; `ACCEPTED_INTERNAL_IDENTITY`. |
| Providers/models/GPU/hardware/credentials | Provider availability and secrets are explicit; no GPU requirement; `THIRD_PARTY_LIMITATION` / `SAFE_DEFAULT_CONFIRMED`. |
| Local host/port 127.0.0.1:9877 | Local-only contractual default; handshake remains authoritative and open port is insufficient; `ACCEPTED_CONTRACT`. |
| Timeouts/sleeps/retries/fallbacks | Load/browser waits use observable state + monotonic deadline; capture timing remains bounded transport/file settling; `FIXED`/`ACCEPTED_CONTRACT`. |
| UI/dialog/locale behavior | Known recovery/modal allowlist; unknown modal fails closed; `ACCEPTED_CONTRACT`. |
| Fixtures, mocks, labs, caches, logs | Static guards and entrypoint tracing keep them off the active generic path; `FIXTURE_ONLY_CONFIRMED`. |
| Schema defaults/numeric fallbacks/UNKNOWN | Defaults are labeled fallback and unknown is not coerced to false; `SAFE_DEFAULT_CONFIRMED`. |
| Reference/event/provider/cache identities | Tokens, hashes, evidence IDs and provider versions are used; filenames/events are not identity; `ACCEPTED_CONTRACT`. |
| Write and rollback targets | Compiler → MutationIntent → SafeWriteExecutor → DawAdapter; persistent refs/readback, not names/indexes; `ACCEPTED_CONTRACT`. |
| Lucas-owned integration surface | Read-only audit only; no Lucas files modified; `LUCAS_OWNED_NOT_MODIFIED`. |

## Corrections made

- M4L device load/readback no longer relies on fixed `sleep(1.0)`, `sleep(0.8)`
  or `sleep(0.6)` delays. It polls the authoritative Live topology with a
  monotonic deadline and returns `TAP_READBACK_TIMEOUT` when convergence fails.
- Browser resolution now uses a bounded observable-condition poll instead of
  the fixed `1/2/4/8` second backoff sequence.
- `project-ready` now converges by polling the observed bootstrap state until a
  bounded deadline. The bootstrap mutation is issued once; polling never calls
  `bootstrap_project` again and never retries policy, identity, transport, or
  collision failures.
- Capture capacity no longer claims `main_sidecar_supported=True` without an
  observed Main tap. Missing capability fails closed.

## Known incidents and causality

- `Kick 808 Deep`, `Sub Bass`, and similar names were found only in historical
  evaluation/fixture material; they are not generic readiness requirements.
- `D:/MusicCopilot` is confined to the tap migration normalizer and guards;
  current provisioning uses the host-discovered capture directory token.
- The original fixed sleeps and counted retry were real active-runtime findings
  and are fixed in this checkpoint.
- The two post-change smoke attempts used a fresh manifest-backed copy created
  from the protected source. Before project bootstrap could run, Ableton showed
  the known `RECOVER_WORK` dialog, the autonomy handler selected `No` for that
  controlled copy, and the process never exposed TCP `127.0.0.1:9877`.
- Process inventory showed one Ableton process, no listening port, no zombie
  port, and no duplicate launcher instance. The failure happened before
  `SESSION_READY`, before M4L tap loading, and before `project_ready`; therefore
  the new tap polling and bootstrap observation loop were not on the failing
  execution path. This rules out those changes as the direct cause, while the
  underlying Ableton recovery/startup cause remains unresolved.
- The controlled process was stopped after the bounded deadline. The protected
  source remained untouched. Status stays `LIVE_REVALIDATION_BLOCKED` rather
  than being promoted by inference.

## Accepted constants

These are contractual or safety constants, not assumptions about a user's
music:

- `Copilot Capture*` names: Copilot-managed infrastructure namespace.
- Slots and TapProtocol values: device/runtime protocol contracts, verified by
  Live readback.
- localhost transport defaults: security boundary, overridable by project
  configuration/environment where supported.
- 32-bar analyzer windows: measurement envelope only; never section truth.
- bounded source/host budgets and deadlines: resource and safety limits.
- schema versions, typed statuses, and protocol capability values.

## Findings classified as non-authoritative

- `SC Trigger`, `Drum Loop`, `Kick`, `Bass`, and similar labels are display
  metadata or capture labels. They are not stable identity, project validity, or
  semantic truth. Source selection uses discovered track state and explicit
  capture references.
- Groove Rider names, measured BPM, arrangement regions, fixture paths, and
  track indices remain in legacy probes, tests, and measured baseline documents;
  they are not requirements of the generic onboarding/analyzer path.
- The legacy `live3r_*`, `live22*`, and autonomous evaluation modules contain
  historical fixture-specific probes. They are not invoked by the frozen
  `producer-analyze` path and remain candidates for a separate legacy cleanup.
- The old `D:/MusicCopilot` string exists only in the tap migration normalizer
  and regression assertions; host runtime provisioning uses discovered paths
  and `__COPILOT_CAPTURE_DIR__`.

## Verification

- `tests/test_platform_cleanup_v1.py`: PASS
- `tests/test_portability_cli_v1.py`: PASS
- `tests/test_environment_autonomy_v1.py`: PASS
- capture/bootstrap/project-ready/M4L/analyzer focused suites: PASS
- `tests/test_platform_hardcode_audit_v1.py`: PASS, including rename, reorder,
  extra-track, no-role/no-kick/no-bass, ambiguity, duplicate-name, device-order,
  readback-timeout, browser-timeout, single-bootstrap-issue, and static-guard
  checks.
- `tests/test_ableton_lifecycle_v1.py`: PASS; exact-PID shutdown, controlled
  recovery metadata quarantine, and original-project preservation.
- `REGRESSION_V1`: `34 PASS / 0 FAIL / 0 BLOCKED`
- `git diff --check`: PASS

## Metamorphic results

- Rename user tracks: PASS; readiness unchanged.
- Reorder tracks: PASS; readiness unchanged and indices remain locators.
- Insert unrelated track: PASS.
- No kick / no bass: PASS; readiness remains valid and source-specific evidence
  is limited rather than invented.
- No recognizable musical roles: PASS; mixture-level readiness remains valid.
- Multiple identical candidates: PASS; `TARGET_AMBIGUOUS`, never first-match.
- Duplicate display names: PASS; display name alone does not become identity.
- Reorder host devices: PASS; tap is found by observed device identity/readback.
- Random project name: PASS; no project-name branch.

## Live revalidation blocker

The lifecycle fix was validated once on a new manifest-backed working copy.
Ableton reached `SESSION_READY`: one controlled process, port open, handshake,
request-id behavior, snapshot, and project identity all passed; there was no
relaunch and no recovery modal. The evidence classified the prior state as
`RECOVERY_OF_CONTROLLED_WORKING_COPY`: `CrashRecoveryInfo.cfg` pointed into
`CopilotProjects`, and the earlier launcher had used forced process-name
termination. That metadata and the Crash folder were preserved in recoverable
Copilot-owned quarantine storage; the protected source was untouched.

The subsequent `project-ready` gate did not pass. It observed a listening port
but timed out during the next handshake and correctly returned
`ZOMBIE_PORT`/`NO WRITE`. Ableton's log shows the Remote Script initialized on
9877 and accepted the launcher handshake commands during startup, but the
later readiness probe did not obtain a complete response. The controlled PID
was then closed with `CloseMainWindow`; no force-kill was used in this run.

Because `PROJECT_READY` and the read-only smoke did not complete, the audit
must remain `CODE_VERIFIED / LIVE_REVALIDATION_BLOCKED`. No additional launch
or exploratory retry is authorized in this checkpoint.

`ADVANCED_PERCEPTION_V1` remains `PROVIDER_LIMITED`; this audit does not add
CLAP, MIR, or semantic providers and does not reopen the frozen Analyzer.
