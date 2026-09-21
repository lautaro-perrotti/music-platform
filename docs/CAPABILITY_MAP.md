# CAPABILITY_MAP.md

Machine source: `python -m copilot.cli capabilities`
(`src/copilot/audio/capability_matrix_v1.py`). This file is the human map.

Statuses: `VERIFIED` | `COMMAND_VERIFIED` | `IMPLEMENTED` | `IN_FLIGHT` |
`WAITING` | `DEFERRED` | `UNSUPPORTED`. Frozen means reopen only for a
reproducible bug.

## Live session

| ID | Status | Notes |
|---|---|---|
| `LIVE_SESSION_READINESS_V1` | VERIFIED / FROZEN | TCP + hello/request-id + light snapshot + identity. Port ≠ session. |
| Working copy policy | VERIFIED | Require a manifest-backed working copy; refuse unprotected originals. Fixture ≠ musical holdout. |
| `CROSS_PROJECT_BOOTSTRAP_V1` | VERIFIED | Infra only. Second run `NO_CHANGES_REQUIRED`. |
| `PROJECT_READY_V1` | VERIFIED | Identity → bootstrap → preflight. |

## Observation

| ID | Status | Notes |
|---|---|---|
| TapProtocol 3 | VERIFIED | Slots 0/1/2. Rec isolation. |
| `LIVE_CAPTURE_BASELINE` | VERIFIED | Real WAV + provenance. Constant tempo. |
| Alignment | LIMITED | ±52 ms musical / ±4 ms inter-view. Not sample-accurate. |
| `CAPTURE_BATCHING_V1` | VERIFIED / FROZEN | Legacy width 2 on TapProtocol 3. `PRE_ROLL_QN=16`. |
| `CAPTURE_HOST_BASELINE_V1` | VERIFIED / FROZEN | Canonical parked state. Live Ext. In+Main is not baseline. |
| `CAPTURE_SCALABILITY_V2` | VERIFIED / FROZEN | Groove Rider 4-source one-pass + Main. Pool counts only `HOST_AVAILABLE`. |
| `RPC_OPTIMIZATION_V2` | VERIFIED / FROZEN | Freshness domains + bulk reads. |
| `ABLETON_MUTATION_PROTOCOL_V1` | VERIFIED / FROZEN | Live Groove Rider compound prepare/restore. Sequential fallback kept. Not for musical writes. |
| `PHYSICAL_DSP_V2` | VERIFIED / FROZEN | Factual measurement layer. No mix-quality judgments. FullMix/LowEnd wrapped, not rewritten. |

## Runtime / agent

| ID | Status | Notes |
|---|---|---|
| `PRODUCER_RUNTIME_V1` | VERIFIED / FROZEN | `producer.analyze_project`. Agent command count = 1. |
| `EVIDENCE_SYSTEM_V2` | VERIFIED / FROZEN | Graph above immutable packs. Fusion keeps contradictions. Limitations propagate. |
| `SAFE_WRITE_FOUNDATION_V2` | VERIFIED / FROZEN | Generic PLAN→readback→KEEP/ROLLBACK. `SET_TRACK_VOLUME` only certified musical action. Analyze stays read-only. |
| `PRODUCER_EXECUTION_V1` | MOCK_VERIFIED / LIVE_PENDING | Additive `MusicPlan → ProductionCompiler → SafeWrite` surface for the six-action minimum. It preserves the frozen foundation contract and requires real Live certification before VERIFIED. |
| `FOUNDATION_INTEGRATION_CHECKPOINT_V1` | VERIFIED | DSP → EvidenceGraph adapter. AnalyzeProject remains read-only. Not a new feature. |
| `PRODUCER_ANALYZE_V1` | COMMAND_VERIFIED | Debug CLI. Fixture `INSUFFICIENT_EVIDENCE` on empty arrangement. |
| `PRODUCER_RUN_V1` | COMMAND_VERIFIED | Autonomous volume only on development working copy. |
| M4L control contract | DOCUMENTED / FROZEN | ANALYZE/STATUS/DIAGNOSIS/PROPOSED ACTION/APPLY/ROLLBACK. APPLY = `SET_TRACK_VOLUME` only. |
| State Trust | VERIFIED (tests + Live smoke) | Tokens, refs, stale plan. See `docs/core/STATE_TRUST.md`. |

## Reasoning

| ID | Status | Notes |
|---|---|---|
| Grounding contract | VERIFIED / FROZEN | No invented measurements/entities. Fail-closed. |
| `ASTRA_REASONING_V2` | VERIFIED / FROZEN | EvidenceView in, grounded MusicDiagnosis out. Astra is not measurement authority. |
| Astra on Groove Rider | RUN (engine) / pack replay BLOCKED | Last Live run stayed `INSUFFICIENT_EVIDENCE`. No persisted pack under `fixtures/frozen/`. |
| MusicPlan gate | VERIFIED | Closed unless evidence + policy allow. |

## Portability

| ID | Status | Notes |
|---|---|---|
| `ENVIRONMENT_AUTONOMY_POLICY_V1` | VERIFIED / CURRENT-HOST | Runtime platform discovery, idempotent Remote Script provisioning, native launch, bridge/readiness lifecycle, and working-copy reconciliation. |
| Windows/macOS/Linux installer | IMPLEMENTED | No secrets. Remote Script + M4L; platform paths are discovered at runtime. |
| Second-machine proof | WAITING | Needs a friend machine. |
| `REGRESSION_V1` | VERIFIED | Offline supported-envelope suite. |

## Waiting on a second real song

| ID | Status |
|---|---|
| `CROSS_PROJECT_MUSICAL_VALIDATION_V1` | WAITING_FOR_EXTERNAL_SONG |
| Musical generalization | UNPROVEN |

Development fixtures and untitled sets are not that test.

## Explicitly not capabilities yet

EQ, compressor, MIDI editing, arrangement editing, unbounded plugin control,
web UI, silent mock success, collapsing producer statuses, autonomous writes on
an unvalidated external song, References, Music Flamingo, CLAP, sample library,
mix, master.

Those must register on Producer Runtime when they exist. They do not get a
sidecar orchestrator.

## Physical capture ceiling (honest)

Today: discovered pool; Groove Rider certified 4 sources + Main in one pass.
`PRE_ROLL_QN=16`. Alignment `LIMITED ±52 ms`.
