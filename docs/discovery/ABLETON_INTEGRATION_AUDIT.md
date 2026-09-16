# Ableton Integration Audit

**Date:** 2026-09-13  
**Environment:** Windows 10, Python 3.12.10, uv 0.12.7, no Ableton Live executable found  
**Ableton Live verification status:** `BLOCKED_BY_ENVIRONMENT`

Clones inspected at `D:\music-platform-audit\`.

---

## Verdict

| Role | Candidate | Decision |
| --- | --- | --- |
| PRIMARY transport | `jpoindexter/ableton-mcp` Remote Script protocol (TCP JSON, port 9877) | `WRAP` |
| FALLBACK transport | `ideoforms/AbletonOSC` (OSC UDP 11000/11001) | `WRAP` later |
| REJECT as product agent | Upstream MCP servers as the agent surface | `REJECT` |
| REJECT as primary | `ahujasid/ableton-mcp` | `REJECT` (telemetry, thinner LOM) |
| BUILD | `DawAdapter`, `SessionState`, stable IDs, typed tools, `TransactionManager` | `BUILD` |

Do not expose raw LOM, `eval`, or arbitrary Python to the agent.

---

## 1. jpoindexter/ableton-mcp

**Repository:** https://github.com/jpoindexter/ableton-mcp  
**Cloned:** `D:\music-platform-audit\ableton-mcp` (shallow, 2026-09-13)  
**License:** MIT (`LICENSE` present)  
**Created:** 2026-01-27  
**Claimed:** "200+ tools"  
**Status:** `PARTIALLY_VERIFIED` (code + unit tests exist; Live execution blocked)

### Architecture

```
LLM / MCP client
    -> MCP_Server/server.py  (FastMCP, 128 @mcp.tool functions)
    -> TCP JSON 127.0.0.1:9877
    -> AbletonMCP_Remote_Script/__init__.py  (257 command types)
    -> Ableton Live Object Model

Also:
    REST API  -> same Remote Script
    M4L device (Node for Max) -> LLM providers directly
