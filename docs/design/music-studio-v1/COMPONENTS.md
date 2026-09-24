# Music Studio UI kit

The production translation of the Claude Design source lives in
`src/copilot/studio/static/ui/`. It is native Custom Elements + Shadow DOM,
served without Node, npm or a build step.

## Layers

- `tokens.css` and `base.css`: surfaces, text, cue/state/role colors, spacing, radii, type and focus rules.
- `lib/`: element registration, event helpers, SVG icon paths and deterministic waveform rendering. `fromPeaks()` accepts real peak arrays.
- `atoms/`: buttons, statuses, rights, roles, inputs, controls, avatars and waveform.
- `molecules/`: transport, A/B switch, stage list, candidate/job/fact rows, reference and state primitives.
- `organisms/`: shell navigation, candidate/plan/critique/analysis cards, compare, timeline, jobs, chat, inspector and SafeWrite review.
- `templates/`: app shell, inspector layout, centered column and overlays.
- `pages/`: the 28 numbered screens from `canvas.json`, composed from templates and organisms.

## Product boundaries

Lucas intent and Core measurements use separate cards. Provider/model, hashes,
LUFS, journal and state-token details are progressive-disclosure content.
In-doubt only exposes Inspect and Reconcile. No component fabricates progress
or a provider result. The browser app calls the existing vertical-slice API;
Ableton remains read-only in this surface and all writes remain Core-owned.

## Verification

```powershell
python -m copilot.studio.server --host 127.0.0.1 --port 8765
# open http://127.0.0.1:8765/ui/catalog.html
python -m pytest -q tests/test_music_studio_ui_assets.py tests/test_music_studio_vertical_slice.py tests/test_music_generation_v1.py
```

The catalog is a lightweight Storybook-style surface with atom, molecule and
organism examples plus links to the page compositions. The current entrypoint
is `/`; `/ui/catalog.html` is the inspection surface and `/ui/page.html?page=`
loads individual page compositions.
