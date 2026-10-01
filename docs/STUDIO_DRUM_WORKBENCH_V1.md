# Studio Drum Workbench V1

The read-only workbench is available at `/ui/drums.html`; its typed API is
`GET /api/drums/events`. It does not accept a path from the browser. Configure
both inputs in the Studio process environment:

- `COPILOT_STUDIO_DRUM_EVENT_SET`: an existing serialized `DrumEventSetV1`.
- `COPILOT_STUDIO_ASSET_SET_MANIFEST`: the asset-set manifest that owns the
  event set's `source_asset_id` and immutable source digest.

Before exposing event rows, the adapter validates the event schema, finds
exactly one source record in the explicitly configured manifest, matches the
source digest, and re-hashes the manifest's immutable source file. Missing or
stale data yields an empty typed unavailable response. Machine-local paths are
never included in the UI contract.

The workbench presents measured onsets against a provisional grid, role
hypotheses, and their evidence. Synthetic detector calibration is not applied
to source onsets; tempo and meter remain provisional/assumed where the source
artifact says so. The Ableton transport subscription reuses the negotiated
push-only `events.transport.v1` client and revalidates project identity. It
does not poll Live or perform musical writes. This transport contract has no
playhead position. No sample selection, MIDI realization, operation lifecycle,
or capture is implied by this read-only workbench.

## Drum sample matching and symbolic proposal (2026-10-01)

When the user-scoped sample library has an explicit root and current index,
the API reads that index and returns up to five deterministic acoustic
candidates per eligible KICK/CLOSED_HAT event. Ranking uses explicit centroid
and broad-band-energy deltas; source attack-window and whole-file descriptors
have different windows/band boundaries, which are returned as limitations.
Filename/folder role labels are metadata only, never a hard filter or ranking
authority. Missing roots, missing index, invalid index, or root/index mismatch
are typed states. This request path does not scan the filesystem.

The API also exposes `DrumReconstructionV1`, a symbolic, non-executable
proposal that preserves source event IDs, measured time, QN/grid projection,
source accent dBFS, and a separate derived velocity. The local proof map is
KICK→MIDI 36 and CLOSED_HAT→MIDI 42; it is not a permanent drum-map standard.
Accent velocity uses role-relative p10/p90 normalization and explicitly falls
back to 64 for under-1 dB spread. MIDI note duration is not inferred. The
proposal remains blocked on sample selection, human confirmation of inferred
roles/grid, note-duration policy, and the existing certified SafeWrite/Live
readback path. No musical writes occur.

On the current host, the sample-library config has no roots. Add a known,
authorized library directory explicitly with:

```powershell
python -m copilot.cli sample-library add "<absolute-library-directory>"
python -m copilot.cli sample-library index
```

Do not replace this with a whole-drive scan. A real candidate shortlist has not
yet been produced for the 32 reference events.
