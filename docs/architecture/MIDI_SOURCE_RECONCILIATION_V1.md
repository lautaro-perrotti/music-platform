# MIDI_SOURCE_RECONCILIATION_V1

This read-only bridge reconciles an authoritative MIDI source captured from
Ableton when a historical `PersistentObjectRef` no longer matches the raw
`.als` representation exactly.

## Resolution rule

The strict `match_als_track` path remains unchanged and continues to protect
mutation and general state resolution. The MIDI-only bridge may resolve a
stale fingerprint only when all of the following hold:

1. the caller has already verified the project identity;
2. exactly one same-name candidate exists;
3. the candidate is a MIDI track and retains the persisted track index as a
   locator;
4. a persisted arrangement clip name and exact QN span match a current
   arrangement MIDI clip in the requested region.

Name or index alone never resolves a source. Multiple same-name candidates,
missing arrangement evidence, or an index change fail closed. The result
records `fingerprint_status=STALE_OR_REPRESENTATION_MISMATCH` so the stale
runtime descriptor is not mistaken for a fresh identity.

## Real Rose Bass result

The working copy retained the same project identity and a unique `Rose Bass`
track at index 5. Its persisted arrangement evidence matched the current
`160–192 QN` clip. The bridge read 73 MIDI note events from the `160–288 QN`
reference region without mutating Ableton.

The previous audio-only transcription had 13 candidates and 3 reliable notes.
Using a ±0.15 QN onset tolerance, the comparison was:

| Measure | Result |
|---|---:|
| MIDI note count | 73 |
| Audio candidates | 13 |
| Audio reliable | 3 |
| Onset matches | 2 |
| Pitch matches | 0 |
| Missed MIDI attacks | 71 |
| False audio candidates | 11 |
| Octave errors | 0 |

These are source-comparison facts. They do not assert that the bass is good or
bad, and tonal collapse remains `INSUFFICIENT_EVIDENCE` until the existing
tonality rules have enough support.

## Safety

- `MODEL/API CALLS = 0`
- `MUSICAL WRITES = 0`
- Ableton `.als` bytes unchanged
- the general State Trust resolver remains fail-closed
