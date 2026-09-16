# Final Component Selection

## Ableton control

**PRIMARY:** Wrap `jpoindexter/ableton-mcp` Remote Script TCP JSON protocol.  
**FALLBACK:** Wrap `ideoforms/AbletonOSC`.  
**REJECTED:** Using either MCP server as the product agent; ahujasid telemetry build; generic LOM RPC.  
**BUILD_OURSELVES:** `DawAdapter`, `SessionState`, identity registry, typed tools, `TransactionManager`, Live install harness.

Evidence: 128 MCP tools counted; 257 Remote Script commands counted; `create_midi_track` drops name; `add_notes_to_clip` replaces notes; no audio tap; Live not installed (`BLOCKED_BY_ENVIRONMENT`).

---

## Audio analysis

**PRIMARY:** numpy + scipy + `pyloudnorm` (executed).  
**FALLBACK:** librosa; OpenL3 embeddings; later a licensed beat/key estimator.  
**REJECTED:** Essentia in-process (AGPL); MERT/MuQ weights (NC).  
**BUILD_OURSELVES:** `MusicObservation`, claim kinds, confidence floors, compare/before-after.

---

## Audio → MIDI

**PRIMARY:** Basic Pitch (wrap).  
**FALLBACK:** none for MVP.  
**REJECTED:** Treating transcription as ground truth.  
**BUILD_OURSELVES:** Mapping into `MidiNote` + Ableton write.

---

## ASR / lyrics

**PRIMARY:** Qwen3-ASR-0.6B.  
**FALLBACK:** Qwen3-ASR-1.7B off-box; Whisper for speech-only.  
**REJECTED:** Cloud-only ASR as the only path.  
**BUILD_OURSELVES:** Alignment into lyrics regions on `MusicObservation`.

---

## Stem separation

**PRIMARY:** `adefossez/demucs` (`htdemucs`) behind a replaceable interface.  
**FALLBACK:** `audio-separator` packaging.  
**REJECTED:** Archived `facebookresearch/demucs` as the dependency pin.  
**BUILD_OURSELVES:** `SeparationProvider` + asset manager.

---

## Local generation

**PRIMARY:** ACE-Step 1.5 `acestep-v15-base` (not XL).  
**FALLBACK:** Cloud Lyria via provider interface.  
**REJECTED:** Suno unofficial APIs; Stable Audio Open 1.0; ACE-Step XL on 6 GB.  
**BUILD_OURSELVES:** `MusicGenerationProvider`, asset import into Live.

---

## Sound design

**PRIMARY:** Same generation provider (ACE-Step) into assets.  
**FALLBACK:** Licensed commercial SFX APIs later.  
**REJECTED:** Stable Audio Open NC as default.  
**BUILD_OURSELVES:** Asset Manager.

---

## MIDI generation

**PRIMARY:** LLM → `MusicPlan` → deterministic MIDI (implemented for slice 1).  
**FALLBACK:** Text2MIDI as a suggester only.  
**REJECTED:** MIDI-RWKV for commercial; AbletonMCP drum/bass recipes as the composer.  
**BUILD_OURSELVES:** Plan schema, validator (next), renderer.

---

## Mixing / mastering

**PRIMARY:** Measure → plan → Live parameter change → re-measure (slice 3).  
**FALLBACK:** none.  
**REJECTED:** Matchering in-process (GPL); Diff-MST (NC-SA); LLM-only mix.  
**BUILD_OURSELVES:** Critics, mix plans, verification.

---

## Embeddings / memory

**PRIMARY:** LAION CLAP when we add retrieval.  
**FALLBACK:** OpenL3.  
**REJECTED:** MERT/MuQ commercially.  
**BUILD_OURSELVES:** Observation-linked memory records. No vector DB in this phase.

---

## Conversational / agent model

**PRIMARY:** OpenAI-compatible tool-calling model via `ModelRouter` (unassigned until credentials exist).  
**FALLBACK:** Local OpenAI-compatible endpoint.  
**REJECTED:** One multimodal model that also generates audio and talks to Live.  
**BUILD_OURSELVES:** Tool policy, validation, SessionState reasoning loop.

---

## Still ours (hypothesis confirmed)

These were not found in reusable form at production quality + license:

- DawAdapter abstraction  
- SessionState + sync + stable IDs  
- MusicObservation / MusicPlan  
- Agent core + permissioned tools  
- Transaction Manager + exact agent undo  
- ModelRouter  
- Asset Manager  
- Event/audit store  
- Before/after verification loop  
- Project memory  
- Evaluation framework  
- Live audio tap / bounce
