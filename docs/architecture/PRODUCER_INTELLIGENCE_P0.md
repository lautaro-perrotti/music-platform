# Producer Intelligence P0

This is the first Core-side delta derived from the external review of
AbletonComposer and ableton-live-mcp.  It does not copy either repository and
it does not add a second Ableton write path.

## Integrated ideas

- `TrackSpec` makes musical intent executable as typed planning data: tempo,
  key, sections, energy, active roles, hook, constraints, and reference/mix
  targets.
- Astra may return a `TrackSpec`; Core validates it and converts only its
  sections into the existing DAW-free `Section` value object.
- Producer state is persisted as a compact decision ledger.  It records what
  was observed, decided, attempted, and why the producer stopped.  It is not a
  transaction journal and cannot mutate Ableton.
- The bounded Alpha coordinator now records session observation, accepted plan,
  execution outcome, and typed critique outcome in that ledger.  Provider
  failure ends in `ABSTAINED`; it never becomes an invented musical verdict.
- Dynamic sections are accepted by name and energy.  No fixed Tech House
  labels are required; genre heuristics remain advisory input.

## Deliberately not integrated

- `live_eval` / `live_exec` arbitrary code execution from ableton-live-mcp.
- AbletonComposer's fixed performance scripts and hardcoded sample generators.
- OSC, a second bridge, a second journal, or a second SafeWrite authority.
- Automatic musical writes from a provider response.

## Next bounded block

Use `TrackSpec` plus `ProducerStateStore` in the existing producer loop, then
add one bounded `CREATE -> OBSERVE -> CRITIQUE -> MODIFY -> VERIFY` iteration.
That work must continue through `MusicPlan -> ProductionCompiler -> SafeWrite`
and retain authoritative readback, rollback, and fail-closed semantics.
