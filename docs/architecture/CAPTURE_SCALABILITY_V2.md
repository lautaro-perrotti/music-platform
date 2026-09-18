# CAPTURE_SCALABILITY_V2

Status: **VERIFIED / FROZEN** on Groove Rider Live (`AUTO_36_68`, identity
`dc08248b…`). One playback pass recorded 4 sources + Main.
`PRE_ROLL_QN=16`. `MUSICAL WRITES=0`. Depends on
`CAPTURE_HOST_BASELINE_V1` (canonical parked restore baseline) and
`TAP_PROTOCOL4_LIVE_ACTIVATION_V1` (Slot 0–8).

Do not reopen unless a reproducible bug appears. Do not mix Astra or
Producer Runtime work into this frontier.

## Live one-pass (acceptance)

| Field | Two-pass baseline | One-pass |
|---|---|---|
| Playback passes | 2 | **1** |
| Sources | 4 | 4 |
| Intrinsic audio | 51.15 s | **23.67 s** |
| RPC wall | 16.20 s | 10.68 s |
| Astra | 47.28 s | 50.34 s (not credited) |
| Total wall | 130.53 s | 100.82 s |
| Journals | 4 VERIFIED | `a3ccd6643657_1`…`_4` VERIFIED |
| Signal classes | HAS_SIGNAL, HAS_SIGNAL, NEAR_SILENCE, HAS_SIGNAL | same |
| Alignment | LIMITED ±52 ms | LIMITED ±52 ms |
| Restore | parked/off-main | canonical parked (Resampling + Sends Only + off) |

Failed one-pass `de45b2377087` (Ext. In restore) is **not** the baseline.
Those journals stay `FAILED`.

## Root cause of the old 2-source ceiling

Not a comment and not Producer Runtime:

1. `devices/Copilot Audio Tap.maxpat` used `sel 0 1 2` and three global files
   (`_next.wav`, `_next_kick.wav`, `_next_bass.wav`).
2. Slot live.numbox `parameter_mmax = 2`.
3. Bootstrap provisioned exactly two hosts (`Copilot Capture`, `Copilot Capture Bass`).
4. `SOURCE_CAPTURE_BATCH_V1.MAX_SOURCES_PER_PASS = 2`.

Slot 0 is Main. Two source slots remain. Same-slot taps collide on one WAV.

## Selected architecture

**B + D:** extend slot addressing on the existing tap (TapProtocol 4) and
provision a Copilot-owned host bank lazily from discovered capacity.

- Slots 0–2 keep the V3 filenames (backwards compatible).
- Slots 3–8 write `_next_s{n}.wav`.
- Host names: legacy slot 1/2 names, then `Copilot Capture {slot}`.
- `CaptureCapacity` is discovered from live tap inventory / advertised protocol.
- Pool capacity counts only `HOST_AVAILABLE` parked hosts.
- Producer asks for sources; capture plans `ceil(n / capacity)` passes.
- Practical max = 8 source slots. Not unlimited. Not hardcoded 4.

## Rejected alternatives

| Option | Why not |
|---|---|
| A only (more V3 taps) | Still three global files. Slots collide. |
| C dynamic path only | Needs a unique Max destination per instance; more fragile than extra `sel` outlets. |
| E protocol 4 without hosts | Device slots unused without provisioned tracks. |
| Hardcode two Groove Rider tracks | Encodes 4 as a new ceiling. |

## Fallback

TapProtocol 3 or two ready hosts → width 2 → `2+2` for four sources. No agent sequencing.
