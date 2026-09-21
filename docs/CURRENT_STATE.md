# CURRENT_STATE.md

Date: 2026-09-21. Runtime > this file when they disagree.

## What the product is

Local-first Ableton copilot. Folder import → working copy → `SESSION_READY` →
bootstrap observation infra → `producer.analyze_project(project)` (one agent
command) → capture Main + isolated sources → DSP → Astra (grounded) → gate.
Analyze does not write the audible mix.

Representative project: Groove Rider working copy, identity `dc08248b…`,
region `AUTO_36_68` (36–68 qn), tempo 126. Four Copilot capture hosts are
parked and `HOST_AVAILABLE`. Track count 34.

## Measured AnalyzeProject (Groove Rider)

| Era | Wall | RPC | RPC wall | Playback passes | Intrinsic audio |
|---|---:|---:|---:|---:|---:|
| First optimized capture era | 254.22 s | 237 | ~110.7 s | 4 | ~91 s |
| `PRODUCER_RUNTIME_V1` | 185.38 s | 174 | 82.70 s | 4 | — |
| `RPC_OPTIMIZATION_V2` (sequential fallback) | 167.55 s | 109 | 54.54 s | 4 | — |
| `ABLETON_MUTATION_PROTOCOL_V1` (two-pass compound) | 130.53 s | 34 | 16.20 s | **2** | **51.15 s** |
| `CAPTURE_SCALABILITY_V2` (one-pass, parked hosts) | **100.82 s** | **23** | **10.68 s** | **1** | **23.67 s** |

Split of the one-pass wall:

```
TOTAL              100.82 s
MODEL / ASTRA       50.34 s
INTRINSIC AUDIO     23.67 s
ORCHESTRATION       15.85 s   (includes SESSION_READY 11.33 s)
ABLETON RPC         10.68 s
CPU + IO             0.28 s
UNATTRIBUTED        ~0
```

Do not credit Astra provider variance to capture scalability. Two-pass Astra
was 47.28 s; this one-pass Astra was 50.34 s.

One shared transport pass: `batch_pass_id=a3ccd6643657`, `batch_size=4`,
`capture.playback_pass` once (29.77 s wall including pre-roll/transport),
journals `_1`…`_4` all `VERIFIED`. Sources: Filter Kick, Open Hi Hat,
High String, Filtered Bassline. Classes: HAS_SIGNAL, HAS_SIGNAL,
NEAR_SILENCE, HAS_SIGNAL. Main sidecar same pass.
`PRE_ROLL_QN=16`. `MUSICAL WRITES=0`. Terminal restored. All four hosts
fresh-read `Resampling` + `Sends Only` + monitor `off`.

Semantic result: `SUCCEEDED`, `INSUFFICIENT_EVIDENCE`, `ABSTAIN`,
`NO WRITE`. Alignment `LIMITED ±52 ms`. Identity still `dc08248b…`.

## Why ~24 s of audio now

1 playback pass × (region 15.24 s + pre-roll 7.62 s) ≈ 22.86 s intrinsic.
Measured exclusive `INTRINSIC_AUDIO` 23.67 s. Two-pass was 51.15 s because
the same region played twice. `PRE_ROLL_QN=16` is unchanged.

## Frozen

`LIVE_SESSION_READINESS_V1`, `CAPTURE_BATCHING_V1`, `PRODUCER_RUNTIME_V1`,
`RPC_OPTIMIZATION_V2`, `ABLETON_MUTATION_PROTOCOL_V1`,
`CAPTURE_HOST_BASELINE_V1`, `CAPTURE_SCALABILITY_V2`, `PRE_ROLL_QN=16`,
`TAP_PROTOCOL4_LIVE_ACTIVATION_V1`, `EVIDENCE_SYSTEM_V2`, `PHYSICAL_DSP_V2`,
`SAFE_WRITE_FOUNDATION_V2`.

`FOUNDATION_INTEGRATION_CHECKPOINT_V1` consolidates those three last
milestones (plus already-frozen runtime/capture) into one repository
state. DSP measurements enter EvidenceGraph via `DspObservation.to_evidence_item(s)`;
relational DSP is `RELATIONSHIP`; non-canonical DSP limitation codes
propagate. AnalyzeProject stays read-only (`MUSICAL WRITES = 0`).
`SET_TRACK_VOLUME` remains the only certified action in the frozen generic
foundation. `PRODUCER_EXECUTION_V1` is now LIVE VERIFIED on the controlled
working copy for the six-action producer minimum: CREATE_TRACK, LOAD_SAMPLE,
DUPLICATE_CLIP_TO_ARRANGEMENT, LOAD_DEVICE, SET_DEVICE_PARAMETER, and
SET_TRACK_VOLUME. Each action passed compiler → SafeWrite → authoritative
readback → rollback, and the final baseline was restored.

`MUSIC_ANALYZER_V1` has a typed, read-only evidence pack, a real local WAV
analysis pass, independent energy/spectral section inference, groove/chroma/
timbre features, optional kick+bass relationship measurements, and separate
`ASTRA_DIAGNOSIS` / `ASTRA_PRODUCER_PLANNER` handoff contracts. `AudioAnalysisInput`
is now the shared ingestion boundary for file and captured-project analysis;
project capture no longer routes through the development-lab preflight contract.
It is VERIFIED / FROZEN for the current factual Analyzer scope. A fresh
controlled Ableton run now reaches `PROJECT_READY`, finalizes shared staging
WAVs, and persists the project pack through the same `AudioAnalysisInput`
boundary.

