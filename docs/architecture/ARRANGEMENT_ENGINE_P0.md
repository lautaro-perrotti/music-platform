# Arrangement Engine P0

`ARRANGEMENT_ENGINE_P0` converts validated producer intent into an auditable,
DAW-free decision graph. It is not an audio analyzer and it does not write to
Ableton.

The flow is:

```text
TrackSpec sections
  -> timeline
  -> energy trend
  -> role additions/removals
  -> structural contrast
  -> tension/release intent
  -> groove-focused role labels
  -> existing MusicPlan actions
```

The engine distinguishes plan intent from measurement:

- `energy` is a producer-declared target, not measured loudness;
- `contrast` is a role-set delta, not an audio similarity score;
- `groove_focus` labels active roles and does not claim that the audio grooves;
- section boundaries come from the plan, not an assumed 32-bar window;
- no result from this module authorizes a DAW write.

Execution remains:

```text
MusicPlan -> ProductionCompiler -> SafeWrite -> Ableton -> readback
```
