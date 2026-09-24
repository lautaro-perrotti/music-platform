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

## Queue

1. Finish Projects comparison and acceptance.
2. Create.
3. Results.
4. Remaining source pages.

## Validation record

- Focused UI tests: `25 passed`.
- UI JavaScript syntax: passed.
- Python compileall: passed.
- `git diff --check`: passed.
- Browser flow: Projects renders, project data appears, navigation remains active.
- Musical writes: `0`.