Latest checkpoint (2026-09-21): the analyzer was run against the persisted
15.238 s Main/Kick/Bass capture from the controlled Groove Rider working copy.
It produced INTRO/GROOVE/OUTRO change-point sections, a MIXED 32-bar
measurement window, bounded kick/bass overlap timing, low-confidence harmony,
evidence references, SHA-256 provenance, and no-write output. Ableton itself
is SESSION_READY / PROJECT_READY after capture-host normalization. The earlier
attempt was `TARGET_SOURCE_UNSUPPORTED`; it
did not close the gate.

The current run supersedes that failed attempt: capture readback, provenance,
terminal restoration, and the no-write invariant passed. The pack still
reports explicit limits: 32-bar windows are measurement windows rather than
section boundaries, LUFS is unavailable in FullMix V1, and source
activity/prominence require isolated sources or separation.

`ADVANCED_PERCEPTION_V1` now has a bounded read-only provider boundary. The
existing Analyzer is exposed as a real local provider, structural regions are
kept separate from section hypotheses, provider availability is explicit,
EvidenceGraph fusion preserves `CONTRADICT`, and distinct reference tokens can
form a `ReferenceIntentBundle` without merging facts. The current real run is
`PROVIDER_LIMITED`: CLAP and semantic-ear providers are unavailable on this
host; the non-semantic embedding stub is not counted as semantic perception.

`PLATFORM_HARDCODE_AUDIT_V1` is now VERIFIED / FROZEN on the controlled
working copy. The Trial modal was acknowledged, the manifest-backed project
reached `PROJECT_READY`, real capture and EvidencePack generation passed, and
a deterministic Astra timeout produced `ABSTAIN` with terminal restoration,
zero journals/transactions, and `MUSICAL WRITES = 0`. The remaining full-suite
failures are environment tests that intentionally expect Live to be down or
the disposable `AI Test` track to be absent.

The lifecycle defect that created repeated `RECOVER_WORK` states is fixed:
the launcher now owns the launched PID, requests normal shutdown, and never
kills an unknown Live session by process name.
Active runtime waits now converge on
observable Live/browser state with bounded deadlines; project bootstrap issues
its mutation once and polls transient state until deadline; managed host identities and protocol values
are classified as internal contracts; and unobserved Main capture capability
fails closed. See `docs/PLATFORM_HARDCODE_AUDIT_V1.md` for the full inventory,
the blocker evidence, and the remaining legacy-fixture boundary.

## Physical DSP (factual, not judgment)

`PHYSICAL_DSP_V2` measures. It does not say muddy / professional / needs
compression. AnalyzeProject capture graph is unchanged. FullMix / LowEnd
are wrapped, not rewritten.

Families: level/dynamics, spectrum, transients, stereo, rhythm facts,
tonal facts (candidates + confidence), timbre, relational overlap,
multi-granularity. Common `DspObservation` contract. Cache key is
audio hash + analyzer id/version + params.

Reported limitations (not faked numbers): no ITU LRA, true-peak 4×
polyphase approximation, Essentia AGPL blocked, librosa not installed,
key is a candidate set, syncopation is onset-phase vs grid, frequency
width is magnitude M/S approximation.

Synthetic baseline (`physical_dsp_v2.json`, 2 s stereo, not Live):
first pass 0.52 s wall / 7 cache misses; second pass 0.005 s / 7 hits.

## Safe writes (generic foundation)

`SAFE_WRITE_FOUNDATION_V2` is the production-write lifecycle on mock Live.
`SET_TRACK_VOLUME` remains the only certified musical action. Unknown
outcome is `IN_DOUBT` — never `FAILED`, never blind retry. AnalyzeProject
stays `MUSICAL WRITES = 0`. This is not capture-host
`ABLETON_MUTATION_PROTOCOL_V1`.

## Astra (evidence-native)

`ASTRA_REASONING_V2` reasons over a scoped EvidenceView. It interprets; it
does not invent measurements. Fusion `CONTRADICT` cannot be picked as fact.
`INSUFFICIENT_EVIDENCE` asks for one small next `EvidenceRequest`, not CLI
or capture commands. Prompt `reason-evidence-view-2`. AnalyzeProject stays
`MUSICAL WRITES = 0`.

Groove Rider pack replay is **not re-seeded**: `fixtures/frozen/` has no
pack body (see README there). Truthful abstention is covered by fixtures.
Do not reopen Live capture to manufacture that pack.

## Not done

- `DEEP_CAUSAL_V2` is currently `FOUNDATION / NOT VERIFIED`: typed causal
  evidence, deterministic grading, separate audio/control paths, stale-state
  rejection and EvidencePack adaptation are implemented read-only. Real
  replay and regression closure remain.
- Groove Rider evidence-pack re-seed into `fixtures/frozen/` (optional; not capture)
- Musical generalization (second real song)
- Reference capture orchestration, Music Flamingo, CLAP, mix, master
- `RUNTIME_LAUNCH_PATH_OPTIMIZATION` (unconditional launcher sleep / backoff)

## Agent command count

AnalyzeProject = **1** high-level call.
