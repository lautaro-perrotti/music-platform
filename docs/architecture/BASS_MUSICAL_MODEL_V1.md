# BASS_MUSICAL_MODEL_V1

This milestone turns the reconciled bass evidence into a deterministic,
read-only symbolic model. It does not generate music, infer aesthetic quality,
call Astra, or write to Ableton.

## Input and output

```text
MusicalUnderstanding
    ↓
pitch material
interval language
rhythmic cells
motif repetition / structural variation
phrase model
tonal hypotheses
bass ↔ drums relationship
    ↓
BassMusicalModel
```

The model keeps facts separate from hypotheses. Pitch classes, interval counts,
onset cells, phrase event counts, and structural repetition are measurements or
deterministic derivations. Tonality remains ranked evidence and may remain
`INSUFFICIENT_EVIDENCE`; the model never forces a key or mode from bass alone.

Motif labels are deliberately structural:

- `FIRST_OBSERVED`
- `EXACT_REPEAT`
- `PITCH_VARIANT`
- `UNIQUE_OR_TRANSFORMED`

They do not claim musical quality, intention, or aesthetic similarity.

## Real Rose Bass result

The model was built from the reconciled artifact without recapture:

- 73 authoritative MIDI events;
- pitch material: `A`, `B`, `C#`, `E`, `F#`, `G#`;
- 72 intervals: 29 up, 24 down, 19 repeated-note transitions;
- stepwise ratio `0.542`, leap ratio `0.361`;
- two rhythmic cells, with the first occurring in phrases 1 and 2;
- two structural motifs: the first repeats in phrases 1 and 2, while phrase 4
  is a separate transformed/unique observation;
- phrase 3 has no events and remains `UNKNOWN`;
- tonal selection remains `INSUFFICIENT_EVIDENCE`.

The absence of a selected tonal hypothesis is correct. The bass material is
described exactly without pretending that a bass-only distribution proves a
harmonic function or mode.

## Safety

- `MODEL/API CALLS = 0`
- `MUSICAL WRITES = 0`
- input artifact is immutable/read-only;
- output contains no raw audio;
- no planner, compiler, SafeWrite, or Ableton path is invoked.
