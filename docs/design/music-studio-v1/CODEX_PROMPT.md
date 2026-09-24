# Prompt para Codex: Music Studio UI kit (Atomic Design)

Copiá todo lo que está debajo de la línea y pegalo en Codex, con el repo `music-platform` abierto.

---

## Contexto

Estás en el repo `music-platform`. Leé primero `AGENTS.md`: es la ley del proyecto. Lo que importa acá:

- Localhost only.
- Sin secretos en git.
- Analyze es read-only.
- Nada de mocks que finjan éxito.

El diseño completo de la app **Music Studio** está en:

```
docs/design/music-studio-v1/source/
```

Son 42 archivos `.dc.html` más `canvas.json`. Cada `.dc.html` es un artboard de un editor de diseño. **No es código de producción.** Es la especificación visual que vas a traducir.

La UI existente se sirve desde `src/copilot/studio/static/index.html`. La sirve `src/copilot/studio/server.py`, que es un servidor stdlib de Python sin paso de build. **No introduzcas Node, npm, bundlers ni frameworks.**

## Objetivo

Construir una librería de componentes reutilizables siguiendo **Atomic Design**:

1. Atoms
2. Molecules
3. Organisms
4. Templates
5. Pages

Usá **Web Components nativos** (Custom Elements v1 + Shadow DOM), ES modules sin build y CSS custom properties para los tokens.

Después armá un catálogo navegable y reconstruí las 28 pantallas usando solo esos componentes.

## Cómo leer los archivos fuente

- Todo el estilo es inline (`style="..."`). Extraé los valores repetidos como tokens. No copies estilos inline a los componentes.
- `{{algo}}` es un hueco de template que se llena desde `renderVals()` en el `<script type="text/x-dc">` al final de cada archivo. Los datos de ejemplo (versiones, jobs, candidatos) están ahí.
- `<sc-for list="{{items}}" as="x">` es un loop.
- `<sc-if value="{{cond}}">` es un condicional.
- `<dc-import name="TopBar" ...>` monta `TopBar.dc.html` con esos atributos como props. Esos tres (`TopBar`, `Sidebar`, `Player`) ya son componentes: convertilos primero.
- Las formas de onda salen de una función `wf(seed, {n, env, jag})`. Genera un path SVG espejado en un viewBox de `0 0 1000 100`, con `preserveAspectRatio="none"`. Los envelopes están en `SONG` y `ENV.{drums,bass,keys,other}`. Portá esto a `ui/lib/waveform.js`. En producción la misma API tiene que aceptar picos reales (`Float32Array`) en vez de seed.
- `Main.dc.html` es el sistema de diseño: principios, colores, tipografía, estados, anatomía de waveform y disclosure. `States.dc.html` tiene los estados empty, loading, failure, disconnected, review y los toasts. Leelos antes que nada.
- `canvas.json` tiene el orden y los títulos numerados (01–28) de cada pantalla.

## Estructura a crear

```
src/copilot/studio/static/ui/
  tokens.css            # :root custom properties: surfaces, text, accent, states, roles, radius, spacing, type
  base.css              # reset, fuentes Geist / Geist Mono, focus-visible
  lib/
    define.js           # helper para registrar elementos, reflejar atributos y emitir eventos
    waveform.js         # wf() portado + fromPeaks(peaks)
    icons.js            # paths SVG 24px, stroke 1.6, por nombre
  atoms/
  molecules/
  organisms/
  templates/
  pages/                # una página por pantalla, solo composición
  catalog.html          # catálogo navegable de todo lo anterior
```

Cada componente es un archivo, `ms-<nombre>.js`, que exporta la clase y hace `customElements.define`. El prefijo de tag es `ms-`.

## Inventario mínimo

Si en los archivos fuente encontrás más repetición, agregá componentes.

**Atoms**

- `ms-button`: variantes primary (ivory), secondary, ghost, danger-outline. Estados disabled y loading. Tamaños sm y md.
- `ms-icon` (name)
- `ms-kbd`
- `ms-status`: queued, provisioning, running, validating, review, succeeded, failed, blocked, cancelled, in-doubt. Siempre glifo + palabra + color.
- `ms-rights`: owned, licensed, reference-only, unknown.
- `ms-role-dot`: mix, drums, bass, harmonic, vocals, other.
- `ms-tag`
- `ms-monogram`: lucas (círculo ivory) o core (cuadrado con regla).
- `ms-badge`: measured o judgment.
- `ms-switch`
- `ms-slider`: unipolar y bipolar.
- `ms-segmented`
- `ms-checkbox-tile`
- `ms-input`
- `ms-textarea`
- `ms-avatar`
- `ms-waveform`: seed | peaks, color, playhead, selection, loop, markers, sections, played-progress.

**Molecules**

