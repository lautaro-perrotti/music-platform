# SUPPORTED_ENVELOPE_V1

100% means every known gate for this envelope passes.

It does **not** mean every Ableton or music-production feature exists.

## Supported now

| Area | What is supported |
| --- | --- |
| Ableton | Live 12.x with Remote Script TCP JSON (protocol 1) and Copilot Audio Tap (TapProtocol 3). **`LIVE_SESSION_READINESS_V1` = VERIFIED / FROZEN.** |
| Project assumptions | Any .als that Core can snapshot. The original source set is refused. A manifest-backed development working copy may use the frozen lab preflight. |
| Bootstrap | `python -m copilot.cli project-bootstrap` installs Main tap + `Copilot Capture` / `Copilot Capture Bass`, unique slots 0/1/2, OFF_MIX_GRAPH routing to generic eligible sources. Second complete run returns `NO_CHANGES_REQUIRED`. Lookalike user tracks are never taken over. |
| Preflight | `project-ready` orchestrates identity → bootstrap → validate → preflight. Development working copy reuses `session-diagnose`. Other projects use `GENERIC_PREFLIGHT_V1`. |
| Discovery | PersistentObjectRef + arrangement clip overlap. Track index is a locator, never identity. |
| Capture | Main `MAIN_FINAL`. Isolated Post Mixer via OFF_MIX_GRAPH hosts. Alignment remains **LIMITED ±52 ms**. Silence is valid evidence. |
| Source isolation | Frozen `ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1` stays development-lab-specific. Generic inventory is `GENERIC_SOURCE_ISOLATION_V1` (no development-song names). |
| Source capture pool | Bounded sequential pool wrapping existing `capture_source_post_mixer`. Core API: `capture_source_post_mixer_ref(target_ref, region)`. |
| DSP | Frozen `lowend-obs-1` and `fullmix-obs-1` only when the caller supplies the required views. No hidden analyzer calls. |
| Astra | One structured call via `reason()`. Statuses stay distinct. |
| MusicPlan | `SET_TRACK_VOLUME` only. Freshness gate. Canonical execute/rollback on the development set. |
| Rollback / State Trust | Unchanged frozen layers. IN_DOUBT is not success. |
| Read-only producer | `python -m copilot.cli analyze-project "<folder>"` (one high-level call). `producer-analyze` remains for debug. |
| Autonomous | `python -m copilot.cli producer-run --mode autonomous` reuses analyze, then the frozen FAMI runner **only** on the development working copy. External-project writes stay `ACTION_NOT_AVAILABLE` until a second real song is validated. |
| Health | `python -m copilot.cli doctor`. Live is ready only after `SESSION_READY` (`LIVE_SESSION_READINESS_V1` **FROZEN**). A listening port is not ready. |
| Onboard | `python -m copilot.cli onboard-project` (alias `project-ready`) |
| Regression | `python -m copilot.cli regression-v1` (no live destructive writes) |
| Capabilities | `python -m copilot.cli capabilities` |
| Cross-project holdout | `python -m copilot.cli cross-project-validate` — **WAITING_FOR_EXTERNAL_SONG**. First pass read-only. |

## Unsupported / deferred

- EQ actions
- compressor actions
- MIDI editing
- arrangement editing
- advanced references / Music Flamingo
- automatic mastering
- unbounded plugin control
- web / Electron / React UI
- silent mock success
- collapsing `WEAKLY_SUPPORTED` into `INSUFFICIENT_EVIDENCE` on the producer path

## Status vocabulary (never collapse)

`SUPPORTED` · `WEAKLY_SUPPORTED` · `INSUFFICIENT_EVIDENCE` · `NO_ACTION_REQUIRED` · `DIAGNOSIS_UNSTABLE` · `ACTION_NOT_AVAILABLE`

Safe abstention is a successful outcome.
