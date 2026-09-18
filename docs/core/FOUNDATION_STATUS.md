# Foundation status

Claim levels: `EXPERIMENTAL` | `FIXTURE_VERIFIED` | `VERSION_VERIFIED` |
`PRODUCTION_ENVELOPE_VERIFIED` | `LIMITED` | `UNSUPPORTED` | `FAILED`.

`VERIFIED` always means: what, which version, which conditions, which artifact.

Runtime > source > docs. Stale docs are called out below.

Target envelope for this register:

```
Ableton Live 12.4.5 Trial
Windows 10 22H2+ (this lab: 10.0.26200)
TCP 127.0.0.1:9877 / Remote Script AbletonMCP
constant tempo
44.1 kHz WAV (session sample rate is truth)
quarter_note_position
no LIVE-4 / no auto EQ / no extra plugins
```

Date of this ingest: 2026-09-14.

---

## Principle

The LLM reasons and proposes. The Core knows real state, measures, authorizes,
executes, and verifies. No measurable musical fact exists because the LLM said
it. No change is successful because the LLM said it worked.

KEEP / ADJUST / ROLLBACK is Core policy, not a model claim.

---

## Capabilities

| Capability | Claim | Status | Evidence | Conditions | Known limitation |
| --- | --- | --- | --- | --- | --- |
| Live TCP connection | health_check ok | `VERSION_VERIFIED` | `logs/ableton_detect.json`; runtime 2026-09-14 | Live 12.4.5 Trial, AbletonMCP, 9877 | Not “all Live 12” |
| Typed Remote Script tools | TCP JSON commands only | `VERSION_VERIFIED` | `vendor/abletonmcp_remote_script`; `src/copilot/daw/protocol.py` | This script build | No `eval` / no arbitrary LOM from LLM |
| LIVE-1 MIDI write + read-back | create clip + notes | `VERSION_VERIFIED` | `logs/live_slice1.json` | Live 12.4.5 Trial | One fixture set |
| Transaction journal | persist + invert | `VERSION_VERIFIED` | `logs/live_slice1.json`, `logs/live_undo.json`; `tests/test_journal_recovery.py` | MIDI slice | Capture path does not use the same journal |
| Agent rollback (not song.undo) | delete created track | `VERSION_VERIFIED` | `logs/live_undo.json`; mock + Live MIDI | MIDI create/delete | Not revalidated on capture-host routing |
| IN_DOUBT / no blind retry | lost ack | `FIXTURE_VERIFIED` | `tests/test_write_indoubt.py` | Mock only | Not injected on Live TCP |
| Concurrency / stale revision (tools) | expected_revision mismatch | `FIXTURE_VERIFIED` | `tests/test_revision_semantics.py` | Mock | Capture logs `revision=0` on first snapshot of a new process |
| Session identity in-process | reorder / rename / insert | `FIXTURE_VERIFIED` | `tests/test_identities.py`, `tests/test_identity_adversarial.py`, `tests/test_state_trust.py` | Mock + Live smoke | Runtime `stable_id` is connection-scoped; persist via `PersistentObjectRef` |
| Master tap capture | real WAV + provenance | `VERSION_VERIFIED` | `logs/live3r_prod.json` pass `105747f7c9b8` | Live 12.4.5, tap last on Main | `MAIN_FINAL` this fixture (Utility → Tap) |
| WAV validate | header / finite / duration | `FIXTURE_VERIFIED` | `src/copilot/audio/live_capture.py` `validate_wav`; live22 logs | Constant tempo | Not sample↔beat certified |
| DSP determinism | same WAV → same structured diagnosis | `FIXTURE_VERIFIED` | `logs/live3r_perf2.json` `same_wav`; `tests/test_lowend_diagnosis.py` | Analyzer current; REGION_A WAVs | Not property-tested for all SR / NaN paths as a release gate |
| Diagnosis stability policy | do not pick a side | `FIXTURE_VERIFIED` | `diagnose.unstable_insufficient`; unit test | Unit + policy | Capture-to-capture still `NOT_RUN` on off-main path |
| Off-direct-Main Post Mixer single-tap | Kick → capture track → WAV | `FIXTURE_VERIFIED` | `logs/live3r_perf2.json` `MINIMAL_EXPERIMENT=PASS` | LIVE22 Kick, Copilot Capture, 0–8 qn, 120 BPM, Monitor In, Sends Only | This fixture only |
| Slot parameter in Live | host sees Rec + Slot + TapProtocol | `FIXTURE_VERIFIED` | Runtime 2026-09-14 after user re-drop: Master, Copilot Capture, Copilot Capture Bass | Live 12.4.5 Trial | Browser load still fails |
| Slot write/read-back | set 0/1/2, read matches | `FIXTURE_VERIFIED` | `logs/live3r_perf3.json` `slot_roundtrip` | Master 0, Capture 1, Bass 2 | Not a Live UUID |
| Capture-track sends read | all levels ≤ 1e-5 | `FIXTURE_VERIFIED` | Kick + Bass hosts `*_sends_all_silent=true` | This set | Not a general OFF_MIX_GRAPH cert |
| TapProtocol handshake | read protocol = 3 | `FIXTURE_VERIFIED` | Runtime 2026-09-15: three loaded instances TapProtocol=3; `logs/live3r_prod.json` | Live 12.4.5 Trial after re-drop + restart | Disk file is not the loaded instance |
| Rec=0 closes sfrecord~ | exclusive open after stop | `FIXTURE_VERIFIED` | `REC CLOSE` + PASS 1 `staging_release=moved` | After leftover LIVE21 Silent tap deleted | A second tap on the same Slot still holds the file after Rec=0 |
| Two-tap independent files | one playback, two WAVs | `FIXTURE_VERIFIED` | pass `118ef73206e7` | REGION_A 0–8 qn, 120 BPM | Kick isolate is `OFF_DIRECT_MAIN` this fixture |
| PASS 1 Master+Kick+Bass | one playback, three WAVs | `FIXTURE_VERIFIED` | `logs/live3r_prod.json` pass `706958d9dac8`; journal VERIFIED; also earlier trust `0a0d0c656fde` | REGION_A 0–8 qn, proto 3 | Production path, not 5× suite |
| Tap inventory + slot ownership | all loaded taps; duplicate Slot fail-closed | `FIXTURE_VERIFIED` | `logs/live3r_trust.json`; `tests/test_tap_trust.py` | This set: 3 taps, slots 0/1/2 unique | Unit test covers Silent+Bass collision |
| Stale tap reconciliation | Rec=0, close, exclude; no silent delete | `FIXTURE_VERIFIED` | Trust run: no leftovers; fail-closed TAP_STALE_ARMED | This set empty residuals | Residual same-Slot still TAP_SLOT_COLLISION |
| LOM Rec isolation | Rec=1 on A does not arm B/C | `FIXTURE_VERIFIED` | `UDP CROSS-TALK TEST.lom` | Three loaded taps | Proto 3 now loaded; LOM test not re-run |
| UDP instance isolation | UDP 19877 cannot arm other taps | `LIMITED` | Proto 3 loaded (UDP disconnected from Rec in patcher). Injection not re-run on production | Production does not recertify | Keep LIMITED unless a certification run is needed |
| File ownership | unique dest; no overwrite | `FIXTURE_VERIFIED` | `staging_release=moved`; unit CAPTURE_PATH_COLLISION | pass_id+slot dest | Max staging names still `_next*.wav` |
| Capture journal | PREPARED→VERIFIED; incomplete ≠ valid | `FIXTURE_VERIFIED` | `logs/capture_journal/0a0d0c656fde.jsonl`; unit IN_DOUBT | This runner | Not IN_DOUBT-injected on Live TCP drop |
| State restore | Rec=0, transport stopped, song_time read-back | `FIXTURE_VERIFIED` | pass `706958d9dac8`: saved song_time 26.459, playing false → read-back identical, error 0.0 beats (tol 0.08) | This production PASS | Mutation-set only; not a full Session restore |
| OFF_MIX_GRAPH / sends silent | 0.0 is mixer min (−inf), not 0 dB | `FIXTURE_VERIFIED` | Kick+Bass sends `min=0 max=1 level=0.0`; `routing_mutations=0` | Capture hosts this set | Not a universal Live send cert |
| MAIN_FINAL | tap last on Main | `FIXTURE_VERIFIED` | Devices: Utility → Copilot Audio Tap; signal_point MAIN_FINAL on master WAV | This set after user reorder | Do not infer from disk |
| Capture alignment | same region across three WAVs | `LIMITED` | `logs/live3r_align.json` pass `align_57083fc0`; known clicks at +1/+2/+3 qn | Live 12.4.5, TapProtocol=3, 44.1 kHz, `capture-prod-1` | Musical ±52 ms / ±2301 frames (constant −52 ms). Inter-view ±4 ms. Not sample-accurate. Kick↔Mix xcorr is not the clock. CERTIFICATION only |
| Capture repeatability | 5× PASS 1 feature stability | `FIXTURE_VERIFIED` | Kick RMS spread 0.0006; Bass 0.0003; Kick events=2 all runs | Off-main REGION_A | CERTIFICATION only — not production workflow |
| Performance envelope | PASS1 < 30 s production | `FIXTURE_VERIFIED` | `logs/live3r_prod.json` pass `105747f7c9b8`: **25.8 s wall** / 20.4 s internal. GRADE ACCEPTABLE | 4 s × 3 views proto 3; serial Rec/fire/stop (batch RS on disk, not live in process) | Remaining: serial fire×6 + stop_clip×6 + Rec serial. One CS reload enables batch. Do not recertify 5× |
| Context-removal ≠ stem | mute-on-Main | `FIXTURE_VERIFIED` | `docs/audio/SUPPORTED_CAPTURE_ENVELOPE.md`; live22 | Declared semantics | Not default PASS 1 |
| Through-master isolate | 14-track reroute | `LIMITED` / deprecated | live3r / live3r_perf | Old path | Not the normal path |
| Tempo automation | map qn → frames | `UNSUPPORTED` | Fail-closed in capture | — | Constant tempo only |
| PDC / Delay Compensation read | LOM | `UNSUPPORTED` | Envelope doc; not readable | — | Manual config limitation |
| Sidechain wiring | LOM set SC | `UNSUPPORTED` | live22e PARTIAL | — | Do not infer pumping |
| Groups / warp / 48 kHz | fixtures | `UNSUPPORTED` | Envelope PARTIAL | — | Need saved `.als` |
| LIVE-4 / auto mix changes | EQ, MIDI fix | `UNSUPPORTED` | Project gate | — | Frozen until real-session diagnosis survives |
| LLM grounding contract | schemas/validators/fail-closed | `FIXTURE_VERIFIED` | `tests/test_reasoning_grounding.py`; `tests/test_reasoning_eval.py`; `logs/reason_eval.json` | Low-end; `reason-lowend-1` / `music-diagnosis-reason-1`; scripted + adversarial | This is the contract that contains the model, not the model itself |
| Real-model grounding | one LLM inside that contract | `VERIFIED` | `logs/reason_real_full.json` (A–F×5); historical `logs/reason_real.json` is JSON-mode only | `gpt-6-astra` Responses API `json_schema` strict | 30/30 schema-valid; 17 accepted / 13 fail-closed; 0 invented measurements/entities accepted; 0 writes. MODEL QUALITY is PARTIAL (separate axis) |
| Real-model semantic stability | same input × 5 | `ACCEPTABLE` | `logs/reason_real_full.json` | Do not prompt-tune toward one answer | Max 2 category/status pairs (CLEAR_SPECTRAL). CLEAR_TEMPORAL action family 2 |
| Grounded reasoning pipeline | OBSERVE → REASON → DIAGNOSE → CANDIDATE | `FIXTURE_VERIFIED` | `src/copilot/reasoning/pipeline.py`; fixtures A–F | LLM never writes Ableton; Core validates before ACCEPTED | Human labels are schema-only; not a unique musical ground truth |
| Cache / audio_revision | reuse assets | `FIXTURE_VERIFIED` | `tests/test_state_trust.py` keyed by project + scoped audible token; Kick volume does not invalidate Vocal isolate | Mock + contract in `docs/core/STATE_TRUST.md` | Live reuse of a real WAV not re-run this slice; incorrect reuse fail-closed |
| Version handshake | Live/Max/Tap/Core | `LIMITED` | TapProtocol=3 loaded; Remote Script batch `get_capture_topology` live after restart | This set | Max version still unread |
| Project identity | Live set path token | `FIXTURE_VERIFIED` | `logs/live_state.json`: `live_set_path` `Sin título.als`; mismatch fail-closed in mock | Live 12.4.5 this lab set | Untitled sets still have a path; structural fallback is LIMITED |
| Canonical state tokens | PROJECT / AUDIBLE / TARGET SHA-256 | `FIXTURE_VERIFIED` | `tests/test_state_trust.py`; Live smoke tokens on topology snapshot | `state-canon-1` | Integer revision is in-process only |
| Reconnect identity | PersistentObjectRef without runtime id | `FIXTURE_VERIFIED` | `logs/live_state.json` RECONNECT Kick+probe RESOLVED; incarnation changed | Same Live Set, Core disconnect/connect | Control Surface reload not separately smoked |
| Stale plan rejection | token compare, no write | `FIXTURE_VERIFIED` | mock volume/reorder/unrelated; Live volume `STALE_PLAN` | PlanEnvelope | No MusicPlan autonomy |
| Ambiguity fail-closed | duplicate fingerprints | `FIXTURE_VERIFIED` | mock two identical Kick tracks → `TARGET_AMBIGUOUS` | Mock | Not injected as a Live duplicate this slice |
| Real-session musical eval | A/B/C review | `LIMITED` | `logs/live3r/`; AWAITING_HUMAN_REVIEW | Lab set, not a song | Do not auto-VERIFIED |
| Production music diagnosis | general | `UNSUPPORTED` | Explicitly not claimed | — | — |

