# ASTRAL — Autonomous Music Producer (Plan de implementación)

## Objetivo
Convertir ASTRAL en el **cerebro productor**: inspecciona Ableton, decide, ejecuta vía MCP, verifica por readback, corrige y entrega un track completo (no solo ideas).

## Principios de diseño
1. **Execute > Explain**: si existe herramienta MCP, se ejecuta.
2. **Inspect → Plan → Execute → Re-inspect → Self-correct** en cada fase.
3. **Python guarda integridad** (rollback, tokens, verificación), **ASTRAL decide música**.
4. **Token efficiency**: contexto compacto, estado resumido, prompts incrementales.

---

## Arquitectura objetivo

### 1) Producer Brain (LLM loop)
Nuevo módulo: `src/copilot/producer/brain.py`

Entradas:
- `ProducerStateSummary` (estado compactado del set)
- `ArtisticContextPack` (reglas de groove/identidad/arreglo/mix)
- `GoalContract` (pedido del usuario)

Salidas:
- `ProductionDecisions` (estructura, densidad, roles, variaciones, mix intents)
- `ActionBatch` tipado (`MusicPlan` + metadata de hipótesis)

### 2) State Summarizer (bajo costo)
Nuevo módulo: `src/copilot/producer/state_summary.py`

Condensa snapshot grande a:
- tempo, compás
- tracks activos, clips por sección
- densidad por sección
- kick/bass/perc relationship
- hooks activos y conflictos
- headroom aproximado (estructural)
- pendientes de QA

### 3) Planner jerárquico
Nuevo módulo: `src/copilot/producer/planner.py`

Pipeline:
- Macro plan: secciones + energía
- Micro plan: qué cambia por sección
- Tech plan: acciones MCP concretas

### 4) Executor con verificación
Reusar `execute_track_build_plan` + loops controlados.
Extender con:
- `arrangement_apply` (duplicate to arrangement por barras)
- `mix_apply` (balance + chain + sidechain checks)
- `qa_apply` (reglas + fixes automáticos)

### 5) Critique + repair loop
Usar `critique.py` como gate:
- `verdict=improve` => corrección automática focalizada (1 pase por issue)
- límite de iteraciones (ej. 3)
- política **modify-before-adding**

---

## Token efficiency (clave)

### A. Context packs estáticos (cacheables)
Separar contexto en packs:
- `genre_pack` (tech house/latin tribal)
- `mix_pack`
- `fx_pack`
- `decision_pack`

Cada pack con:
- versión
- hash
- 10-20 reglas máximas (sin prose largo)

### B. Prompt en dos niveles
1. **System compacto fijo** (rol + loop operativo + límites)
2. **Task incremental** (solo delta del estado y objetivo actual)

Evitar mandar:
- snapshots completos
- listas grandes de samples completas en cada iteración

### C. Candidate compression
Para selección de samples:
- top-k por rol (k=3)
- features mínimas: `filename`, `role`, `bpm`, `energy_tag`
- no enviar metadatos redundantes

### D. Stateful memory local
Persistir `producer_memory.json` por sesión:
- decisiones ya tomadas
- elementos descartados
- razones cortas

Así no se re-razona desde cero en cada vuelta.

---

## Plan por etapas (implementación)

### Etapa 0 — Hardening inmediato (1-2 días)
- quitar buses por defecto (todo a Main salvo excepción explícita)
- limpiar set inicial (sin tracks de prueba huérfanos)
- guardrails de escala (volumen/ceiling normalizados)

### Etapa 1 — ASTRAL decide arreglo (2-3 días)
- reemplazar arreglo fijo por `ArrangementDecision` de ASTRAL
- validación estructural Python (longitud total, secciones válidas, densidad)
- aplicar en Arrangement con duplicate-to-arrangement

### Etapa 2 — ASTRAL decide identidad y densidad (2-3 días)
- hook primario obligatorio
- política de sustracción automática en DROP/BREAK
- control de conflictos (hook overlap, perc crowding)

### Etapa 3 — Mix autónomo inicial (2-4 días)
- balance por objetivos (kick anchor)
- sidechain verify (routing real)
- limiter ceiling correcto + headroom premaster

### Etapa 4 — Self-correction loop (3-5 días)
- critique -> repair batch -> re-critique
- stop criteria: finalize / max_iter / no_gain

### Etapa 5 — Modo producción nocturna (1-2 días)
- `vibe --autonomous --target "..."`
- checkpoints cada N minutos
- reporte final + TODO audible

---

## Contratos de datos nuevos

1. `ArrangementDecision`
- secciones[{name,bars,energy,active_roles}]
- outro_policy
- transition_points

2. `IdentityDecision`
- primary_hook_track
- answering_tracks
- forbidden_overlaps

3. `MixIntent`
- target_balance_profile
- low_end_owner_map
- sidechain_contract

4. `QAVerdict`
- pass/fail por eje
- fix_actions mínimas

---

## Criterios de aceptación (DoD)
1. ASTRAL genera track completo en Arrangement (no solo Session).
2. Arreglo no fijo: lo decide ASTRAL y pasa validación.
3. Hook primario explícito y conflicto de densidad controlado.
4. Mix rough coherente (sin silencios por escala mal mapeada).
5. Critique final con <=3 issues mayores o `finalize`.
6. Todo verificable por readback MCP + snapshot.

---

## Riesgos y mitigación
- **LOM quirks**: setters no disponibles -> usar estrategias compatibles (tiling por barras).
- **Deriva de tokens**: usar state summary + deltas.
- **Falsos positivos de "éxito"**: siempre readback post-acción.
- **Sobre-complejidad musical**: regla dura "modify before adding".

---

## Próximas 3 tareas concretas
1. Implementar `ArrangementDecision` + parser/validator + wiring en `cli._vibe`.
2. Sustituir `TECH_HOUSE_ARRANGEMENT` fijo por decisión de ASTRAL.
3. Agregar `producer_memory.json` y prompts delta para bajar costo de tokens.
