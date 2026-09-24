# MUSIC_STUDIO_VERTICAL_SLICE_V1

Status: `IMPLEMENTED / REAL_PROVIDER_REQUIRED`.

This is the first local Music Studio application boundary. It connects the
existing provider-neutral generation contracts to a durable local application
without creating a second Ableton write authority.

## Supported path

```text
Music Studio UI
    → Project
    → typed GenerationBrief
    → durable Generation Job
    → existing real provider boundary
    → managed immutable Artifact
    → Candidate
    → persisted event stream
    → browser playback / Range seek
    → KEEP
    → non-destructive Version
```

The implementation lives under `src/copilot/studio/`:

- `contracts.py` — project, job, event, artifact, candidate and version contracts;
- `store.py` — SQLite durable state and ordered per-job events;
- `service.py` — provider-neutral generation orchestration and artifact registration;
- `server.py` — localhost REST/SSE/audio server;
- `static/index.html` — simple Create / Listen / Keep UI.

## Run

```bash
python -m copilot.cli studio --port 8765
```

The durable store defaults to `runtime/music-studio`. Override it with
`COPILOT_STUDIO_DATA_DIR` or `--studio-data-dir`.

The browser never receives a filesystem path. Audio is addressed by an
artifact ID and served only from the managed artifact root with HTTP Range
support.

## Provider boundary

The Studio uses the existing `AceStepProvider` when `ACESTEP_API_URL` is
configured, and the existing ElevenLabs Music provider when
`ELEVENLABS_API_KEY` is configured. If neither authorized provider is
available, the durable job ends in `BLOCKED / REAL_PROVIDER_REQUIRED` and no
candidate or fake audio is created.

The first implementation does not silently substitute fixtures, sine waves,
or mock audio for real-provider certification. Isolated tests may use a test
provider, but a production status must remain blocked until a real provider
is authorized and returns a technically valid WAV.

## Safety boundary

The Studio has no Ableton mutation path. Ableton status is read-only and
reports `MUSICAL_WRITES = 0`. Keeping a candidate creates a durable,
non-destructive Version manifest; it does not modify an `.als` file.

The following remain outside this milestone:

- Stem Review Lab and stem benchmark reruns;
- Lucas-owned file changes;
- direct browser → Ableton writes;
- arbitrary path, shell, Python or LOM endpoints;
- mixer, DAW, collaboration and mastering UI;
- automatic musical winner claims.

## Verification

The focused vertical-slice tests cover:

- durable project/job/candidate/event/version persistence;
- idempotent generation submission;
- ordered candidate-ready events;
- non-destructive KEEP;
- honest blocking when no real provider is configured.
