# AI Producer — Roadmap (vibe coding en Ableton)

**Visión:** track de referencia + librería + prompt → Astra desarrolla el track
(swap de samples + tweaks de devices nativos + mezcla en grupos + mastering),
con rollback + verificación. UX tipo vibe coding; motor con red de seguridad.

**Principio:** MEASURE != DIAGNOSE != DECIDE != ACT != IMPROVE. El LLM razona;
el Core es dueño de la realidad (State Trust, rollback durable, provenance,
abstention > intervention). Nativos (EQ, saturación, compresión, delay, reverb,
pitch) porque son legibles, con unidades reales y DSP conocido.

## Hecho (fundación read-only)
- Live session readiness, captura, DSP (lowend/fullmix/measure).
- Downstream causal + sidechain/automation (read-only).
- **Sample library**: discovery, index incremental, DSP factual, clasificación,
  embeddings (stub, CLAP reemplazable), retrieval por descriptores, SampleSetContext.
- Reference analyzer (Lautaro) → targets.

## Milestones (en orden)
1. **KEY_DETECTION_V1** — chroma → tonalidad de samples; "scan library by key". Cierra el gap de StudioPilot.
2. **PRODUCTION_EXECUTOR_V1** — `MusicPlan` tipado + aplicar (SAMPLE_SWAP, DEVICE_LOAD, DEVICE_TWEAK) con rollback durable + readback + verificación.
3. **MIX_GROUPS_V1** — mezcla en grupos de drums/buses con nativos.
4. **MASTERING_V1** — bus master (EQ+glue+limiter→LUFS target) + análisis de stems.
5. **ARRANGEMENT_V1** — intro/build/drop, locators, variaciones ("finish tracks").
6. **M4L_FRONT_V1** — control surface fino (referencia, chat, librería, candidatos, gate aprobar/rollback) vía OSC → daemon Python.

## Diferenciación
Local-first (no SaaS), Core dueño de la realidad, rollback + verificación, nativos.
