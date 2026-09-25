# Serum2 preset-first P0

Serum2 production starts from a catalogued preset, not an invented init patch.
The catalog records role compatibility, tags, optional macro targets and
provenance. Selection is deterministic and returns a shortlist rather than
performing a DAW mutation.

```text
TrackSpec / producer role
  -> PresetCatalog.select
  -> selected preset URI + provenance
  -> Core intent adapter
  -> canonical MusicPlan action
  -> ProductionCompiler
  -> SafeWrite
```

No role-compatible preset means `UNAVAILABLE`; the selector never silently
substitutes a lead for a bass or invents a patch. `writes_authorized` remains
zero at selection time.
