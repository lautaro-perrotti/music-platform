# MIDI / Symbolic Music Audit

**Date:** 2026-09-13

---

## MVP architecture (selected)

```
LLM
  -> MusicPlan JSON
  -> Music Theory Validator (ours, later)
  -> MIDI Renderer (ours, now: deterministic note lists)
  -> DawAdapter
  -> Ableton / mock
```

A single generative MIDI model is **not** the MVP composer. Vertical slice 1 already writes exact C3 quarter notes without any MIDI LM.

---

## MidiTok

| Field | Finding |
| --- | --- |
| repo | https://github.com/Natooz/MidiTok |
| license | MIT |
| role | Tokenization (REMI, TSD, CPWord, …), not a generator |
| maintenance | Active, PyPI 2.1.x |
| decision | `WRAP` when/if we train or host a symbolic model |

---

## MIDI-RWKV

| Field | Finding |
| --- | --- |
| repo | https://github.com/christianazinn/MIDI-RWKV |
| code license | MIT |
| task | Long-context multi-track **infilling**, not text-to-MIDI |
| dataset | GigaMIDI **CC-BY-NC-4.0** |
| status | `DOCUMENTED_ONLY` |
| decision | `REFERENCE_ONLY` / likely `LICENSE_BLOCKED` for commercial weights derived from GigaMIDI |

---

## Text2MIDI

| Field | Finding |
| --- | --- |
| repo | https://github.com/AMAAI-Lab/text2midi |
| code | MIT |
| weights card | Apache-2.0 |
| data | MidiCaps / Lakh MIDI (CC-BY-4.0 on Lakh files) |
| quality | Research-grade caption → MIDI |
| decision | `REFERENCE_ONLY` for MVP. Possible later `WRAP` behind the same MusicPlan path as a suggestion engine, never as the only writer. |

---

## AbletonMCP "AI music helpers"

`generate_drum_pattern` / `generate_bassline` are Remote Script recipes (house, techno, …).  
**Decision:** `REJECT` as composition. They bypass MusicPlan and cannot be validated/undone at the musical-intent layer.

---

## BUILD ourselves (confirmed)

- `MusicPlan` schema — implemented  
- Deterministic MIDI renderer for plans — slice 1 notes helper implemented  
- Theory validator — not yet  
- Reharmonization / voicing — later, rule-based first  

Editable composition belongs in MIDI + Live clips, not in a baked audio model.