---

## Stale documents (do not treat as current)

| Doc | Stale claim | Superseded by |
| --- | --- | --- |
| `docs/discovery/LIVE_STATUS.md` | AUDIO CAPTURE `NOT_STARTED` | LIVE-2 / 2.2 / 3 / 3R logs |
| `docs/audio/SUPPORTED_CAPTURE_ENVELOPE.md` (pre-2026-09-15) | `TRACK_POST_MIXER` “not achieved”; `MAIN_NOT_FINAL` | `logs/live3r_prod.json` MAIN_FINAL + OFF_MIX_GRAPH Kick/Bass |
| `docs/architecture/TARGET_ARCHITECTURE.md` (pre-2026-09-14) | Live tap still blocked | Same + this file |

---

## Artifact hashes

| File | SHA-256 | Note |
| --- | --- | --- |
| `devices/Copilot Audio Tap.amxd` | `64756b07396ba35338816253fa0ea1db00814e0f475328709ea3294da27f2bf4` | Rec/Slot/TapProtocol=2 + Rec=0 `close`; 12362 bytes |
| User Library copy | rebuilt this PERF-3 run | Disk file is not the loaded Live instance |
| Live loaded instance | Rec, Slot, TapProtocol=3 on Main, Copilot Capture, Copilot Capture Bass | Rec=0; MAIN_FINAL; topology live |

