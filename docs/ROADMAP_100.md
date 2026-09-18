# ROADMAP_100.md

Sequenced work **on this runtime**. Not a second product. Items 1–42 are done
on Groove Rider / fixtures unless noted. Later items are capabilities to
register, not invitations to fork orchestration.

Legend: `[x]` done/frozen · `[~]` in flight · `[ ]` not started · `[!]` waiting
on a human/environment.

## A. Foundation (done)

1. [x] Local-first copilot loop: observe → reason → (optional) typed write → verify
2. [x] `DawAdapter` + Ableton TCP wrap, localhost only
3. [x] Typed protocol, no eval / no arbitrary LOM from the LLM
4. [x] Handshake + `request-id`
5. [x] `LIVE_SESSION_READINESS_V1` (port ≠ session) — FROZEN
6. [x] SessionState as canonical, DAW-independent snapshot
7. [x] Runtime object ids vs persistent refs
8. [x] State Trust tokens (project / audible / target)
9. [x] Plan envelope + stale-plan fail-closed
10. [x] `IN_DOUBT` / no blind retry (MIDI path; capture journals exist)
11. [x] Agent transaction journal + invert (MIDI slice)
12. [x] Working-copy policy; refuse originals
13. [x] Windows installer (venv, Remote Script, M4L, no secrets)
14. [x] macOS installer path
15. [x] `doctor` / capability matrix CLI
16. [x] Offline `regression-v1`
17. [x] M4L command contract documented and frozen (volume-only APPLY)

## B. Capture envelope (done / limited)

18. [x] Master tap `MAIN_FINAL` with last-device read-back
19. [x] Capture host tracks (Copilot Capture + Bass)
20. [x] TapProtocol 3
21. [x] Unique slots 0/1/2
22. [x] Rec isolation (LOM + production Rec=0)
23. [x] Staging unique dest + exclusive open
24. [x] `OFF_MIX_GRAPH` / silent sends on hosts
25. [x] Views: MASTER_CONTEXT vs TRACK_ISOLATED vs CONTEXT_REMOVAL
26. [x] Constant-tempo duration math (`N * 60 / tempo`)
27. [x] WAV validate (header / finite / duration)
28. [x] Capture journal PREPARED→VERIFIED
29. [x] Transport restore with song_time read-back
30. [x] Alignment certified LIMITED ±52 ms — do not recertify for sport
31. [x] DSP FullMix + LowEnd, deterministic on same WAV
32. [x] Diagnosis stability: do not pick a side when unstable
33. [x] `PRE_ROLL_QN = 16` — FROZEN
34. [x] `CAPTURE_BATCHING_V1` (2 sources + Main / pass) — FROZEN
35. [x] Arrangement-region capture (Groove Rider `AUTO_36_68`)
36. [x] Terminal verification after analysis

## C. Runtime (done)

37. [x] Producer Runtime kernel (graph, registry, context, resources)
38. [x] `producer.analyze_project` — FROZEN
39. [x] CLI `analyze-project` thin adapter (command count 1)
40. [x] ProjectReadView V1 then V2 (domain freshness)
41. [x] Bulk reads / `READ_CAPTURE_HOSTS_STATE` via `get_tracks_info`
42. [x] `RPC_OPTIMIZATION_V2` — FROZEN (109 RPC / 54.54 s RPC wall)
43. [x] Performance traces with category split, UNATTRIBUTED ≈ 0
44. [x] Precision-parity harness (timing excluded)
45. [x] Constitution (`AGENTS.md` + this map)

## D. Ableton mutations (now)

46. [x] Mutation inventory of temporary host writes
47. [x] Whitelisted MutationBatch contract (no arbitrary execution)
48. [x] Durable pre-state before any compound send
49. [x] Project-identity guard on compound request
50. [x] Per-step results: APPLIED / FAILED / NOT_ATTEMPTED / UNKNOWN
51. [x] Stop dependent steps on failure; no fake atomicity
52. [x] `IN_DOUBT` on disconnect/timeout-after-write; no blind resend
53. [x] `prepare_capture_hosts` compile + one mutation RPC when advertised
54. [x] `restore_capture_hosts` compile + exact restore + bulk verify
55. [x] Per-host journals survive compound transport
56. [x] Sequential fallback if Remote Script lacks capability
57. [x] Handshake advertises `compound.temporary_mutation` on the running Live process
58. [x] Groove Rider before/after with Live actually executing compound batches
59. [x] Do **not** route musical EQ/MIDI through this batch

## E. Launch / environment (later, authorized)

