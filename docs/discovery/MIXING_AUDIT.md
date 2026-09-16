# Mixing / Mastering Audit

**Date:** 2026-09-13

We will not let an LLM "guess" a mix. Preferred loop:

```
MEASURE -> CRITIC -> HYPOTHESIS -> MIX PLAN -> ABLETON PARAMS -> RE-MEASURE -> COMPARE
```

Slice 3 implements a conservative version of this against a mock EQ parameter and real measurements.

---

## Matchering

| Field | Finding |
| --- | --- |
| repo | https://github.com/sergree/matchering |
| license | **GPL-3.0** |
| capability | Reference matching: RMS, spectrum, peak, stereo width + Hyrax limiter |
| maintenance | README still updated in 2025–2026 |
| commercial | Linking into a proprietary distributed app is a GPL problem. Isolated SaaS is a legal question, not a technical one. |
| decision | `REFERENCE_ONLY`. Do not import into our package. Optional future **separate process** only after legal review. |

Status: `DOCUMENTED_ONLY`

---

## Diff-MST

| Field | Finding |
| --- | --- |
| repo | https://github.com/sai-soum/Diff-MST |
| license | **CC-BY-NC-SA-4.0** |
| capability | Research differentiable mixing style transfer |
| decision | `REJECT` (`LICENSE_BLOCKED` + research code) |

---

## What actually helps the product now

| Tool | Use |
| --- | --- |
| Our `MusicObservation` | MEASURE |
| Simple critics (bass_energy_ratio, LUFS, centroid) | CRITIC |
| `MusicPlan` with constraints | MIX PLAN |
| `DawAdapter.set_mixer_volume` / `set_device_parameter` | APPLY |
| `compare_observations` | COMPARE |
| `TransactionManager` | ROLLBACK |

No full mastering chain in MVP. No autonomous mastering.

---

## Later wrap candidates (not selected)

- `pedalboard` (Spotify, GPL-3 — same caution as Matchering)  
- Commercial cloud mastering APIs — provider interface only