---

## Capture hardening (STOPPED)

For the documented envelope (Live 12.4.5 Trial, TapProtocol=3, 44.1 kHz,
REGION_A 0→8 qn, Master+Kick+Bass, this fixture):

- Production PASS 1 **25.8 s wall** (ACCEPTABLE; was 35.6 s)
- State restore **0.0 beat error**
- Alignment **LIMITED** ±52 ms musical / ±4 ms inter-view — not FRAME_GROUNDED, not sample-accurate
- Batch Rec/fire/stop is in the vendored Remote Script + User Library; **Live process still lacks those commands** until one Control Surface reload / Live restart

Do not invent more capture fixtures. Do not recapture. Do not run LIVE-4.

Session state trust (this envelope): Live smoke `logs/live_state.json` **ok**.
Tokens + stale-plan + reconnect + scoped cache: `FIXTURE_VERIFIED`.
See `docs/core/STATE_TRUST.md`.

`CAPTURE FOUNDATION` stays **not CLOSED** (alignment LIMITED ±52 ms).
State hardening for the documented envelope is **STOPPED**.

---

## LLM grounding contract (VERIFIED)

```
LLM GROUNDING CONTRACT         VERIFIED
PROVIDER CONTRACT              PASS
CORE SAFETY                    PASS
REAL MODEL GROUNDING           VERIFIED
REAL MODEL SEMANTIC STABILITY  ACCEPTABLE
MODEL QUALITY                  PARTIAL
ZERO MUSICAL WRITES            VERIFIED
```

