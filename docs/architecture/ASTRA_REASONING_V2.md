# ASTRA_REASONING_V2

Canonical evidence-native diagnosis. Astra interprets; it is never measurement
authority.

## Flow

```
EvidenceGraph
    → EvidenceRequirement (runtime decides how to acquire)
    → EvidenceView (scoped)
    → Astra (reason over view + typed observations + contradictions + limitations)
    → grounding validator (outside the model)
    → Grounded MusicDiagnosis
    → minimal next EvidenceRequest if INSUFFICIENT_EVIDENCE
```

`reason(pack, provider, view=None)` remains the public function. Pack-only
callers get a view adapter (`scoped_from_pack`). Producer Runtime already
passes `graph.view()` into `ASTRA_REASONING` after `EVIDENCE_FUSION`.

Do not copy capture, DSP, fusion, or Safe Write into a second pipeline.

## Input

Astra receives a scoped EvidenceView: support, counterevidence, limitations,
provenance, freshness, subject identities, regions, confidence components,
fusion rows.

It does not receive arbitrary logs, repository state, parent/child pack
navigation, or unrelated project history. `evidence_pack_v1` remains readable
via `graph_from_pack` / `scoped_from_pack`.

Prompt version: `reason-evidence-view-2`.

## Diagnosis

`MusicDiagnosis` / `GroundedHypothesis` are extended in place:

- diagnosis: question, scope, project identity, region, status, confidence,
  limitations, hypotheses, candidate strategies, requested evidence
- hypothesis: claim, supporting refs, contradicting refs, missing evidence,
  limitations, confidence, status

Statuses are unchanged and must not be collapsed:

`SUPPORTED` | `WEAKLY_SUPPORTED` | `INSUFFICIENT_EVIDENCE` |
`NO_ACTION_REQUIRED` | `DIAGNOSIS_UNSTABLE`

`INSUFFICIENT_EVIDENCE ≠ NO_ACTION_REQUIRED`.
`DIAGNOSIS_UNSTABLE ≠ INSUFFICIENT_EVIDENCE`.

## Fact / reasoning boundary

Allowed: interpret measured energy vs MIDI as compatible with later
attenuation.

Not allowed without evidence: “the compressor is suppressing the synth by 6 dB.”

Quantitative statements must map to EvidenceRefs that contain those values.

## DSP V2

Consume LEVEL/DYNAMICS, SPECTRAL, TRANSIENT, STEREO, RHYTHM/TONAL/TIMBRE facts,
RELATIONAL DSP. `pdsp.*` from `DspObservation.to_evidence_item()` is
`MEASUREMENT` (relational = `RELATIONSHIP`).

- `POTENTIAL_OVERLAP` → Astra may say potential masking is plausible
- not “muddy” / “masking problem” as a measurement
- not an automatic “apply -3 dB at 300 Hz”
- `CAPTURE_FAILED` is not measured silence
- `KEY_IS_CANDIDATE_SET_NOT_CERTAIN` means no single key is certain
- non-canonical codes (`ITU_LRA_NOT_IMPLEMENTED`, …) propagate

## Confidence

Typed components only: measurement quality, provider confidence, source
reliability, cross-source agreement, contradictions, missing evidence,
limitations. Qualitative `HIGH` / `MEDIUM` / `LOW`. `combined` is always null.

## Contradictions

Fusion `CONTRADICT` cannot be resolved by picking one side as fact.
`PARTIALLY_AGREE` / `NOT_COMPARABLE` stay visible. HIGH confidence cannot
ignore strong contradictory evidence.

## Grounding (outside the model)

Validator checks: EvidenceRef exists; subject identity; region/time;
quoted numbers live in cited evidence; units; stale evidence is not current
fact; limitations are not dropped.

Failure → `DIAGNOSIS_UNSTABLE`. Do not retry the model until it says the
desired answer. Schema retries remain JSON/schema only (2 attempts).

Internal claim classes: `FACTUAL_REFERENCE`, `INTERPRETATION`, `HYPOTHESIS`,
`RECOMMENDATION`. Recommendations must not masquerade as measurements.

Causal language (not Deep Causal): `COINCIDES_WITH`, `COMPATIBLE_WITH`,
`SUGGESTS`, `WEAKLY_SUPPORTS`, `STRONGLY_SUPPORTS`, `CAUSE_UNRESOLVED`.
Temporal coincidence is not causation.

## Next evidence / strategies

When `INSUFFICIENT_EVIDENCE`, request the smallest useful `EvidenceRequest`
(goal, subject/`target`, region, reason/`why_needed`, required kinds, priority).

Never emit capture/CLI/Python commands. Candidate strategies are high-level
musical ideas, not executable writes and not ungrounded parameter values.

Producer Runtime decides how to acquire evidence. AnalyzeProject musical
writes stay 0. `SET_TRACK_VOLUME` remains the only certified musical action;
this milestone does not add writes.

## Provider failure

Timeout, 429, network, schema/truncated JSON, invalid refs, grounding failure:
no musical verdict from an invalid response.

## Performance

Spans: EvidenceView construction, serialization, provider request, provider
wait, parse, grounding, post-process (`CAT_CPU` / `CAT_MODEL`). Prompt bytes
and approximate input tokens are recorded. Do not optimize intelligence away
by dropping relevant evidence.

## Not this milestone

`DEEP_CAUSAL_DIAGNOSIS_V2`, Music Flamingo, CLAP, mix, master.
