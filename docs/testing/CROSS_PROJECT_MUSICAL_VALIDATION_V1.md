# CROSS_PROJECT_MUSICAL_VALIDATION_V1

Next milestone. **Tests first.** No EQ, compressor, MIDI, references, or new models
until a real unseen song says they are needed.

Status today: **WAITING_FOR_EXTERNAL_SONG**.

There is no second real `.als` in this lab besides `pista` / its working copy /
the bootstrap fixture. `Sin título.als` is the same 67 KB fixture, not a holdout.

## First pass (read-only)

Operator duplicates a real song (Core never copies the only file), opens that
working copy, transport stopped, `SESSION_READY`, then:

```
python -m copilot.cli cross-project-validate
```

That is:

```
doctor → onboard-project → preflight → producer-analyze
  (region by activity → Main + active Post Mixer → evidence pack → Astra once)
```

Zero musical writes. No code changes for that project.

## What the result authorizes

| Result | Next development |
| --- | --- |
| plumbing/identity/capture bug | Fix that bug. Rerun the same test. |
| `INSUFFICIENT_EVIDENCE` / `DIAGNOSIS_UNSTABLE` | Audit missing evidence. Perception only if justified. |
| `ACTION_NOT_AVAILABLE` | The gate names the next action type. Only then EQ/comp/MIDI/etc. |
| `SUPPORTED` + `SET_TRACK_VOLUME` + `LEVEL_IMBALANCE` | MusicPlan volume write → recapture → KEEP/ROLLBACK |
| understands the song (`SUPPORTED` / `NO_ACTION_REQUIRED`) | Read-only generalization VERIFIED, then external autonomous |

## Roadmap after the first holdout

1. Second real song → this certification
2. First external autonomous evaluation
3. Audit the first real limit
4. Implement **one** capability to close that limit
5. Retest on new material
6. Repeat

Do not stockpile features that have not improved a producer on a real song.
