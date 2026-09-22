# STEM_REVIEW_LAB_V1

`python -m copilot.review.stem_review_lab` launches the local blind review
utility for the immutable stem benchmark. Set `STEM_BENCHMARK_ROOT` or use
`--root` to point at the controlled benchmark directory; no developer machine
path is embedded in the package.

The listener-facing API exposes only opaque candidate IDs. Raw audio is served
from paths allowlisted by the benchmark manifest, while loudness-matched audio
comes from the existing benchmark blind bundle. The bundle's deterministic
constant-gain derivative is never written over a raw candidate. Factual
measurements are persisted under `reviews/loudness_analysis.json`, and review
state is persisted under `reviews/review_session.json`.

V1 intentionally implements solo audition only. A fixed context mix is not
defensible yet, so the UI labels `IN CONTEXT` as unavailable instead of
creating a separator-dependent comparison.

Model identity is not included in the public state, HTML, audio URLs, or
normal review payload. `REVEAL MODELS` is available only after all required
roles (drums, bass, other) have complete human ratings and an explicit blind
finalization. The revealed `final_selection.json` contains the private join,
technical metadata, and raw asset lineage.

This tool is read-only with respect to Ableton and the benchmark's raw audio:
`ABLETON_IMPORT=HOLD` and `MUSICAL_WRITES=0` are explicit terminal state.
