# Audio Generation Audit

**Date:** 2026-09-13  
**Local GPU:** GTX 1660 SUPER 6 GB — ACE-Step 1.5 XL is out of scope on this machine.

Generation is **not** part of vertical slice 1. This document only selects wrap points.

---

## Local: ACE-Step 1.5

**Official repo:** https://github.com/ace-step/ACE-Step-1.5  
**Code license (fetched):** MIT, copyright 2026 ACEStep  
**Weights card:** `ACE-Step/acestep-v15-base` lists `license: mit` and states generated music may be used commercially  
**Status:** `DOCUMENTED_ONLY` (repo/license fetched; model not downloaded — C: has ~3.8 GB free)

| Field | Evidence |
| --- | --- |
| text-to-music | Yes |
| reference audio | Yes (`Refer audio` column) |
| continuation / complete | Yes on `v15-base` |
| variation / cover / repaint | Yes |
| extract / lego / stems | `v15-base` lists Extract/Lego; SFT/turbo drop some edit modes |
| multitrack | Not a DAW-native stem session. Treat output as audio assets. |
| duration control | Documented via plan/LM; not executed here |
| VRAM | README: <4 GB for 1.5; XL (≥12 GB, 20 GB recommended) |
| API | Gradio + REST in the official 1.5 repo (not executed) |
| latency | Claimed seconds-to-song on 3090/A10. Unverified on 1660 Super. |

### License split (do not collapse)

| Layer | Finding | Risk |
| --- | --- | --- |
| CODE LICENSE | MIT | Low |
| WEIGHTS LICENSE | Card says MIT for `acestep-v15-base` / sft / turbo | Medium — still verify each XL card before shipping |
| DATASET RESTRICTIONS | Authors claim licensed + RF + synthetic | Cannot audit dataset here |
| COMMERCIAL USE | Explicitly claimed for generated music | Residual style-similarity / copyright risk remains |
| REDISTRIBUTION | MIT allows code/weight redistribution; still attribute | Medium |

**Decision:** `WRAP` behind `MusicGenerationProvider`. Do not couple the agent to ACE-Step. Do not download XL onto this GPU. Do not treat MIT as a warranty that every output is legally clear.

---

## Cloud

### Suno official API

Multiple 2026 sources state **there is no official public developer API**. Third-party "Suno APIs" are unofficial.  
**Decision:** `REJECT` as a product dependency.  
**Status:** `DOCUMENTED_ONLY`

### Google Lyria

Official: Gemini API / Interactions API (`lyria-3-clip-preview`, `lyria-3.5`). Vertex has older `lyria-002`.  
Outputs include SynthID watermarking. Terms and pricing change; do not hardcode.  
**Decision:** `WRAP` as a cloud fallback provider.  
**Status:** `DOCUMENTED_ONLY`

### Other relevant APIs

MiniMax Music and similar hosts exist. Evaluate later under the same provider interface. None become the architecture.

---

## Provider interface (conceptual, implemented as a contract later)

```python
class MusicGenerationProvider:
    def generate(self, prompt, duration, **cond): ...
    def continue_audio(self, audio, prompt, **cond): ...
    def create_variation(self, audio, prompt, **cond): ...
    def inpaint(self, audio, region, prompt, **cond): ...
    def create_stem(self, audio, stem, **cond): ...
    def get_job(self, job_id): ...
```

Compare later on: quality, control, latency, API maturity, price, commercial terms, upload requirements, privacy, rate limits, output ownership, availability.

---

## Stable Audio / sound design

| Artifact | License | Decision |
| --- | --- | --- |
| Stable Audio Open 1.0 | Non-commercial unless Stability membership / commercial license. Hosted API use restricted. | `REJECT` for default product path (`LICENSE_BLOCKED`) |
| Stable Audio 3 Medium community license | Commercial below $1M revenue with registration; terminates above | `WRAP` only with an explicit license gate |
| ACE-Step 1.5 | Better default for textures/songs locally | `WRAP` |

For textures, FX, one-shots: prefer generating into an **asset store**, then import to Ableton. Do not generate inside the audio thread.

---

## Stem separation

| Candidate | License | Maintenance | Decision |
| --- | --- | --- | --- |
| `facebookresearch/demucs` | MIT | **Archived 2025-01-01** | `REJECT` as upstream |
| `adefossez/demucs` | MIT | Author says this is the maintained fork; v4.1.0 noted 2026-11-07 | `WRAP` PRIMARY |
| `audio-separator` | MIT | Actively packaged; wraps Demucs/UVR models | `WRAP` as installable inference facade |
| Random torchcodec forks | MIT | Tiny, unproven | `REFERENCE_ONLY` |

4-stem: vocals / drums / bass / other. `htdemucs_6s` adds guitar/piano (piano weak).  
6 GB VRAM: expect segmented inference or CPU.  
**Interface must be replaceable.** Weights/training-data provenance of UVR models must be checked per checkpoint before commercial redistribution.

---

## ASR / lyrics / vocals

### Qwen3-ASR (required)

**Repo:** https://github.com/QwenLM/Qwen3-ASR  
**Weights:** `Qwen/Qwen3-ASR-0.6B`, `Qwen/Qwen3-ASR-1.7B`  
**License:** Apache-2.0 (code + cards)  
**Status:** `DOCUMENTED_ONLY`

| Need | Evidence |
| --- | --- |
| spoken voice | Yes |
| singing | Yes (M4Singer / MIR-1k numbers published) |
| vocals + accompaniment | Yes (`EntireSongs-en/zh`) |
| Spanish / English | Both listed |
| timestamps | Separate `Qwen3-ForcedAligner-0.6B` |
| streaming / offline | Both |
| GPU | 0.6B is the realistic local fit for 6 GB |

Whisper-large-v3 is weaker on singing in their table. Keep Whisper only as speech fallback.

**Decision:** `WRAP` Qwen3-ASR-0.6B as PRIMARY asr_model. Do not load 1.7B on this GPU unless offload is proven.

---

## Audio → MIDI

### Basic Pitch (required)

**Repo:** https://github.com/spotify/basic-pitch  
**License:** Apache-2.0 (Spotify AB)  
**Status:** `DOCUMENTED_ONLY` (not installed this pass — disk)  
Polyphonic, instrument-agnostic, best on single-instrument sources. Pitch + onset + duration + pitch-bend. Velocity is estimated, not production-grade. Lightweight CPU/ONNX/CoreML.

**Decision:** `WRAP` as MVP transcription. Good enough to draft MIDI, not to trust blindly. Always show notes as `INFERRED` until a user or Ableton clip confirms them.

Alternatives (not selected for MVP): MT3, piano-only transcription models — heavier, narrower, or murkier licenses.