The contract contains the model. It is not the model's behavior.

Pipeline: Core evidence → structured prompt → ReasoningProvider → schema
→ measurement grounding → entity grounding → confidence caps → ACCEPTED
`MusicDiagnosis`. LLM output is never authoritative. Candidate actions are
not MusicPlans.

Prompt `reason-lowend-1`. Schema `music-diagnosis-reason-1`. Alignment LIMITED
±52 ms is injected and enforced (`UNSUPPORTED_PRECISION` for 5–10 ms claims).

Fixtures A–F (synthetic, no new audio). Adversarial Serum/63 Hz/7 collisions/
8 ms rejected by Core. Scripted same-input stability 1.0 is **not** real-model
stability.

Real-model gate: `copilot reason-real --gate smoke|mini|full`.

Historical `logs/reason_real.json` (2026-09-15 02:51, chat `json_object`):
CORE SAFETY = PASS. PROVIDER CONTRACT = FAIL. MODEL QUALITY = NOT_MEASURED.
Do not rewrite that log.

After Responses API `text.format.type=json_schema` strict (enum unchanged):
smoke CLEAR_NO_ACTION: structurally valid, ACCEPTED, `NO_ACTION_REQUIRED`.
mini A–F ×1: 6/6 schema-valid, 6/6 Core-accepted.
full A–F×5 (`logs/reason_real_full.json`, 2026-09-15 03:40, ~25.2 min, 30 HTTP):
schema_valid_rate 1.0; 17 ACCEPTED / 13 REJECTED `LLM_GROUNDING_VIOLATION`;
all 13 were `ungrounded duration 10 ms` (quoting the 5–10 ms limitation;
validator only exempts ±52 ms). 0 accepted invented measurements/entities.
0 writes.

