# AI Producer — Roadmap (vibe coding en Ableton)

**Visión:** track de referencia + librería + prompt → Astra desarrolla el track
(swap de samples + tweaks de devices nativos + mezcla en grupos + mastering),
con rollback + verificación. UX tipo vibe coding; motor con red de seguridad.

**Principio:** MEASURE != DIAGNOSE != DECIDE != ACT != IMPROVE. El LLM razona;
el Core es dueño de la realidad (State Trust, rollback durable, provenance,
abstention > intervention). Nativos (EQ, saturación, compresión, delay, reverb,
pitch) porque son legibles, con unidades reales y DSP conocido.

## Hecho (fundación read-only + executor)
- Live session readiness, captura, DSP (lowend/fullmix/measure).
- Downstream causal + sidechain/automation (read-only).
- **Sample library**: discovery, index incremental, DSP factual, clasificación,
  embeddings (stub, CLAP reemplazable), retrieval por descriptores, SampleSetContext.
- Reference analyzer (Lautaro) → targets.
- **KEY_DETECTION_V1** — chroma → tonalidad de samples.
- **PRODUCTION_EXECUTOR_V1** — `MusicPlan` tipado + 4 acciones
  (SET_TRACK_VOLUME, DEVICE_TWEAK, DEVICE_LOAD, SAMPLE_SWAP) con rollback
  durable (journal `plan_write`/`record` + `inverse_operation`) + readback + verify.
- **CLIP_AUDIO_MODEL_V1** — `ClipState.sample_uri` + SAMPLE_SWAP con readback preciso
  del clip (swap → verificar sample → rollback → verificar restauración).

## Estrategia de prueba (escritura)
NO track armado (Duck2 fue para análisis read-only). El test de escritura es
**vibe coding desde plantilla de 0**: set vacío → prompt → Astra arma drums/bass/hats
con la librería → executor aplica → escuchar → KEEP/ADJUST/REPLACE.

## Milestones (en orden)
1. ✅ KEY_DETECTION_V1
2. ✅ PRODUCTION_EXECUTOR_V1 (+ CLIP_AUDIO_MODEL_V1)
3. ✅ TRACK_BUILDER_V1 — "vibe coding de 0": receta groovy/latin tech house (127 BPM,
   percusión-first, retrieval latino) + multi-acción en un MusicPlan. Incluye:
   - RHYTHM_V1: patrones de groove (velocity + micro-timing: 4-on-floor, swing,
     tumbao, son clave, bajo syncopado).
   - ARRANGEMENT_V1: estructura 8/16/32 por substracción/variación (SET_TRACK_MUTE).
   - MIXING_V1: cadenas nativas por pista + master (EQ/Comp/Saturator/DrumBuss/Echo).
   - CLI `track-build "groovy latin tech house"` → arma todo y muestra set+estructura.
4. **MIX_GROUPS_V1** — mezcla en grupos de drums/buses (Drum Buss group) con nativos.
5. **MASTERING_V1** — bus master real (EQ+Glue+Saturator+Limiter→LUFS target) + stems.
6. **ASTRA_IN_THE_LOOP** — prompt → Astra razona (receta + librería) → MusicPlan,
   reemplazando la receta estática (hoy determinista).
7. **ABLETON_REAL_V1** — validación contra Ableton real (bridge TCP), incl. sidechain
   DEVICE_TWEAK con params reales (el mock usa params genéricos).
8. **M4L_FRONT_V1** — control surface fino (referencia, chat, librería, candidatos,
   gate aprobar/rollback) vía OSC → daemon Python.

## Diferenciación
Local-first (no SaaS), Core dueño de la realidad, rollback + verificación, nativos.
