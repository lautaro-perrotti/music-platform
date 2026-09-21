# NEW_PROJECT_VALIDATION

Procedure for a completely different real song.

No programming while someone waits. No manual tap/routing if bootstrap is VERIFIED.
Do not retune handshake, timeouts, State Trust, or capture unless a bug reproduces.

Live is ready only after `SESSION_READY`. A listening port is not enough.

## 1. Working copy

Core never duplicates the song. The operator does.

1. Duplicate the `.als` (never operate on the only copy).
2. Open **that manifest-backed working copy** in Ableton Live.
3. Stop the transport.
4. Wait until `doctor` reports `SESSION_READY` (Control Surface `AbletonMCP`).

Refuse the original source set. Only a manifest-backed working copy is eligible.
The bootstrap fixture is plumbing, not a musical holdout.

## 2. Health

```
python -m copilot.cli doctor
```

Expect `status=READY` and `LIVE_SESSION_READINESS_V1 = VERIFIED / FROZEN`.
If BLOCKED, stop. Doctor is read-only and does not print secrets.

## 3. Onboard

```
python -m copilot.cli onboard-project
```

Alias of `project-ready`. This is the only onboarding command:

1. identify current project + working-copy policy
2. refuse the original source set if it is still open
3. discover topology
4. bootstrap missing observation infrastructure
5. validate bootstrap (second plan must be `NO_CHANGES_REQUIRED`)
6. run preflight (generic, not the development-lab contract)
7. persist `logs/project_ready_v1.json`

Do not run Astra here. Do not write music.

## 4. Cross-project preflight

`onboard-project` already runs generic preflight. Optional explicit bootstrap check:

```
python -m copilot.cli project-bootstrap
```

A second bootstrap on a complete project must return `NO_CHANGES_REQUIRED`.

## 5. Read-only analyze

```
python -m copilot.cli producer-analyze
```

Optional:

```
python -m copilot.cli producer-analyze --start-qn 0 --end-qn 32
```

Inspect `logs/producer_analyze_v1.json` and `logs/evidence_pack_v1.json`.

Statuses must remain distinct. Abstention is success.

Empty arrangement → `INSUFFICIENT_EVIDENCE` is the correct outcome, not a failure of plumbing.

## 6. Only if read-only is green

On the **development working copy only**:

```
python -m copilot.cli producer-run --mode autonomous
```

On a new external song, autonomous SET_TRACK_VOLUME is `ACTION_NOT_AVAILABLE` until that project has a live-validated write. Do not invent a second write loop.

The frozen development runner remains:

```
python -m copilot.cli production-write --first-autonomous-musical-improvement
```

## Stop conditions

- transport playing
- original source set still open
- doctor BLOCKED / not `SESSION_READY`
- onboard-project BLOCKED
- open capture journals or IN_DOUBT transactions
- producer-analyze BLOCKED / DIAGNOSIS_UNSTABLE

Do not capture, do not write, do not retry Astra to force an action.
