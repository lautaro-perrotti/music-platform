# HARMONIC_UNDERSTANDING_V1

This milestone adds a deterministic, read-only harmonic evidence layer above
the verified MIDI and bass model artifacts.

```text
authoritative Ableton MIDI
        +
optional OTHER/MUSIC stem chroma
        ↓
4-bar harmonic windows
        ↓
ranked chord hypotheses
        ↓
harmonic rhythm
        ↓
tonal hypotheses
        ↓
bass ↔ harmony relationships
```

The implementation is deliberately conservative:

- the reconciled MIDI is the authoritative bass source;
- the `OTHER` stem contributes measured pitch-class energy only;
- chord labels are ranked deterministic templates, not ground truth;
- a window needs sufficient evidence and separation from alternatives before
  it gets a selected chord;
- global tonality remains `INSUFFICIENT_EVIDENCE` when candidates are too close;
- absence of the harmonic audio source produces `AUTHORITATIVE_BASS_ONLY` and
  no selected chord/tonality;
- separator reconstruction is not treated as perceptual source purity;
- no LLM/API calls, Ableton access, or musical writes occur.

Public entry point:

```python
from copilot.audio.harmonic_understanding_v1 import build_harmonic_understanding

result = build_harmonic_understanding(
    bass_model_path,
    musical_understanding_path,
    other_stem_path=other_wav,
    output_path=output_json,
)
```

The output contains evidence references, source hashes, ranked hypotheses,
harmonic-rhythm segments, bass-role relationships, and explicit limitations.
It is not a planner and does not authorize a production write.
