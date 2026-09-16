# Live integration status

**Date:** 2026-09-13  
**Verification classes:** `MOCK_VERIFIED` ≠ `LIVE_VERIFIED`

LIVE-1 MIDI gate is `VERIFIED` against Ableton Live 12.4.5 Trial. Audio capture is `NOT_STARTED`.

`revision` is an observed `state_hash` version. It increases when the normalized SessionState changes, regardless of who mutated it.

---

## Detection (executed)

Searched, without unbounded filesystem walks:

- Uninstall registry (HKLM 64/32, HKCU)
- `SOFTWARE\Ableton` HKLM/HKCU
- `C:\Program Files\Ableton`, `C:\Program Files (x86)\Ableton`, `C:\ProgramData\Ableton`
- `%LOCALAPPDATA%\Programs\Ableton`
- `D:\Ableton`, `D:\Program Files\Ableton`
- `%APPDATA%\Ableton` prefs / User Remote Scripts
- Start Menu `.lnk` whose name contains Ableton (ignored Windows LiveCaptions)
- Running `Ableton Live*` processes
- TCP `127.0.0.1:9877`

**Result:** Live 12.4.5 Trial found; Control Surface AbletonMCP listening on `127.0.0.1:9877`.

Evidence: `logs/ableton_detect.json`, `logs/live_probe.json`, `logs/live_slice1.json`, `logs/live_undo.json`, `logs/live_identity.json`.

---

## Report

| Capability | Status | Evidence |
| --- | --- | --- |
| LIVE CONNECTION | `VERIFIED` | TCP `127.0.0.1:9877`; `health_check` ok; backend `ableton-tcp`. |
| LIVE READ | `VERIFIED` | Probe from Live: tempo 120, 4/4, 4 default tracks (1-MIDI, 2-MIDI, 3-Audio, 4-Audio). |
| LIVE MIDI WRITE | `VERIFIED` | `slice1` created `AI Test` + 4-bar clip + 16 notes pitch 60. `txn_ca6852779022`. |
| LIVE READ-BACK | `VERIFIED` | Post-write snapshot: track/clip exist, `note_count==16`, all pitches 60, postcondition SATISFIED. |
| LIVE TRANSACTION | `VERIFIED` | Transaction `VERIFIED` only after read-back. Journal + `logs/live_slice1.json`. |
| LIVE ROLLBACK | `VERIFIED` | Agent inverses (not `song.undo()`). After undo, `AI Test` absent; default 4 tracks untouched. |
| AUDIO CAPTURE | `NOT_STARTED` | MIDI Live gates are `VERIFIED`. Audio spike not started. |

---

## MOCK_VERIFIED only (does not satisfy Live acceptance)

- Vertical slice MIDI + rollback against in-process mock
- TCP protocol against `MockRemoteScriptServer`
- Identity rematch: insert-before and rename
- Failures: missing port, missing track/clip, stale revision, volume out of range, partial apply → `ROLLED_BACK`/`FAILED` never `VERIFIED`
- Detection correctly reports Ableton absent
- CLI probe/slice1 never use mock

---

## Canonical Live path (single)

```
copilot.cli detect
copilot.cli install-script
copilot.cli probe          # AbletonTcpAdapter.probe — never mock
copilot.cli slice1         # connect_live() only
copilot.cli undo           # inverses from logs/agent_transactions.json
```

`AbletonTcpAdapter` ↔ `localhost:9877` TCP JSON ↔ vendored `jpoindexter` Remote Script ↔ LOM.

MCP server is **not** in the runtime.

Vendored script: `vendor/abletonmcp_remote_script/AbletonMCP/__init__.py`  
Host `localhost` / port `9877` / protocol `tcp-json`.

`install-script` will not create fake Ableton prefs. It copies the script only after a real Live prefs tree exists.

---

## Identity guarantees (honest)

Ableton LOM does not give us durable object UUIDs over this protocol. Index is a **locator**.

Our `IdentityRegistry` is session-scoped:

| Event | Guarantee |
| --- | --- |
| Insert another track before | Same `stable_id` if the name+role pair stays unique |
| Index change | ID kept; `index` updates |
| Rename | ID kept if device/clip fingerprint is unique |
| Duplicate names | Name match is skipped; may allocate a new ID |
| Restart of our process | IDs reload only from our sidecar `logs/agent_transactions.json`. That is not a Live UUID. |
| Live save/reload without our sidecar | IDs reset |

We do not invent persistence Ableton does not offer.

Rollback path:

```
stable_id → current SessionState → fingerprint → current locator → inverse
```

`target_name_at_apply` is debug/fingerprint only. If the target cannot be resolved unambiguously: `ROLLBACK_CONFLICT` and no writes.

---

## BLOCKED_BY_ENVIRONMENT

```
Missing:
Ableton Live installation
```

### Everything verified before the boundary

- Detector (registry + official paths + process + port)
- Remote Script vendored + installer that refuses to invent prefs
- Live-only CLI (`probe`, `slice1`, `undo`) with no mock fallback
- Probe payload includes session/tempo/tracks/clips/devices when connected
- Transaction persist for cross-process Live undo
- Rollback by stable_id + fingerprint + current locator; abort of partial apply; `ROLLBACK_CONFLICT` when ambiguous
- Identity rematch rules + tests
- Failure tests (port, targets, stale revision, range, partial)

### Exact first commands after installation

```
python -m copilot.cli detect
python -m copilot.cli install-script
```

Then in Live: Settings → Link, Tempo & MIDI → Control Surface **AbletonMCP**, Input/Output **None**. Restart Live if the script was copied while Live was open.

```
python -m copilot.cli probe
python -m copilot.cli slice1
python -m copilot.cli undo
```

Do not treat `mock-slice1` as Live evidence.
