# New tech-house project (experimental)

Run only with an explicitly authorized, **saved empty** Ableton template,
an indexed sample library, and a separate destination directory:

```text
python -m copilot.cli produce-tech-house --template "<empty.als>" --sample-index "<index.json>" --sample-root "<library folder>" --workspace "<output folder>" --prompt-file "<super-prompt.txt>"
```

Optionally add `--reference "<licensed-reference.wav>"` for read-only,
aggregate groove/energy/mix comparison. It is not a source of copied
notes, melodies, or stems, and the command never exports a final WAV.

The UTF-8 super prompt must contain exactly one `BPM: 127` and one
`Duración: 64 compases` (alternatively `Duration: 2:00` or
`Duración: 2 minutos 30 segundos` or `Duración: 120 segundos`).
Duration in time is quantized to the nearest
4/4 bar; the maximum deviation is half a bar. Fill in both values before
running. For example:

```text
BPM: 127
Duración: 64 compases
Produce an original tech-house groove. Choose kick, bass, percussion and a
distinctive hook from the indexed samples. Compare two or three candidates
per role and explain the choice and rejections using factual timbre and
transient evidence; do not claim semantic listening without a listener.
Develop kick/bass/percussion first. Describe the foreground, low-end owner
and deliberately unoccupied space for each section, with intentional
transitions and a variation hypothesis. Correct one prioritized problem at a
time and revert changes without measured improvement. Apply EQ Eight only
for a specific measured hypothesis, and Limiter last with a verified ceiling.
```

The supplied longer Spanish super prompt can be used as advisory creative
copy, but its BPM and duration placeholders must be replaced with exactly
one explicit value each. Its requests never authorize arbitrary model code
or override the runtime's measurements and fail-closed gates.

The command validates BPM/duration **before** opening Live, refuses model
fallbacks and unknown sample selections, creates a unique manifest-backed
copy, and only uses MusicPlan -> ProductionCompiler -> SafeWrite for musical
actions. It records its report under `logs/producer/<run-id>/report.json`.
The `.als` is the intended editable deliverable; internal WAV captures are
evidence, never a final export.

**Evidence and decisions:** each indexed candidate gets a reproducible
one-bar preview at matched peak and an equally gained single-bar context
proxy with a fixed Kick (or Bass) reference. Source SHA, type, BPM/pitch
confidence, transient/crest/spectrum and license state are reported along
with chosen and rejected candidates. The previews are **not** full groove
auditions in Live, and descriptors cannot judge musical fit or guarantee
license; if no semantic listening provider exists the report does not say
Astral heard them. A structured `producer_criteria` separates artistic
preferences and perceptual estimates from measured facts, checks section
roles against the actual plan and cites only selected sample hashes.
Section listening captures opening, middle and ending windows on both sides
of transitions; every window must have independent identity, timing,
non-silent, non-clipping evidence. An intentional silent break currently
cannot pass `COMPLETE` without a separate certified intentional-silence
contract.
**Phrase MIDI boundary:** the current compound compiler can create an editable
pattern on a *new* MIDI track, but it cannot atomically replace a phrase on an
existing sample-backed track while preserving its instrument and independently
verifying Arrangement playback/rollback. Repeated copies of slot 0 are not a
new phrase. If a model-specified section requests a MIDI `variation`, the
producer records `PHRASE_MIDI_VARIATION_NOT_CERTIFIED` as an essential
deferred action, saves the verified draft where possible and returns `DRAFT`,
never `COMPLETE`. A planning `variation_hypothesis` alone is not evidence that
a variation was rendered.

EQ Eight APPLY requires a stated `reduce_low_band` or `reduce_high_band`
hypothesis, and Limiter APPLY requires `reduce_peak`. The initial hypothesis
is based on sample descriptors, **not** a claim that the produced Live mix
was already heard. SafeWrite physical
readback and fresh before/after audio are followed by objective spectral or
crest comparison with limited transient loss; an unchanged or worsened
result rolls back. These are factual proxies, **not** proof of artistic
quality. Revisions target the highest-priority critique issue, compare
whole-section windows and stop after at most three attempts; provider
absence remains DRAFT. When comparable mix captures exist, an optional
`blind_review/review.json` contains randomized A/B previews normalized
to −18 dBFS RMS and six human questions; a separate
`answer_key.json` holds capture provenance. The human review is posterior
and does not gate technical completion. `artistic_quality_human_verified`
remains `false` until a human actually submits a review.

Example report fields (illustrative, not a completed Live run):

```json
{
  "status": "DRAFT",
  "MUSICAL_WRITES": 0,
  "sample_comparisons": {},
  "producer_criteria": null,
  "section_captures": {},
  "quality_gate": {
    "status": "DRAFT",
    "artistic_quality_human_verified": false
  },
  "blockers": ["BRIDGE_CAPABILITY_BROWSER_LOAD_UNAVAILABLE"]
}
```

**Current certification boundary:** the template must already have the
requested BPM; a tempo-change MusicPlan action is not certified. The installed
bridge may omit `browser.load` and exposes no verified Save command. Physical
EQ Eight/Limiter units must be reported by the connected bridge with
`device.physical_units_v1`; older installed scripts will defer requested
adjustments. The model may request one EQ Eight adjustment on an active
track and one Main Limiter ceiling adjustment with a typed physical value.
Only a native scale matching Live's own displayed unit permits the existing
MusicPlan → ProductionCompiler → SafeWrite path. Fresh parameter readback,
before/after Main captures and a second verified Save/reopen are required.
No recipe device chain is silently substituted. The blank template may have
empty Return tracks but no musical tracks, clips or Return devices.
A partial Live pass is `DRAFT`, and a failed
preflight is `BLOCKED`; neither is a finished track. No completion or
artistic-quality claim may be inferred from a file existing or from a
successful structural readback alone. A verified Save/reopen, audible
section-by-section and source checks, a bounded typed volume correction loop,
and real Live certification are required before the quality gate can return
`COMPLETE`. Live certification has not been performed on this change.
Arbitrary BPM remains blocked unless the empty saved template already matches
the requested BPM. Real Live proof needs a disposable authorized empty set,
an indexed licensed sample root, an available model provider, and a connected
bridge advertising actual `browser.load` and `device.physical_units_v1` when
those actions are requested. Two distinct prompt/project runs have not been
performed on this host; the offline mocks are not substitutes.