Fixture readout (category/status ×5):
- CLEAR_NO_ACTION: 5/5 `NO_ACTION_REQUIRED` / `NO_CHANGE` (did not invent problems)
- CLEAR_TEMPORAL: 5/5 `TEMPORAL_MASKING` + `INSUFFICIENT_EVIDENCE` (names temporal, never commits)
- CLEAR_SPECTRAL: 1/5 `SPECTRAL_MASKING`, 4/5 `INSUFFICIENT_EVIDENCE`; status `INSUFFICIENT` 5/5
- AMBIGUOUS: 5/5 `INSUFFICIENT_EVIDENCE` LOW (did not force certainty)
- CONTRADICTORY: 5/5 `INSUFFICIENT_EVIDENCE` LOW (abstained)
- HALLUCINATION_TRAP: 5/5 `INSUFFICIENT_EVIDENCE`; 0 accepted hallucinations

Do not reopen capture / alignment / identity / revision / cache / reconnect
unless a correctness bug appears. Do not broaden beyond low-end yet.
Do not prompt-tune toward a desired fixture answer.
Do not run another large synthetic A–F round unless a real bug is confirmed.

---

## Foundation acceptance (not closed)

Still not `VERSION_VERIFIED` for Foundation close:

TRANSACTION SAFETY (capture), ROLLBACK (capture), ALIGNMENT ENVELOPE (LIMITED ±52 ms),
VERSION HANDSHAKE.

LLM GROUNDING CONTRACT is `VERIFIED`. REAL MODEL GROUNDING is `VERIFIED`.
MODEL QUALITY is `PARTIAL` (over-abstention on clear temporal/spectral).

DSP DETERMINISM (same WAV) is `FIXTURE_VERIFIED` only.

---

## Producer Runtime

`PRODUCER_RUNTIME_V1` — one high-level call `producer.analyze_project(project)`.
Canonical `producer-analyze` remains callable. Capture source batching frozen at 2.
Next: RPC_OPTIMIZATION_V2 (Ableton round trips) and ROADMAP_100.

---

## Next dependency

**REAL SESSION DIAGNOSIS** — 3–5 real musical regions, frozen DSP thresholds,
grounded real-model reasoning, human listening comparison. Still no writes.

Pending: **REAL SESSION DIAGNOSIS — PHASE 1** on a real song.
Live currently has the lab set (`Sin título.als`). Real candidate on disk:
`C:\Users\lsper\Desktop\pista Project\pista.als`. Do not capture the lab set
as real music. Inspect: `copilot session-diagnose`. Still no writes.

Only after real-session diagnosis survives:

MusicPlan → LIVE-4 → reversible intervention → recapture → Verification →
KEEP / ADJUST / ROLLBACK.
