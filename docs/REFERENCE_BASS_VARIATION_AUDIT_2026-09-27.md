# Auditoría: variación de bajo sobre «pista Project»

Fecha: 2026-09-27. Alcance: una variación de 8 compases en la copia controlada
`pista_copilot_eval.als`, región QN 160–192, tempo 167 BPM.

## Evidencia comprobada

- El análisis de referencia y `MusicalUnderstanding` comparten referencia,
  análisis y proyecto. Las alturas provienen de MIDI de Ableton reconciliado:
  73 eventos en los 32 compases de la fuente. Los primeros 8 compases producen
  21 notas; se desplazan 7 ataques secundarios un cuarto de pulso, se conservan
  las alturas y los primeros ataques de cada compás, y se ajustan duraciones.
  La armonía pendiente de evaluación humana no selecciona notas. La respuesta
  de Astra, si existe, queda como contexto consultivo.
- `MusicPlan → ProductionCompiler → SafeWrite` verificó en Live cuatro acciones:
  pista nueva, Operator, clip MIDI de Session y una copia en Arrangement de
  QN 160 a 192. El registro de SafeWrite terminó `VERIFIED`, con lectura de
  21 notas y del clip de Arrangement. Ninguna acción musical apuntó a pistas
  originales.
- La captura aislada del nuevo clip terminó `HAS_SIGNAL`: 11,497 s,
  RMS −21,14 dBFS, pico 0,19537 y SHA-256
  `79707c3c048fd2daa8a81f91a07eb26be52e4b1880b9f4499d6b2af7d4070adc`.
  El host de captura volvió a su ruteo anterior, y el transporte quedó detenido.
  El set de trabajo se guardó. `pista.als` conservó el SHA-256
  `6e7c8dc5192038047409a82ec72bce9d0089c0c788fe71939aa8d5f4a38d93e8`.
  La comprobación canónica `ensure_ableton_ready` terminó `PROJECT_READY`
  sobre la copia guardada, con 21 pistas y la misma identidad de proyecto.
- El demo anterior de 32 compases tenía una vista previa aislada con pico 0 y
  todas las muestras en cero. Su registro se corrigió de `READY` a `FAILED`,
  razón `SILENT_PREVIEW_LEGACY`, sin borrar el WAV ni el registro.
- La primera prueba de esta implementación falló en un preflight heredado que
  exigía ruteo específico de batería. SafeWrite revirtió la pista nueva; el
  registro quedó `FAILED / CAPTURE_PREFLIGHT_NOT_READY`. La comprobación genérica
  que ya usaba el proyecto permitió la segunda prueba y mantuvo bloqueantes las
  verificaciones de identidad, hosts, copia de trabajo y transporte.

## Qué certifica el código

`READY` requiere clip en la región capturada, referencia MIDI vinculada al
proyecto, WAV aislado medido con señal, hash coincidente y duración correcta.
Una captura silenciosa o fallida revierte la nueva variación y deja un motivo
concreto. Las pruebas cubren identidad y procedencia de artefactos, ausencia de
MIDI, transformación, posición del clip y reversión. Un clip que quede solo en
Session no puede terminar `READY`; la regresión reproduce una confirmación de
escritura sin clip de Arrangement y verifica que también se retire la pista
nueva. Esa prueba descubrió y corrigió una reversión parcial de la transacción.

## Límites

El resultado técnico es MIDI editable y audio audible. La calidad musical y
la corrección armónica siguen pendientes de comparación humana con el bajo de
referencia. El descarte funciona en el mismo proceso de Studio que creó la
variación; después de reiniciarlo no hay recuperación de rollback certificada.
El proyecto de Studio usado aquí tiene configuradas las rutas de ambos
artefactos de referencia, de modo que su control de generación puede usar
esta región; otros proyectos requieren esa configuración o rutas explícitas.
Esto no certifica generación de 3 o 5 alternativas ni cualquier canción o
región.
