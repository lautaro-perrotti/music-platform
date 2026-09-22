# Musical intelligence audit V1

This is an evidence map, not a quality claim. The rejected Alpha remains the
baseline and `USER_JUDGMENT=REJECTED` is recorded without inferring a reason.

| Capability | Status | Current evidence / limitation |
|---|---|---|
| Music Analyzer / factual DSP | `IMPLEMENTED_VERIFIED` | Persisted `MusicAnalysisPack`; facts remain separate from judgments. |
| CLAP | `IMPLEMENTED_VERIFIED` | Real local `laion/clap-htsat-unfused` embedding observation in Alpha. Similarity is not semantic language. |
| Music Flamingo / semantic ear | `PROVIDER_UNAVAILABLE` | No adapter, weights, runner, cache, or real semantic observation. |
| Source separation | `PROVIDER_UNAVAILABLE` | A model slot exists, but no verified stem inference or artifacts. |
| Multi-reference / role binding | `IMPLEMENTED_PARTIAL` | Typed bindings exist; no multi-reference quality run. |
| Sample library intelligence | `IMPLEMENTED_VERIFIED` | Catalog, DSP descriptors, retrieval, and provenance exist. |
| Sample-in-context selection | `DEFERRED_BY_DESIGN` | No alternate audition provider or rendered candidate. |
| Candidate generation/search | `MISSING` | Core accepts real Lucas alternatives and rejects synthetic candidates; provider currently rate-limited. |
| Candidate comparison / blind A-B | `CONTRACT_ONLY` | Human-evaluation infrastructure exists; no candidate pair rendered. |
| Long-range reasoning | `IMPLEMENTED_PARTIAL` | Section/energy/role trajectory is persisted; motif and audible development remain unverified. |
| Musical issue taxonomy | `IMPLEMENTED_PARTIAL` | Typed feedback categories exist; Alpha reason remains unknown. |
| Provider registry / health / failover | `IMPLEMENTED_PARTIAL` | Capability registry and bounded health semantics exist; critique provider returned rate limit. |
| Preference learning | `IMPLEMENTED_PARTIAL` | One scoped rejection only; no global taste inference. |
| Human benchmark | `CONTRACT_ONLY` | Blind evaluation tools exist; no new labels. |
| MIDI/composition | `DEFERRED_BY_DESIGN` | Lucas-owned musical generation remains the source of intent. |
| Groove / transitions | `IMPLEMENTED_PARTIAL` | Measurement and action contracts exist; no improved render. |
| Sound design search | `MISSING` | No verified search/evaluation loop. |
| Mix/master intelligence | `IMPLEMENTED_PARTIAL` | Safe execution foundation exists; musical comparison is pending. |
| Vocals intelligence | `MISSING` | No verified vocal loop. |

## Current external boundary

The configured reasoning provider was probed once with a cheap request and
returned `MODEL_RATE_LIMITED`. No alternate authorized endpoint was present.
The system therefore remains fail-closed: no fake semantic observations, no
synthetic Lucas candidates, and no Ableton rendering.

## Core translation invariant

The Core adapter now removes executable actions for roles omitted by Lucas when
the model supplies selections/arrangement metadata. This prevents the broad
deterministic recipe from resurrecting roles such as Guitar or Sax. Lucas-owned
files remain untouched.

