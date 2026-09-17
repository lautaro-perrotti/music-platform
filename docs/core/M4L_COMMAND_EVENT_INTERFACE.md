# M4L command / event interface (no UI)

STATUS: **DOCUMENTED / FROZEN** (`M4L_CONTROL_CONTRACT_V1`).

Source of truth: `copilot.audio.m4l_control_contract_v1`.

Native workflow for now:

Ableton Live + Max for Live + local Core CLI.

Do **not** build a web, Electron, or React frontend.

A future Max device may send these commands and display these events. Core already has CLI/API equivalents.

## Commands the device may send

| Command | Core equivalent | Writes music? |
| --- | --- | --- |
| `ANALYZE` | `python -m copilot.cli producer-analyze` | No |
| `STATUS` | `python -m copilot.cli doctor` then `onboard-project` | No |
| `DIAGNOSIS` | last `logs/producer_analyze_v1.json` diagnosis/gate | No |
| `PROPOSED ACTION` | gate + MusicPlan dry-run | No |
| `APPLY` | `producer-run --mode autonomous` (only when justified) | Yes, `SET_TRACK_VOLUME` only |
| `ROLLBACK` | existing transaction rollback | Restores this agent's write |

## Events Core should emit

| Event | Payload (no secrets) |
| --- | --- |
| `STATUS` | `READY` / `BLOCKED` + failures |
| `DIAGNOSIS` | preserved status + category + summary |
| `PROPOSED_ACTION` | `SET_TRACK_VOLUME` or `NONE` |
| `APPLY_RESULT` | `KEEP` / `ROLLBACK` / `ABSTAIN` / `IN_DOUBT` |
| `TERMINAL_STATE` | transport / taps idle / journals / transactions |

## Rules

- Commands are explicit. No hidden fallback.
- `APPLY` must refuse unless the read-only gate says the volume action is justified.
- Status strings are never collapsed.
- Device UI is deferred until a concrete acceptance test needs it.
