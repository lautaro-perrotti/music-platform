# Foundation debt register

Severity:

- `CRITICAL` — can hear the wrong signal, hit the wrong object, use stale
  evidence, false success, silent project mutation, or overwrite assets.
- `HIGH` — correctness / safety / provenance / rollback inside the envelope.
- `MEDIUM` — trust or operability; does not silently lie if status stays LIMITED.
- `LOW` — polish.

Acceptance: `OPEN CRITICAL = 0` and `OPEN HIGH = 0` inside the supported
envelope before Foundation close. Medium/Low only if explicitly accepted.

---

## OPEN CRITICAL

| ID | Debt | Risk | Required test | Status |
| --- | --- | --- | --- | --- |
| C4 | Equivalent captures of REGION_A produced different diagnoses | Core can pick TEMPORAL_MASKING vs NO_ACTION on capture noise | Repeatability + `DIAGNOSIS_UNSTABLE` | OPEN — 5× off-main features stable; diagnosis not run |

---

## OPEN HIGH

| ID | Debt | Risk | Required test | Status |
| --- | --- | --- | --- | --- |
| H4 | Alignment still `REGION_ALIGNMENT_PARTIAL` | Temporal diagnosis can shift with trim | Transport/frame mapping; no onset-as-truth | OPEN — Live click fixture `align_57083fc0`: musical ±52 ms; not this phase |
| H5 | Adapter cannot read send levels | C3 cannot close | Wrap Remote Script `get_send_level` | CLOSED for read API; C3 still OPEN as a naming gate |
| H6 | No capture-host transaction journal | Routing mutations not IN_DOUBT-safe | Disconnect after `set_track_output_routing` | OPEN |
| H8 | No version handshake (Live/Max/Tap/Core) | Run against wrong device silently | `PROTOCOL_INCOMPATIBLE` | OPEN |

---

## OPEN MEDIUM (acceptable only if not promoted)

| ID | Debt | Why not CRITICAL | Status |
| --- | --- | --- | --- |
| M1 | IN_DOUBT proven on mock, not Live TCP | MIDI Live path is VERSION_VERIFIED separately | OPEN |
| M2 | Trust suite 455 s; PASS 1 was 39.7 s | Correctness first; production path no longer runs 5× | OPEN / accepted as CERTIFICATION cost — PRODUCTION_PASS_TIME **25.8 s** ACCEPTABLE; leftover bottleneck is serial fire/stop/Rec until CS reload |
| M8 | Alignment xcorr across different sources | Lag 0.6–2.6 s is content, not clock | OPEN — click fixture Live-run; musical ±52 ms / inter-view ±4 ms; xcorr not used as clock |
| M9 | `move_device` / `get_capture_topology` need Control Surface reload | Script patched+installed; Live process still old | CLOSED — after Live restart, `snapshot_source=topology` |
| M10 | TapProtocol 3 (UDP disconnected) not loaded | Loaded instances still proto 2 | CLOSED — three instances TapProtocol=3 runtime |
| M3 | Leftover MIDI tap on LIVE21 Silent shared Slot 2 with Bass | Rec=0/Device Off did not release `_next_bass.wav` | CLOSED this session — deleted that tap before PASS 1 |
| M4 | `_next.wav` staging names still exist in Max | Collision-safe only if every Slot is unique among loaded taps | OPEN — unique dest `capture_{pass}_slotN.wav`; staging names still shared |
| M5 | Human must drag `.amxd` (browser load unreliable) | Fail-closed, not silent | OPEN / accepted |
| M6 | 48 kHz / groups / warp / sidechain / PDC unread | Already UNSUPPORTED | OPEN / accepted as envelope |
| M7 | No fail-closed Slot collision scan across all loaded taps | Second tap on same Slot holds the WAV after Rec=0 | OPEN — discovered 2026-09-14 |
| M13 | Astra over-abstains on clear temporal/spectral | Safe; conservative producer is preferred now | OPEN / accepted as MODEL QUALITY PARTIAL until real-session diagnosis. Do not prompt-tune | `logs/reason_real_full_revalidated.json`: temporal evidence SUFFICIENT (named 5/5, never commits); spectral SUFFICIENT (named 1/5) |

