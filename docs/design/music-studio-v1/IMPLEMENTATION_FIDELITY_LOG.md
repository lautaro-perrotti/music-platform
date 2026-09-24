# Implementation fidelity log

Claude Design `.dc.html` is the only visual source of truth. The previous
runtime visual layer was removed instead of being overridden.

## Hard reset applied

- Removed `ui/app.css`, `ui/base.css`, `ui/tokens.css`, and the refinement override.
- `define()` no longer injects component-local Shadow DOM styles.
- Production shell, top bar, sidebar, player, and Projects use Light DOM with
  values copied from `source/TopBar.dc.html`, `source/Sidebar.dc.html`,
  `source/Player.dc.html`, and `source/Projects.dc.html`.
- Backend, API calls, persistence, routing, jobs, simulation, and musical-write
  boundary were not changed.

## Current pass

### App shell — SOURCE TRANSPLANT ACTIVE

- 48px top bar, 212px sidebar, 64px player.
- Claude SVGs and inline values are used directly.
- A/B empty state and waveform layering follow the Player source.

### Projects — SOURCE TRANSPLANT ACTIVE

- Source: `source/Projects.dc.html`.
- Project data is connected to the source table structure.
- Existing open-project and new-project actions remain wired.
- Browser validation: `Projects` at `http://127.0.0.1:8791/`.
- This is the hard stop: do not port another page until the Projects PNG
  comparison is available and this screen is accepted.

### Project Home - SOURCE TRANSPLANT ACTIVE

- Source: `source/ProjectHome.dc.html`.
- The home route now uses the source composition: breadcrumb, project header,
  action row, current-version waveform, brief, recent generations, running
  jobs, Ableton, references, and activity panels.
- Available API data is connected where present; missing source-only content is
  represented by explicit source-shaped mock data so the page is complete
  without inventing a new visual language.
- Browser validation: `http://127.0.0.1:8791/#home` renders with the shell,
  source layout, waveform, and working navigation/action links.
- Musical writes: `0`.

### Ableton direction vocabulary - SOURCE TRANSPLANT ACTIVE

- Synced the latest 42-artboard source update and its New Project variants.
- New Project is now intentionally identity-only: the five starting points
  (Empty, An idea, Audio, Ableton, Reference) change only the next screen.
  It does not collect musical controls, generate, analyze, or write to Live.
- Ableton language is explicit in the runtime: `Capture from Ableton` for
  Ableton → Music Studio and `Apply to Ableton` for Music Studio → Ableton.
- Project Home now exposes the Working Copy connection and both directional
  actions. Ableton exposes Capture from Ableton and Apply current version to
  Ableton. Versions expose Compare, Open in Studio, and Apply to Ableton.
- Stems now separates `Export stems` (download) from `Apply stems to Ableton`
  (working-copy action boundary).
- The source rule is recorded in `CODEX_PROMPT.md`; no Ableton runtime writes
  were introduced by this UI work.

### New project and header switchers - SOURCE TRANSPLANT ACTIVE

- Synced the new Claude sources: `NewProject`, `NewProjectAbleton`,
  `ProjectSwitcher`, and `VersionSwitcher`, plus the updated `Projects`,
  `TopBar`, `canvas.json`, and `CODEX_PROMPT.md`.
- `New project` is now a distinct route from `Create`: it supports An idea,
  Audio, Ableton set, and Empty modes, with source-shaped details and the
  corresponding CTA. The Ableton mode exposes read-only Core checks and the
  Working Copy boundary; it does not write to Live.
- The project switcher is available from the top-bar project name and keeps
  the active project visibly distinct from recent projects.
- The version switcher is available from the top-bar version chip and keeps
  Active distinct from In Ableton.
- Browser validation: `#new-project`, all four mode routes,
  `#project-switcher`, and `#version-switcher` rendered successfully.
- Musical writes: `0`.

### Create simple - SOURCE TRANSPLANT ACTIVE

