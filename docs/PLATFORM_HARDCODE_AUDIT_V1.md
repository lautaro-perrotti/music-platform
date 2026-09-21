# PLATFORM_HARDCODE_AUDIT_V1

Date: 2026-09-21

Baseline: `205fd1c` (`MUSIC_ANALYZER_V1 = VERIFIED / FROZEN`)

## Result

`PLATFORM_HARDCODE_AUDIT_V1 = CODE_VERIFIED / LIVE_REVALIDATION_BLOCKED`

The audit covered active runtime code, the Ableton/M4L integration boundary,
capture topology, project onboarding, platform adapters, and static portability
guards. No user-project name, path, track index, genre, BPM, or fixed section
shape is used as authoritative runtime identity in the current producer/analyzer
path.

## Corrections made

- M4L device load/readback no longer relies on fixed `sleep(1.0)`, `sleep(0.8)`
  or `sleep(0.6)` delays. It polls the authoritative Live topology with a
  monotonic deadline and returns `TAP_READBACK_TIMEOUT` when convergence fails.
- Browser resolution now uses a bounded observable-condition poll instead of
  the fixed `1/2/4/8` second backoff sequence.
- `project-ready` now converges on the observed bootstrap state until a bounded
  deadline. It does not retry an arbitrary fixed number of times and never
  retries policy, identity, transport, or collision failures.
- Capture capacity no longer claims `main_sidecar_supported=True` without an
  observed Main tap. Missing capability fails closed.

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
- `REGRESSION_V1`: `34 PASS / 0 FAIL / 0 BLOCKED`
- `git diff --check`: PASS

## Live revalidation blocker

The post-change clean-working-copy Live smoke was attempted twice. The
launcher discovered Ableton, preserved crash recovery, dismissed the recovery
modal, and kept the original source untouched, but Live never exposed TCP
`127.0.0.1:9877` before the bounded deadline (`PORT_CLOSED`). The controlled
Ableton process was then stopped. This is an environment/lifecycle blocker,
not a successful runtime validation, so the audit must not be promoted to
fully `VERIFIED` until a fresh Live session reaches `SESSION_READY` and
`PROJECT_READY` with the new readback waits.

`ADVANCED_PERCEPTION_V1` remains `PROVIDER_LIMITED`; this audit does not add
CLAP, MIR, or semantic providers and does not reopen the frozen Analyzer.
