# Capture envelope

Not universal Ableton compatibility.

```
LIVE_CAPTURE_BASELINE          VERIFIED
PRODUCTION_CAPTURE_ENVELOPE    FIXTURE_VERIFIED (this lab set)
ALIGNMENT_ENVELOPE             LIMITED ±52 ms musical / ±4 ms inter-view
CAPTURE HARDENING              STOPPED
```

Evidence: `logs/live3r_prod.json` pass `105747f7c9b8`, `logs/live3r_align.json` pass `align_57083fc0`.
Live 12.4.5 Trial. Constant tempo. WAV sample rate is truth. TapProtocol=3.

`view` and `signal_point` are different fields. Do not collapse them.

## Views

| view | Question | Not |
| --- | --- | --- |
| MASTER_CONTEXT | What is on Main at the declared signal point? | A stem |
| TRACK_ISOLATED | What does this track emit at the declared signal point? | The track inside the mix; solo |
| TRACK_CONTEXT_REMOVAL | What happens to Main when this track is muted? | `mix − track` as a stem |

## Signal points observed

| signal_point | Meaning | Status |
| --- | --- | --- |
| MAIN_FINAL | Copilot Audio Tap is the last Main device | This fixture: Utility → Copilot Audio Tap. Read-back enforced. Else `CAPTURE_SIGNAL_POINT_AMBIGUOUS` |
| MAIN_NOT_FINAL | Tap is not last | Declared when true. Not “the master” |
| TRACK_POST_MIXER | Audio From target Post Mixer, recorded off Main on a capture host (Sends Only) | This fixture: Copilot Capture / Copilot Capture Bass. Kick+Bass `OFF_MIX_GRAPH` |
| TRACK_POST_MIXER_THROUGH_MASTER_CHAIN | Post Mixer → capture host → Main → Master tap | Deprecated path. Not the production isolate |

## LIVE_CAPTURE_BASELINE (VERIFIED)

- Real Live TCP capture, unique WAV + provenance
- Constant-tempo 90 / 120 / 128 / 174, duration = `N * 60 / session_tempo`
- 4/4, 3/4, 6/8 as **quarter_note_position** units (6/8 bar = 3 quarter notes, not 2 dotted-quarter pulses)
- Mute compare as TRACK_CONTEXT_REMOVAL
- Returns: isolated excludes send wet when returns are zeroed; wet is Main
- Mixer restore of properties we change. No `song.undo()`
- Fail closed: tempo automation, unknown routing, ambiguous target, mutated capture, non-finite WAV
- Tap-not-last is refused for MASTER_CONTEXT_FINAL

## PRODUCTION_CAPTURE_ENVELOPE (this fixture)

- Master+Kick+Bass, one playback, unique slots 0/1/2, unique dest files
- Master `MAIN_FINAL`; Kick/Bass `TRACK_POST_MIXER` + `OFF_MIX_GRAPH`
- Journal PREPARED→VERIFIED; Rec=0 after pass; handles released
- Transport restored with song_time read-back (tol 0.08 beats)
- Production PASS 1 **25.8 s wall** / 20.4 s internal (ACCEPTABLE). Physical audio 4.0 s
- Topology snapshot 1 TCP, `get_track_info=0`
- `routing_mutations=0` in steady state
- Fire/stop of session clips is **fixture-specific**. Arrangement-only capture does not fire clips. This lab set still fires session clips `[4,5,7,8,10,14]`
- Batch Rec/fire/stop exists in the Remote Script on disk; Live process still serial until CS reload
- Remaining fixed sleep: region duration wait only

## Alignment (CERTIFICATION only — not per production pass)

Known clicks at region start +1/+2/+3 quarter notes, initial silence, three views.

```
ALIGNMENT_ENVELOPE: ±52 ms / ±2301 frames (musical)
INTER_VIEW:         ±4 ms
sample_accurate:    false
claim:              LIMITED
```

Constant −52 ms bias vs the requested grid. Click spacing is 0.500 s (1 qn at 120).
Kick↔Master content xcorr is **not** the clock.

Capability cache key: Live 12.4.5 / RS protocol 1 / TapProtocol 3 / 44100 / `capture-prod-1`.
Do not re-run the click fixture unless that key changes.

## Session state trust (STOPPED)

Tokens, persistent refs, stale-plan, reconnect, scoped AudioAsset cache.

Evidence: `logs/live_state.json`, `tests/test_state_trust.py`, `docs/core/STATE_TRUST.md`.

Integer `session.revision` is not identity. Plans carry tokens.

Do not invent more identity edge cases unless a real bug appears.

## Still outside the envelope

- Delay Compensation / Reduced Latency When Monitoring: **not readable via LOM**
- Sidechain input wiring via LOM: unsupported. Do not infer pumping
- Groups / warp sets / 48 kHz: not certified. Save the `.als` fixtures if you want those PARTIALs closed
- Tempo automation: fail-closed
- Sample-accurate musical alignment: **not claimed**

## Region language

Internal: `quarter_note_position` (Ableton `current_song_time`).
External: bar / beat-in-bar / subdivision for the active meter.
Never “capture two beats in 6/8” without saying which beat.

Policy this phase: STRICT_REGION. Onset detector is not the capture clock.

## Capture hardening is stopped

Do not invent more capture fixtures unless a real capture bug appears.

State hardening for this envelope is also stopped.

## LLM grounding

```
LLM GROUNDING CONTRACT         VERIFIED
PROVIDER CONTRACT              PASS
CORE SAFETY                    PASS
REAL MODEL GROUNDING           VERIFIED
REAL MODEL SEMANTIC STABILITY  ACCEPTABLE
MODEL QUALITY                  PARTIAL
ZERO MUSICAL WRITES            VERIFIED
```

Contract evidence: `tests/test_reasoning_grounding.py`, `tests/test_reasoning_eval.py`,
`logs/reason_eval.json`. CLI: `copilot reason-eval`.

Real-model: historical `logs/reason_real.json` is JSON-mode (provider contract
FAIL, Core safety PASS, quality NOT_MEASURED). Do not rewrite it.

Current provider: Responses API `json_schema` strict. Smoke + mini + full in
`logs/reason_real_smoke.json` / `logs/reason_real_mini.json` /
`logs/reason_real_full.json`. Full A–F×5 (2026-09-15): 30/30 schema-valid,
17 accepted, 13 fail-closed on quoted `10 ms`, 0 writes.
MODEL QUALITY PARTIAL: CLEAR_TEMPORAL named temporal 5/5 but never committed;
CLEAR_SPECTRAL named spectral 1/5.

LLM reasons. Core owns reality. No LIVE-4. No Ableton writes from this path.

Next: REAL SESSION DIAGNOSIS (3–5 real regions, human listening). Still no writes.
No more large synthetic A–F rounds unless a real bug is confirmed.
