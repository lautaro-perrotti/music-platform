# Session state trust

The Core may not write musically until it knows:

- which Live Set it is looking at
- which object a plan refers to
- whether observed state is still current
- whether a cached WAV is still valid

Integer `session.revision` is an **in-process observation counter** derived from
tokens. It resets on Core reconnect. It is not identity. Plans and cache keys
use cryptographic tokens.

## Tokens

Canonical JSON (`state-canon-1`), `sort_keys`, 6-decimal floats, SHA-256.

| Token | Includes | Excludes |
| --- | --- | --- |
| `PROJECT_STATE_TOKEN` | track **order**, names, roles, routing, device chain structure, clip slots/names/length/note counts, tempo, signature | mixer, playback cursor, selection, runtime IDs |
| `AUDIBLE_STATE_TOKEN` | mixer, routing, sends, device parameters, clip notes, tempo, signature | names, track order, selection, playback |
| `TARGET_STATE_TOKEN` | one object's name + mixer + routing + sends + devices/params + clips/notes | index, runtime id |

Scoped audible: `MASTER_CONTEXT` hashes the whole set. Isolated views hash only
the named source. Kick volume does not invalidate an unrelated Vocal isolate.

## What invalidates AudioAsset

Invalidate when any of these change for the asset's audible scope: clip content
and MIDI, clip length, mute/solo/volume/pan/arm, routing, sends, device chain
and parameters, tempo, time signature, source selection.

Do **not** invalidate for: track selection, UI/browser, playback cursor,
irrelevant metadata on tracks outside the scope.

Incorrect reuse is forbidden. Conservative over-invalidation is allowed.

## Object identity

`RuntimeObjectId`: `session_incarnation_id` + process `stable_id` + index locator.
Valid only for this Core connection.

`PersistentObjectRef`: project identity + role + device/clip fingerprint.
Name is weak evidence only. Index is never identity.

Reconciliation returns exactly one of `RESOLVED` / `TARGET_NOT_FOUND` /
`TARGET_AMBIGUOUS` / `PROJECT_MISMATCH`. No best guess.

## Project identity

Prefer Live `get_session_path` (`kind=live_set_path`). Untitled/unsaved sets
fall back to a structural fingerprint (`kind=structural_fingerprint`) which can
collide across similar templates. Project mismatch → `PROJECT_MISMATCH`, no write.

## Plan envelope

Every future action carries `project_token`, `observed_state_token` (with
scope), `target_ref`, `target_state_token`, evidence asset IDs. `created_at` is
audit only.

Before write: read current relevant state and compare. Fail closed:

`PROJECT_MISMATCH` `STALE_PLAN` `TARGET_NOT_FOUND` `TARGET_AMBIGUOUS`
`STATE_UNAVAILABLE` `STATE_TOKEN_MISMATCH` `CACHE_STALE`
`IDENTITY_RECONCILIATION_FAILED`

## Mutation table

See `MUTATION_TABLE` in `src/copilot/daw/state_tokens.py`.

| mutation | PROJECT | AUDIBLE (session) | TARGET |
| --- | --- | --- | --- |
| volume / mute | no | yes | yes |
| routing | yes | yes | yes |
| rename target | yes | no | yes |
| reorder target | yes | no | no |
| insert unrelated | yes | yes | no (target + scoped audible) |
| target MIDI | yes | yes | yes |
| device parameter | no | yes | yes |
| playback / selection | no | no | no |

## Cache key

`project_identity + audible_token + region + source + view + signal_point + capture protocol + sample rate + capture-prod-1`

Provenance: file exists, sha256, journal `VERIFIED`, protocol/signal-point match.
Any mismatch → `CACHE_MISS`. Never “probably reusable”.
