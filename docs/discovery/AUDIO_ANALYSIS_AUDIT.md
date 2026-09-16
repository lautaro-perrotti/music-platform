# Audio Analysis / MIR Audit

**Date:** 2026-09-13  
**GPU:** NVIDIA GTX 1660 SUPER 6 GB  
**Python analysis stack verified:** numpy 2.5.3, scipy 1.18.1, soundfile 0.14.0, pyloudnorm 0.2.0

---

## Decision

| Need | PRIMARY | FALLBACK | REJECT |
| --- | --- | --- | --- |
| Loudness / RMS / peak / LUFS | `pyloudnorm` + numpy | librosa (ISC) | Essentia in-process (`AGPL-3.0`) |
| Spectral / crest / stereo | numpy/scipy (ours) | librosa | — |
| BPM / beats / downbeats | later: `madmom` only if NC is acceptable, else build/beat tracker with permissive weights | session tempo as `INFERRED` | Do not guess |
| Key / scale / chords | later, only with sufficient confidence | none | Invented key |
| Embeddings | LAION CLAP (`laion/larger_clap_music`, MIT) | OpenL3 (MIT code, CC-BY 4.0 weights) | MERT, MuQ weights (`CC-BY-NC-4.0`) |
| Semantic caption | deferred | — | One giant multimodal model |

MVP measurement implemented and **executed** in `copilot.audio.measure`:

- duration, RMS, peak, crest factor, LUFS, spectral centroid, bass energy ratio  
- tempo/key left unset unless confidence is high enough  
- claims tagged `MEASURED` / `INFERRED` / `HYPOTHESIS`

---

## Essentia (MTG)

| Field | Finding | Status |
| --- | --- | --- |
| capabilities | Best-in-class MIR: BPM, key, loudness, spectral, tonal, extractors | `DOCUMENTED_ONLY` |
| accuracy | Strong, widely cited | `DOCUMENTED_ONLY` |
| latency / streaming | Offline extractors; streaming possible in C++ | `DOCUMENTED_ONLY` |
| Python | Bindings; Windows wheels historically weaker than Linux | `DOCUMENTED_ONLY` |
| license | **AGPL-3.0** + paid proprietary option | `LICENSE_BLOCKED` for closed commercial in-process use |
| decision | `REJECT` as a linked library. Revisit only as a **separate AGPL process** or after a commercial license. |

---

## pyloudnorm + numpy (verified)

Installed and executed in this environment.

| Field | Result |
| --- | --- |
| capabilities | ITU-R BS.1770 integrated LUFS, plus our RMS/peak/FFT features |
| license | MIT (`pyloudnorm`) |
| streaming | No (block/offline) |
| offline | Yes |
| commercial | Yes |
| integration | Low. Used now. |
| status | `VERIFIED` for LUFS/RMS/peak/spectral on generated fixtures |

Limitation: LUFS skipped below ~0.4 s. True-peak oversampling not implemented yet (`true_peak` currently equals sample peak).

---

## MERT

| Field | Finding |
| --- | --- |
| code license | Apache-2.0 (`yizhilll/MERT`) |
| weights | **CC-BY-NC-4.0** (`m-a-p/MERT-v1-*`, `MERT-v2-30s`) |
| commercial | No, without a separate grant |
| decision | `REJECT` for product embeddings |

---

## OpenL3

| Field | Finding |
| --- | --- |
| code | MIT |
| weights | CC-BY-4.0 (commercial OK with attribution) |
| maintenance | Older; still usable |
| GPU | Optional |
| decision | `WRAP` as embedding fallback |

---

## CLAP (LAION)

| Field | Finding |
| --- | --- |
| `laion/larger_clap_music` | Listed **MIT** on MTEB audio-text table |
| capabilities | Music-text similarity, retrieval, reference matching |
| VRAM | ~0.7 GB listed for the 194M music checkpoint |
| decision | `WRAP` as PRIMARY embedding model |

Microsoft CLAP variants exist; check each card. Do not assume all CLAP checkpoints are MIT.

---

## MuQ / MuQ-MuLan

Stronger recent music SSL / music-text model. **Weights CC-BY-NC-4.0**. `REJECT` for commercial product memory.

---

## Beat / key

No permissive, high-quality, well-maintained key/beat package was verified in this pass to the point of `VERIFIED`.

`madmom` models are typically CC-BY-NC-SA. Treat as `LICENSE_BLOCKED` until a specific checkpoint is re-checked.

Until then:

- session tempo from Ableton is `INFERRED`  
- do not publish key/BPM from audio unless a later verified estimator exceeds the confidence floors (`0.65` tempo, `0.70` key)

---

## Real-time

All of the above run in a worker, never in the Ableton audio callback. Our renderer/measure path is offline.
