# PHYSICAL_DSP_V2

Status: implemented as a **factual measurement layer**. It MEASURES. It does
not decide if music is good or bad. Music Flamingo, CLAP, mix, master, and
Astra are out of scope.

Public call:

```python
from copilot.audio.physical_dsp_v2 import analyze_path, analyze_pair
from copilot.schemas.dsp import DspObservation

bundle = analyze_path("source.wav", tempo_bpm=126.0, start_qn=36.0, end_qn=68.0)
pair = analyze_pair("kick.wav", "bass.wav")
items = bundle.observations[0].to_evidence_items()
```

Producer Runtime may request capability id `PHYSICAL_DSP_V2` later. The
AnalyzeProject capture graph is unchanged.

## Contract

Every analyzer returns `DspObservation` (`copilot.schemas.dsp`):

- observation id (deterministic cache key)
- analyzer id / version `2.0.0`
- subject (source / pair / mix)
- time span + granularity
- typed `values[]` with units and optional confidence
- quality `OK | LIMITED | UNSUPPORTED`
- limitations
- provenance + source artifact hash

No anonymous per-analyzer dict is the public API.

## Providers

| Library | Role | Missing / blocked |
|---|---|---|
| numpy / scipy | frames, STFT, true-peak 4x, chroma | fail-closed `UNSUPPORTED` |
| pyloudnorm | integrated LUFS | fail-closed `PYLOUDNORM_UNAVAILABLE` |
| soundfile | decode | fail-closed |
| librosa | not installed | `LIBROSA_NOT_INSTALLED` |
| Essentia | AGPL, never imported | `ESSENTIA_LICENSE_BLOCKED` |

Canonical wrap (not a rewrite):

- `compute_fullmix_observation` → `physical-dsp-v2.fullmix-wrap`
- `compute_lowend_features` / `detect_transients` / `spectral_overlap_over_time` / `overlap_at_attacks`

## Cache

`sha256(audio_hash + analyzer_id + analyzer_version + params)`. Path/name is
never the key.

## Unsupported measurements (limitations, not fake numbers)

- Official ITU loudness range (`ITU_LRA_NOT_IMPLEMENTED`)
- Certified ITU true-peak meter (`TRUE_PEAK_4X_POLYPHASE_APPROXIMATION`)
- Essentia / librosa extractors
- One certain musical key (`KEY_IS_CANDIDATE_SET_NOT_CERTAIN`)
- Musical syncopation score (`SYNCOPATION_IS_ONSET_PHASE_VS_GRID`)
- Complex M/S STFT width (`FREQ_WIDTH_MAGNITUDE_MS_APPROXIMATION`)
- Cross-source sample alignment (pair compare uses min length)

## Granularity

`TRACK | REGION | BAR | BEAT | EVENT | SOURCE | SOURCE_PAIR`.
Bar/beat use supplied tempo / qn. Live is not required for synthetic tests.
Missing tempo → `ABLETON_TIMING_NOT_PROVIDED`.

## Evidence

`DspObservation.to_evidence_item()` / `to_evidence_items()` map onto existing
`EvidenceItem` fields. EvidenceGraph consumption is Agent B.

## Performance

Synthetic CPU/wall/cache baseline:
`docs/architecture/baselines/physical_dsp_v2.json`. No invented Live capture
numbers.