60. [ ] `RUNTIME_LAUNCH_PATH_OPTIMIZATION` (launcher sleep, `PROBE_BACKOFF`)
61. [ ] Measure launch only with explicit Live restart authorization
62. [!] Second-machine portability proof
63. [!] Live 12 non-Trial / other OS recertify (do not claim “all Live 12”)

## F. Intrinsic audio (needs hardware/topology)

64. [x] Specify N-host capture architecture (`capture_scalability_v2`)
65. [x] Widen M4L slot selector 0–8 (TapProtocol 4; slots 0–2 keep V3 filenames)
66. [x] Lazy Copilot-owned host bank (`Copilot Capture {n}`)
67. [x] One-pass Groove Rider Live measurement (4 sources + Main)
68. [x] Recertify isolation, slots, journals, terminal on N-host
69. [ ] Recertify alignment key if TapProtocol/RS/sample-rate changes
70. [x] Keep `PRE_ROLL_QN=16` unless a new observation contract is written

Capture / Ableton mutation / RPC / Producer Runtime are **FROZEN**. Next work
is three parallel foundations that must not mix with capture:

- ~~`PHYSICAL_DSP_V2`~~ **VERIFIED / FROZEN**
- ~~`EVIDENCE_SYSTEM_V2`~~ **VERIFIED / FROZEN**
- ~~`SAFE_WRITE_FOUNDATION_V2`~~ **VERIFIED / FROZEN**

`FOUNDATION_INTEGRATION_CHECKPOINT_V1` consolidates the three into one
repository state. `ASTRA_REASONING_V2` starts after that commit.

Then `ASTRA_REASONING_V2` (after DSP + Evidence), then
`DEEP_CAUSAL_DIAGNOSIS_V2`, then `CROSS_PROJECT_CERTIFICATION`.
Do not start Music Flamingo / CLAP / mix / master until that chain closes.

## G. Astra / reasoning (separate frontier)

71. [ ] Trace already splits prompt / HTTP / parse / grounding — keep it
72. [ ] Evidence compression / retrieval without dropping EvidenceRefs
73. [ ] Incremental reasoning across regions (no silent context drop)
74. [ ] Model routing by task (diagnosis vs plan vs critique)
75. [ ] Quality work **inside** the grounding contract (fail-closed stays)
76. [ ] Do not optimize Astra by weakening pack facts
77. [ ] Optional local model slot — same schemas

## H. Musical generalization

78. [!] `CROSS_PROJECT_MUSICAL_VALIDATION_V1` on one unseen real song
79. [ ] Decide which capture/DSP claims survive off Groove Rider
80. [ ] Holdout vs development working copy remains policy
81. [ ] `ACTION_NOT_AVAILABLE` on external songs until 78 passes
82. [ ] First gated `SET_TRACK_VOLUME` on a non-dev song only after 78+gate

## I. MIDI / devices as evidence (read)

83. [ ] `READ_MIDI` on AnalyzeProject when `HARMONIC_CONTEXT` requested
84. [ ] `READ_DEVICES` / `READ_ROUTING` when `DEVICE_CAUSAL_CONTEXT` requested
85. [ ] EvidenceGraph domain invalidation already exists — use it on those nodes
86. [ ] Do not treat unread MIDI as silence

## J. References, search, generation (new capabilities)

87. [ ] ReferenceAnalysis as a Producer task (not a sidecar script)
88. [ ] Music Flamingo / CLAP behind the same evidence + license matrix
89. [ ] Sample library index + search as Producer tasks
90. [ ] License/matrix enforcement (`docs/licenses/MODEL_LICENSE_MATRIX.md`)
91. [ ] No generated audio into Live without provenance + working-copy policy

## K. Production writes (separate certification)

92. [ ] MusicPlan vocabulary beyond volume (EQ, dynamics) — new milestone each
93. [ ] Audible-effect verification required before KEEP
94. [ ] MIDI correction — new milestone, not MutationBatch V1
95. [ ] Arrangement edits — new milestone
96. [ ] Mix task on Producer Runtime
97. [ ] Master task on Producer Runtime
98. [ ] Rollback/read-back for each new write class
99. [ ] Human KEEP / ADJUST / ROLLBACK remains Core policy, not a model claim
100. [ ] Stop. Do not start LIVE-4 / auto-mix until 78 and write certification exist.

## Operating rule

Ship the next numbered open item that unblocks the current bottleneck.
Do not open J–K while D is unfinished unless a human explicitly reorders.
Do not open F by shortening pre-roll. Do not open G to hide a failed D.