- Source: `source/Create.dc.html`.
- The simple create route now uses the source composition: brief editor,
  starting point, duration, version count, vocals, quality, references,
  Lucas tip, recent prompts, and generation action.
- Generation remains connected to the existing API contract. Duration and
  candidate count are submitted through the existing `generate` action;
  recent prompts and the version stepper are interactive local controls.
- Missing reference content falls back to an explicit source-shaped mock; no
  new visual language or provider behavior was invented.
- Browser validation: `http://127.0.0.1:8791/#create`; visual layout renders,
  recent prompt selection works, and version count changes work.
- Musical writes: `0`.

### References library - SOURCE TRANSPLANT ACTIVE

- Source: `source/References.dc.html`.
- The references route now uses the source grid of six evidence cards with
  waveforms, type, duration, rights, analysis state, and intended use.
- Existing reference records are used when available; the remaining catalog
  rows are explicit source-shaped mocks so the library composition is complete
  while the backend grows.
- Search and type filters are functional. Add reference remains connected to
  the existing durable action boundary.
- Browser validation: `http://127.0.0.1:8791/#references`; MIDI filtering was
  exercised and reduced the grid to the matching card.
- Musical writes: `0`.

### Versions - SOURCE TRANSPLANT ACTIVE

- Source: `source/Versions.dc.html`.
- The versions route now uses the source lineage layout with eight historical
  rows, branch connectors, waveforms, status badges, compare actions, and the
  selected-version inspector.
- Durable versions are used when available; the remaining history is filled
  with explicit source-shaped mocks so the non-destructive timeline is
  visible before the backend has a full project history.
- All/Kept/In Ableton filters are functional. Compare and Apply to Ableton
  remain routed through existing safe review surfaces; no direct write was
  introduced.
- Browser validation: `http://127.0.0.1:8791/#versions` renders the full
  lineage and inspector.
- Musical writes: `0`.

### Remaining product screens - SOURCE TRANSPLANT ACTIVE

- Results, Compare, Chat, Jobs, Ableton, Mix / Master, Activity, Providers,
  Stems, Studio, Voice, and System Health now use Claude-source compositions
  with the existing route and action boundaries preserved.
- Each screen keeps its product-specific source hierarchy: blind candidate
  review, A/B comparison, Lucas plan/chat, background jobs, Ableton state,
  measured mix facts, musical activity, provider routing, stem review hold,
  timeline editing, voice direction, and environment health.
- Real workspace data is used where available; missing backend fields are
  filled with explicit source-shaped mocks, never with a new visual language.
- Browser route sweep: all 12 routes rendered with visible headings at
  `http://127.0.0.1:8791/`.
- Musical writes: `0`.

### Secondary source screens - SOURCE TRANSPLANT ACTIVE

- The remaining Claude source designs are now routed and rendered as explicit
  screens: AddReference, ApplyVersion, ChatResult, ChatRunning,
  CommandPalette, CreateAdvanced, JobDetail, Mobile, ReferenceDetail, States,
  StemCompare, StudioRegion, VersionCompare, and VoiceResults.
- Complete source inventory: 35 `.dc.html` designs.
- Product routes transplanted: 17.
- Shared shell source compositions transplanted: Main, TopBar, Sidebar, and
  Player (4).
- Secondary routes transplanted: 14.
- Browser validation: all 14 secondary routes rendered with a real heading or
  source-defined dialog/mobile composition and no route-load error.
- Musical writes: `0`.

## Queue

1. Compare each transplanted screen against its corresponding Claude PNG.
2. Replace explicit source-shaped mocks with durable backend data as each
   product contract becomes available.

## Validation record

- Focused UI tests: `25 passed`.
- UI JavaScript syntax: passed.
- Python compileall: passed.
- `git diff --check`: passed.
- Browser flow: Projects renders, project data appears, navigation remains active.
- Musical writes: `0`.