```

Remote Script binds `HOST` default `localhost`, port `9877`. No `eval` / `exec` / `subprocess` found in the Remote Script or MCP server.

### Versions

| Item | Evidence |
| --- | --- |
| Ableton | README: Live 10+. Changelog 2.0.0 dated 2026-01-27. Live 12 not separately certified here. |
| Python | README: 3.8+. Our stack is 3.12. Compatible at language level. |
| Remote Scripts | Yes. Required. |
| Max for Live | Optional third mode. Not required for our adapter. |
| MCP | Yes, FastMCP. |
| REST | Yes, FastAPI. |
| WebSocket | Not present. |
| OSC | Not present. |

### Real tool counts (executed, not README)

Counted from source with `D:\music-platform-audit\count_tools.py`:

- **MCP tools (`@mcp.tool` in `MCP_Server/server.py`): 128**
- **Remote Script command types: 257**
- **TOOLS.md still says "80+". The README "200+" is marketing. The Remote Script is the larger surface; MCP does not expose all 257 commands.**

MCP tools found:

```
health_check, get_playback_position, get_session_info, get_track_info,
get_track_color, get_clip_color, get_scene_color, get_clip_loop, get_send_level,
create_midi_track, create_audio_track, set_track_name, set_track_mute,
set_track_solo, set_track_arm, set_track_volume, set_track_pan, create_clip,
delete_clip, get_clip_notes, add_notes_to_clip, set_clip_name, set_tempo,
load_instrument_or_effect, fire_clip, stop_clip, start_playback, stop_playback,
get_device_parameters, set_device_parameter, get_all_scenes, create_scene,
delete_scene, fire_scene, stop_scene, set_scene_name, set_scene_color,
duplicate_scene, delete_track, duplicate_track, freeze_track, flatten_track,
unarm_all, move_device_left, move_device_right, set_track_color, toggle_device,
delete_device, duplicate_clip, set_clip_color, set_clip_loop, remove_notes,
remove_all_notes, transpose_notes, undo, redo, get_return_tracks,
get_return_track_info, set_send_level, set_return_volume, set_return_pan,
get_current_view, focus_view, select_track, select_scene, select_clip,
start_recording, stop_recording, toggle_session_record,
toggle_arrangement_record, set_overdub, capture_midi, get_arrangement_length,
set_arrangement_loop, jump_to_time, get_locators, create_locator,
delete_locator, get_track_input_routing, get_track_output_routing,
get_available_inputs, get_available_outputs, set_track_input_routing,
set_track_output_routing, get_cpu_load, get_session_path, is_session_modified,
get_metronome_state, set_metronome, get_scale_notes, quantize_clip_notes,
humanize_clip_timing, humanize_clip_velocity, generate_drum_pattern,
generate_bassline, get_browser_tree, get_browser_items_at_path, load_drum_kit,
get_master_info, set_master_volume, set_master_pan, browse_path, search_browser,
load_item_to_track, load_item_to_return, get_clip_gain, get_clip_pitch,
set_clip_gain, set_clip_pitch, set_clip_warp_mode, get_clip_warp_info,
get_warp_markers, add_warp_marker, delete_warp_marker, get_clip_automation,
set_clip_automation, clear_clip_automation, create_group_track, fold_track,
unfold_track, set_track_monitoring, get_track_monitoring, get_device_by_name,
get_rack_chains, select_rack_chain, get_groove_pool, apply_groove, commit_groove
```

### Capability matrix (code-inspected)

| Area | Present | Notes |
| --- | --- | --- |
| Session / transport | Yes | `get_session_info` omits `is_playing` and the track list. Need `get_playback_position` + per-track `get_track_info`. |
| Tracks create/modify | Yes | `create_midi_track` **ignores `name`**. Wrapper must call `set_track_name`. |
| MIDI notes | Yes | `add_notes_to_clip` calls `clip.set_notes()` — **replace, not append**. |
| Clips | Yes | Length in beats. Slot must be empty. |
| Devices / params | Yes | Index-based. Nested racks only partly covered (`get_rack_chains`). |
| Mixer | Yes | Volume 0–1, pan -1–1. |
| Automation | Partial | Clip envelopes only. |
| Scenes | Yes | |
| Observers | No | Pull-only. No Live observer subscriptions. |
| Object IDs | No | Track/clip **indices only**. Unstable after reorder/delete. |
| Undo | Live global | `_song.undo()`. Not agent-scoped. Cannot isolate "undo my last change". |
| Audio capture | **No** | No bounce/render/tap command. |
| Error handling | Partial | Command whitelist. Exceptions become `{status: error}`. |
| Security | Mixed | Localhost default. No TCP auth. REST can use `REST_API_KEY` + rate limit. Unit tests exist in `tests/unit/test_security.py`. |
| Stability | Unknown in Live | `BLOCKED_BY_ENVIRONMENT` |

### Gaps we must wrap / fix

1. `create_midi_track` name is dropped by the Remote Script.  
2. `add_notes_to_clip` replaces the whole clip.  
3. No stable identity.  
4. No agent-scoped undo.  
5. No audio region capture.  
6. MCP surface is smaller than the Remote Script.  
7. "AI helpers" (`generate_drum_pattern`, `generate_bassline`) are hardcoded style recipes — do not use as composition engine.

### Tests observed

- Unit tests: security, validation, error handling, REST. They mock Ableton.
- **Executed in this environment (2026-09-13):** `tests/unit/test_validation.py` + `tests/unit/test_error_handling.py` → **110 passed** (FastAPI TestClient, Ableton mocked).
- Live integration: not runnable here.

---

## 2. ideoforms/AbletonOSC

**Repository:** https://github.com/ideoforms/AbletonOSC  
**Cloned:** `D:\music-platform-audit\AbletonOSC`  
**License:** MIT  
**Last upstream push listed:** 2025-11-19  
**Status:** `PARTIALLY_VERIFIED` (code + in-repo Live tests; Live execution blocked)

### Architecture

Remote Script OSC server. Listen `11000`, reply `11001`, default `127.0.0.1`.

Handlers generated from Live methods/properties plus custom addresses. A naive string scan found **75 hardcoded `/live/...` addresses**; many more are registered dynamically (`/live/song/<method>`, property get/set, `start_listen`).

### Comparison vs jpoindexter protocol

| | jpoindexter Remote Script | AbletonOSC |
| --- | --- | --- |
| Protocol | TCP JSON | OSC UDP |
| Functional coverage | Broader explicit clip/note/device helpers | Broad song/track/device/clip + **observers** |
| Simplicity | One JSON command map | Many OSC addresses, tuples |
| Maintenance | New 2026 fork of ahujasid; 48★ | Older, 728★, slower merge, open PRs in 2026 |
| Stability | Unknown here | Used in production-ish setups; Live 12 used by author |
| Observe changes | No | `start_listen` / `stop_listen` |
| Latency | TCP + main-thread queue | UDP; typically lower setup cost |
| Extensibility | Add command handler | Add OSC handler |
| Undo | Live undo | Live undo |
| Identity | Indices | Indices |
| Audio tap | No | No |
| Fits our adapter | Yes — already wrapped | Yes — fallback backend |

AbletonOSC is the better **observer** and the more battle-tested **control surface**. jpoindexter is the better **immediate wrap** for the first vertical slice because it already has `create_clip` + note payload in one JSON protocol we can speak from Python without OSC deps.

---

## 3. Other bridges (only the relevant ones)

| Project | Why considered | Decision |
| --- | --- | --- |
| `ahujasid/ableton-mcp` | Original MCP; 2912★; MIT | `REJECT` as primary: anonymous telemetry, smaller tool set, we already wrap the thicker fork's protocol. |
| `alaarab/livemcp` | 220 tools, docs search, resources | `REFERENCE_ONLY`. Overlaps our build. Not cloned this pass. |
| `Ziforge/ableton-liveapi-tools` | 220 LiveAPI tools over TCP | `REFERENCE_ONLY`. Same class as Remote Script bridges. |
| `sunflower-of-parchman/codex-live-bridge` | M4L OSC + generic LiveAPI RPC + observers + write token | `NOT_RECOMMENDED` as core: generic `/api/call` is arbitrary LOM execution. Useful ideas for observers + capability token. |
| Official Live Remote Script API / M4L LiveAPI | Platform primitive | `REUSE_AS_IS` as the only way into Live. We do not reinvent LOM. |

---

## 4. Security findings

| Risk | Where | Our rule |
| --- | --- | --- |
| Unauthenticated TCP on localhost | jpoindexter Remote Script | Keep localhost. Do not bind `0.0.0.0` in production. |
| Arbitrary LOM RPC | codex-live-bridge `/api/call` | Do not adopt. |
| Telemetry | ahujasid | Do not ship. |
| Live `undo()` is global | both | Build our own transaction log. |
| M4L Node talking to cloud LLMs | jpoindexter M4L | Out of scope; do not embed API keys in Live. |

---

## 5. Audio thread constraint

Neither bridge runs in the audio callback. Both use Live's main thread / Remote Script tick. That is compatible with:

```
ABLETON AUDIO THREAD -> (future tap) -> worker -> MusicObservation -> Agent -> command queue -> Ableton
```

Never put an LLM in the audio callback. Existing bridges already obey this by accident.

---

## 6. What we built after the audit

`src/copilot/daw/ableton_tcp.py` wraps the jpoindexter JSON protocol.  
`src/copilot/daw/mock.py` + `mock_tcp_server.py` verify the protocol without Live.  
Real Live connection remains `BLOCKED_BY_ENVIRONMENT` until Ableton + Remote Script are installed.
