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
| `PLATFORM_HARDCODE_AUDIT_V1` | VERIFIED / FROZEN | Platform audit closed on the controlled working copy. Trial-modal acknowledgement, manifest-backed project discovery, `PROJECT_READY`, real capture, deterministic Astra-timeout `ABSTAIN`, terminal restoration, and zero musical writes all passed. |

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
| `MUSIC_ANALYZER_V1` | VERIFIED / FROZEN | Real WAV and real Ableton captured-project analysis produce persisted read-only evidence through `AudioAnalysisInput`; staging-WAV finalization, provenance, and terminal restoration passed. Scope limitations remain explicit in the pack. |
| `DEEP_CAUSAL_V2` | VERIFIED / FROZEN | Facts → observed events → read-only SessionState signal graph → candidate hypotheses → before/during/after evaluation. Real controlled-working-copy validation generated 8 events, 88 candidates and preserved unresolved alternatives with zero writes. |
| `ADVANCED_PERCEPTION_V1` | VERIFIED / FROZEN | Local Analyzer plus real optional LAION CLAP audio/text embeddings and EvidenceGraph fusion are read-only. Semantic-ear remains an explicit unavailable limitation. |
| `BASS_MUSICAL_MODEL_V1` | VERIFIED / FROZEN | Deterministic symbolic model over reconciled authoritative MIDI: pitch material, intervals, rhythmic cells, motifs, phrases, and explicit bass-only tonal abstention. No writes or model calls. |
| `HARMONIC_UNDERSTANDING_V1` | IMPLEMENTED / READ-ONLY | Fuses authoritative bass MIDI with optional OTHER/MUSIC-stem chroma into ranked chord hypotheses, harmonic rhythm, tonal candidates, and bass-role relationships. Stem purity is not promoted to ground truth; ambiguous windows and global tonality abstain. |
| `HARMONIC_HUMAN_REVIEW_PACKAGE_V1` | READY / AWAITING HUMAN | Reuses the exact harmonic artifact, verifies source hashes, produces 8 deterministic master/OTHER/BASS listening windows plus typed evidence and static review files. All verdicts remain `PENDING`; no musical correctness is self-certified. |
| `HARMONIC_REVIEW_AUDIO_USABILITY_FIX_V1` | VERIFIED / HUMAN AUDIBILITY PENDING | Context-first review copies use the full reference source, verified QN-origin mapping, deterministic -3 dBFS constant gain, per-file signal audit, and valid local HTML paths. Human playback remains the next boundary. |

## Runtime / agent

| ID | Status | Notes |
|---|---|---|
| `PRODUCER_RUNTIME_V1` | VERIFIED / FROZEN | `producer.analyze_project`. Agent command count = 1. |
| `EVIDENCE_SYSTEM_V2` | VERIFIED / FROZEN | Graph above immutable packs. Fusion keeps contradictions. Limitations propagate. |
| `SAFE_WRITE_FOUNDATION_V2` | VERIFIED / FROZEN | Generic PLAN→readback→KEEP/ROLLBACK. `SET_TRACK_VOLUME` only certified musical action. Analyze stays read-only. |
| `PRODUCER_EXECUTION_V1` | VERIFIED / FROZEN | `MusicPlan → ProductionCompiler → SafeWrite` six-action minimum passed Live readback/rollback on the controlled working copy. |
| `LUCAS_CORE_INTEGRATION_V1` | VERIFIED / FROZEN | Lucas `build_plan_from_prompt` and `critique_track` are called through typed Core handoff; Astra remains internal to Lucas planning. Real Live bounded run verified 2 execution writes, 2 rollback mutations, authoritative readback, and post-analysis with 0 writes. `SAMPLE_LOAD` is Lucas input vocabulary; compiler emits canonical SafeWrite `LOAD_SAMPLE`. No Lucas-owned files were changed. |
| `PRODUCER_INTELLIGENCE_P0` | IMPLEMENTED / NOT VERIFIED | Typed `TrackSpec` and persistent producer decision ledger are available. Astra `track_spec` responses can drive validated dynamic sections; Core intent gating accepts their roles. No new Ableton authority or automatic musical write path. |
| `MIXING_MASTERING_EXECUTION_V1` | VERIFIED / FROZEN | Core-owned bounded mix/master execution maps volume, device load, and parameter intent through ProductionCompiler and SafeWrite. Generic active-region selection fixed the silent-region bug; real working-copy validation produced non-silent comparable captures, measurable mix consequence, 4 verified writes, authoritative readback, rollback, and 0 direct Lucas writes. Validation strategy provenance was `CONTROLLED_FIXTURE`, not `build_plan_from_prompt` output; this freezes execution/audio verification, not autonomous Lucas mix/master planning. |
| `AUTONOMOUS_PRODUCER_ALPHA_V1` | PRODUCTION_PASS_VERIFIED / REVISION_PROVIDER_LIMITED | Real Lucas planner â†’ MusicPlan â†’ ProductionCompiler â†’ SafeWrite â†’ Live readback â†’ capture â†’ Analyzer/CLAP/Evidence completed on the controlled working copy. 23 writes verified, 29 actions explicitly deferred, direct Lucas/Soniq writes 0, unresolved IN_DOUBT 0. Audio sample placement remains deferred because the bridge does not advertise `browser.load`; critique provider timed out, so no revision was invented. |
| `LUCAS_POST_CHANGE_CRITIQUE_PROVIDER` | PROVIDER_LIMITED | The unchanged Lucas critique contract and Core bounded provider failover are implemented and tested. Real mix/master critique attempts used persisted evidence, but the only configured compatible provider (`gpt-6-astra`) timed out; no verdict was fabricated. |
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
an unvalidated external song, Music Flamingo semantic-ear descriptions, and
autonomous Lucas mix/master planning from a real producer request.

Those must register on Producer Runtime when they exist. They do not get a
sidecar orchestrator.

## Physical capture ceiling (honest)

Today: discovered pool; Groove Rider certified 4 sources + Main in one pass.
`PRE_ROLL_QN=16`. Alignment `LIMITED ±52 ms`.