- `ms-nav-item`
- `ms-search-field`
- `ms-transport` (prev, play, next, loop)
- `ms-ab-switch` (con "Synced")
- `ms-section-ruler`
- `ms-stage-list` (stages reales, sin %)
- `ms-candidate-row`
- `ms-job-row`
- `ms-fact-row`
- `ms-plan-row` (KEEP, CHANGE, GENERATE)
- `ms-reference-chip`
- `ms-dimension-toggle`
- `ms-toast`
- `ms-empty-state`
- `ms-error-card` (typed, con Advanced colapsable)
- `ms-connection-banner`
- `ms-preference-picker` (Prefer A / Too close / Prefer B)
- `ms-lane-header` (nombre, S, M)

**Organisms**

- `ms-top-bar`: compone `ms-project-switcher` y `ms-version-switcher`, que se abren desde el nombre del proyecto y el chip de versión. Si la versión Active difiere de la que está In Ableton, lo avisa.
- `ms-sidebar` (prop active, modo avanzado)
- `ms-player` (global, persistente)
- `ms-candidate-card` (ready, generating, queued, failed)
- `ms-plan-card`
- `ms-running-card`
- `ms-critique-card` (superficie cálida, siempre "Musical judgment")
- `ms-analysis-card` (superficie fría, siempre "Measured")
- `ms-compare-panel` (dos lados o apilado, mismo playhead)
- `ms-timeline` (ruler, sections, lanes, selección, markers)
- `ms-region-toolbar` (con menú Add layer)
- `ms-version-lineage`
- `ms-jobs-panel`
- `ms-job-drawer`
- `ms-command-palette`
- `ms-dialog`
- `ms-in-doubt-panel` (solo Inspect y Reconcile, nunca Retry)
- `ms-apply-review` (Will, Will not, lista técnica en Advanced)
- `ms-chat-composer`
- `ms-chat-message` (user, lucas, core-result, error)
- `ms-inspector`
- `ms-provider-card`
- `ms-health-row`
- `ms-project-switcher`: dropdown del top bar (`ProjectSwitcher.dc.html`). Tiene búsqueda, el proyecto activo fijado arriba con check y badge Active, recientes con su estado (in doubt, review, failed), "Make active" con ↵, y links a All projects y New project. Atajo P. Evento `ms-project-activate`.
- `ms-version-switcher`: dropdown del top bar (`VersionSwitcher.dc.html`). Muestra dos conceptos distintos: **Active** (la versión sobre la que trabajás) e **In Ableton** (la última aplicada y verificada). Filas con preview, Compare y Activate, más los links All versions y Compare active with…. Atajo V. Evento `ms-version-activate`.
- `ms-new-project`: alta liviana (`NewProject.dc.html` más 4 variantes de estado). **Un Project es un workspace, no una especificación de canción.** Incluye:
  - Nombre.
  - Descripción opcional.
  - "Start from", un radiogroup de 5 opciones: Empty (default), An idea, Audio, Ableton, Reference. Solo define la pantalla siguiente y no es un tipo de proyecto.
  - Un panel "What will happen" con tres ítems: el proyecto creado, **Next** (lo único que cambia según la opción) y "Ableton: Nothing is changed".
  - Un CTA fijo, "Create project".

  Destinos:

  | Opción | Pantalla siguiente |
  |---|---|
  | Empty | Project Home |
  | An idea | Create |
  | Audio | import de audio |
  | Ableton | Connect Ableton |
  | Reference | Add reference |

  Prohibido en este componente: BPM, key, compás, duración, vocals, prompt, dirección para Lucas, provider, generar, analizar o escribir en Ableton. Todo eso pertenece a Create / GenerationBrief.

**Templates**

- `ms-app-shell`: top bar 48, sidebar 212, main, player 64. Slots main e inspector.
- `ms-shell-inspector`: main + aside de 300–380 colapsable.
- `ms-centered-column`
- `ms-overlay` (dialog o drawer sobre shell atenuado)

**Pages**

Hay 28 pantallas en `pages/`, más New project (`NewProject` y sus variantes `NewProjectIdea`, `NewProjectAudio`, `NewProjectAbleton` y `NewProjectReference`, que solo cambian la opción seleccionada). En la lista de Projects, el proyecto activo lleva el badge "Active" y los demás tienen la acción "Make active". Cada una solo compone templates y organisms, con los datos de ejemplo sacados de `renderVals()`. El flujo clickeable de `canvas.json` (nota "flow") tiene que funcionar con links entre páginas.

## Reglas de implementación

