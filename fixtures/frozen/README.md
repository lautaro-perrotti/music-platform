# Frozen evidence fixtures

Committed, immutable inputs for regression tests.

## Why this directory exists

`logs/` is runtime and gitignored. `logs/evidence_pack_v1.json` is a **single
mutable slot**: every `producer-analyze` run on any project overwrites it via
`persist_evidence_pack`. A regression test that pins a specific `pack_id` in
that slot therefore fails the moment the product is run on a different project.

That is exactly what happened on 2026-09-17: a traced `producer-analyze` on the
Groove Rider working copy overwrote the `pack_afe4d6403ee4` pack that
`ASTRA_EXTERNAL_REASONING_V1` was frozen against, and the content is not
recoverable (`logs/` is not versioned, and the surviving artifacts record only
the pack id and payload hash, not the pack body).

## Contract

- Files here are **inputs**, never outputs. No product code writes to this
  directory.
- `EXPECTED_PACK_ID` in `copilot/audio/astra_external_reasoning_v1.py` stays the
  authority on which pack is canonical. Do not change it to match whatever
  happens to be on disk.
- A test that needs the canonical pack reads it from here and skips with an
  explicit reason when it is absent, rather than reading the runtime slot.

## Re-seeding `evidence_pack_v1_astra_external_reasoning.json`

Open the `pista_copilot_eval.als` working copy in Live, run
`python -m copilot.cli producer-analyze`, confirm
`logs/evidence_pack_v1.json` reports `pack_afe4d6403ee4`, then copy it here
under that name. Until then the pinned assertion is not exercised.