---

## CLOSED (do not reopen without new evidence)

| ID | Debt | Closed by |
| --- | --- | --- |
| X1 | Diagnosis non-determinism on same WAV | `logs/live3r_perf2.json` same_wav YES |
| X2 | Capture must reroute 14 tracks to hear Kick | PERF-2 single-tap; through-master not used |
| X3 | Analyzer randomly changes mind | Same WAV twice identical; variance is capture |
| X4 | Live MIDI integration missing | LIVE-1 `VERSION_VERIFIED` |
| X5 | Agent rollback was `song.undo()` | Inverses + journal; Live undo log |
| X6 | Adapter could not read send levels | `AbletonTcpAdapter.get_send_level` / `get_track_sends` |
| X7 | Slot missing on loaded tap / shared `_next.wav` | PERF-3 two-tap `65ddbdca4694`; distinct SHA; Slot roundtrip |
| X8 | TapProtocol absent on Live instance | Runtime read TapProtocol=2 on Main + Copilot Capture + Bass |
| X9 | PASS 1 / three-file one-playback missing | `logs/live3r_perf3.json` pass `57127fa19917` |
| X10 | Send 0.0 misread as possible 0 dB | Live send `min=0 max=1`; 0.0 is fader-down; Kick+Bass OFF_MIX_GRAPH this fixture |
| X11 | Main tap not last / MAIN_FINAL lie | Utility → Copilot Audio Tap; pass `706958d9dac8` signal_point MAIN_FINAL |
| X12 | TapProtocol 3 not loaded | Runtime TapProtocol=3 on Main + Copilot Capture + Copilot Capture Bass |
| X13 | get_capture_topology missing in Live process | After Live restart; `snapshot_source=topology`; `get_track_info=0` |
| X14 | Production PASS >30 s / prepare 12 s + stop 6 s fixed waits | Event-driven handle wait; skip Rec=0 and idle stop_clip; per-tap arm timestamps; prod `105747f7c9b8` 25.8 s |
| X15 | Envelope doc claimed TRACK_POST_MIXER / MAIN_FINAL not achieved | `SUPPORTED_CAPTURE_ENVELOPE.md` rewritten 2026-09-15; H10 |
| X16 | Process-scoped stable_id used as persistent identity | `PersistentObjectRef` + Live reconnect `logs/live_state.json`; H1 |
| X17 | Integer revision=0 on reconnect used as stale fence | PROJECT/AUDIBLE/TARGET tokens; PlanEnvelope; H2 |
| X18 | AudioAsset cache disabled / ungated | Scoped audible cache keys; H9 |
| X19 | LLM diagnosis text not schema-grounded | Reasoning pipeline + grounding validator; H7; `logs/reason_eval.json` |
| M11 | First Astra run used JSON mode, not Structured Outputs | Responses API `json_schema` strict; full `logs/reason_real_full.json` 30/30 schema-valid; REAL MODEL GROUNDING VERIFIED |
| M12 | Semantic stability proven on scripted provider only | Full A–F×5 `ACCEPTABLE`; max 2 category/status pairs |
| M13V | Grounding treated limitation `10 ms` as a session measurement | NumericProvenance; capability quote accepted, invented `overlap of 10 ms` still rejected; `logs/reason_real_full_revalidated.json` |

---

## Promotion rule

A capability may leave LIMITED/FAILED only with:

```
CLAIM
STATUS
TEST
REAL EVIDENCE
LIMITATIONS
REGRESSION ADDED
DEBT CLOSED
NEW DEBT DISCOVERED
NEXT DEPENDENCY
```

If a limitation can make the Core hear, identify, or restore the wrong thing,
it stays CRITICAL/HIGH or the capability exits the envelope.