1. **Tokens primero.** Extraé todos los colores, radios, espaciados y tamaños de fuente de `Main.dc.html` y de los usos reales a `tokens.css`. Ningún componente usa un hex literal, solo `var(--ms-*)`. Incluí `--ms-role-*` y `--ms-state-*`.
2. **API por atributos y propiedades.** Los atributos son para valores simples. Las propiedades JS son para arrays y objetos (listas de candidatos, jobs). Los eventos son `CustomEvent` con prefijo `ms-` (`ms-play`, `ms-keep`, `ms-select`, `ms-change`). Usá slots para el contenido.
3. **Composición estricta.** Un átomo no importa nada. Una molécula solo importa átomos. Un organismo importa moléculas y átomos. Los templates no tienen datos. Las páginas no tienen estilos propios salvo layout.
4. **Disclosure progresivo.** Provider, model, seed, job id, hash, LUFS, state token y journal solo se renderizan dentro de `[advanced]`, o en un `<details>` "Advanced ▸". Nunca en la vista por defecto.
5. **Lucas y Core nunca comparten card.** Critique y analysis son organismos distintos, con superficies y badges distintos.
6. **Accesibilidad.**
   - Botones reales.
   - `aria-label` en los botones que solo tienen ícono.
   - `role="switch"`, `slider`, `radiogroup` y `tablist` donde corresponda.
   - `:focus-visible` con outline cue de 2px.
   - Contraste de texto ≥ 4.5:1.
   - Los estados nunca se comunican solo por color.
7. **Teclado.**
   - Space: play/pause.
   - A / B: switch del compare.
   - Ctrl/Cmd+K: palette.
   - G: generate.
   - /: foco al chat o a la búsqueda.
   - L: loop.
   - Esc: cierra drawer o dialog.
   - No se disparan si el foco está en un input.
8. **Sin progreso falso.** Si no hay progreso conocido, `ms-stage-list` muestra el stage actual. No hay barras de % inventadas.
9. **Ableton es bidireccional.** El vocabulario es fijo:
   - "Capture from Ableton" para Ableton → Music Studio: importar, adjuntar, analizar.
   - "Apply to Ableton" o "Apply current version to Ableton" para Music Studio → Ableton: audio, stems, clips, arreglos, cambios aprobados, con readback y rollback.

   Nunca uses "Export to Ableton". "Export" queda reservado para descargar archivos.

   El card de Ableton en Project Home muestra "Connected to <Working Copy>" y las dos acciones. Cada versión muestra Compare, Open in Studio y Apply to Ableton. En New Project, la opción Ableton significa "Start from a Live project and keep it connected".
10. **No destructivo.** Ningún componente ofrece "Overwrite" ni "Retry" en in-doubt.
11. **Responsive.**
    - A 1280px el sidebar colapsa a 56 y el inspector pasa a overlay.
    - Por debajo de 768 solo existen las vistas de review (ver `Mobile.dc.html`).
12. **Fidelidad.** Cada organismo y cada página tiene que coincidir visualmente con su `.dc.html`: mismos tamaños, gaps, colores y copy. Si algo del fuente es inconsistente, unificalo hacia el token y anotalo.

## Catálogo (`ui/catalog.html`)

Una página tipo Storybook sin dependencias:

- Sidebar con las secciones Tokens, Atoms, Molecules, Organisms, Templates y Pages.
- Cada componente se muestra con todas sus variantes y estados (por ejemplo, las 10 variantes de `ms-status` y los 4 estados de `ms-candidate-card`).
- Debajo de cada componente, la tabla de API: atributos, propiedades, eventos y slots.
- Toggle de "Advanced mode" global para ver el disclosure.
- Links a las 28 páginas.

Agregá una ruta en `server.py` para servir `/ui/*` desde `static/ui/` (probablemente ya lo hace el static root; verificalo).

## Verificación

- `python -m pytest` tiene que seguir pasando en el gate no-ambiental. No toques el runtime de Ableton.
- Levantá `python -m copilot.studio.server` (o como se lance hoy) y abrí `/ui/catalog.html`. Recorré el flujo: Projects → Create → Results → Compare → Keep B → ChatResult → StudioRegion → Versions → VersionCompare → ApplyVersion → Ableton.
- Sin errores en consola. Sin hex literales fuera de `tokens.css`: verificalo con un grep.
- Entregá un `docs/design/music-studio-v1/COMPONENTS.md` con el inventario final, qué pantalla usa qué componente, y las decisiones o desvíos respecto del diseño.

## Orden de trabajo

1. `tokens.css`, `base.css`, `icons.js`, `waveform.js`
2. Atoms, más su sección en el catálogo
3. Molecules
4. `ms-top-bar`, `ms-sidebar`, `ms-player` y `ms-app-shell`
5. El resto de organisms
6. Pages, en el orden del flujo clickeable
7. `COMPONENTS.md` y la verificación

Hacé commits chicos por capa. No mezcles cambios de runtime o Python con esta UI, salvo la ruta estática si hace falta.
