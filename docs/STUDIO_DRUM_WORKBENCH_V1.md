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
