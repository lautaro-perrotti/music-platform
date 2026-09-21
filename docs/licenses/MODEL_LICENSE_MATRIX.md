# Model / Dependency License Matrix

Code license ≠ model license ≠ commercial right to outputs or datasets.

| project | repository | component | code_license | weights_license | dataset/license notes | commercial_use | redistribution | modification | API terms | risk | decision | source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ableton-mcp | github.com/jpoindexter/ableton-mcp | Remote Script + MCP | MIT | n/a | n/a | yes | yes | yes | n/a | protocol gaps | WRAP | clone LICENSE + source 2026-09-13 |
| AbletonOSC | github.com/ideoforms/AbletonOSC | Remote Script OSC | MIT | n/a | n/a | yes | yes | yes | n/a | UDP | WRAP | clone LICENSE.md |
| ahujasid ableton-mcp | github.com/ahujasid/ableton-mcp | MCP + telemetry | MIT | n/a | telemetry ToS in TERMS.md | yes, with telemetry policy | yes | yes | telemetry | privacy | REJECT | clone README/TERMS |
| pyloudnorm | pypi/github csteinmetz | LUFS | MIT | n/a | ITU algorithm | yes | yes | yes | n/a | low | WRAP | installed 0.2.0 |
| numpy / scipy | — | DSP | BSD | n/a | n/a | yes | yes | yes | n/a | low | REUSE_AS_IS | installed |
| Essentia | github.com/MTG/essentia | MIR library | AGPL-3.0 | TF models often research | proprietary license sold separately | not as closed in-process | AGPL rules | AGPL rules | n/a | copyleft | REJECT | GitHub/PyPI AGPL-3.0-only |
| MERT | github.com/yizhilll/MERT | code | Apache-2.0 | n/a | n/a | code yes | yes | yes | n/a | — | REFERENCE_ONLY | GitHub |
| MERT weights | huggingface.co/m-a-p/MERT-v1-330M | checkpoints | n/a | CC-BY-NC-4.0 | training data not fully open | no | NC | NC | n/a | NC | REJECT | HF card |
| MERT-v2-30s | huggingface.co/m-a-p/MERT-v2-30s | checkpoints | n/a | CC-BY-NC-4.0 | — | no | NC | NC | n/a | NC | REJECT | HF card |
| OpenL3 | github.com/torchcreatives/openl3 (upstream) | embeddings | MIT | CC-BY-4.0 | AudioSet-ish | yes with attribution | yes with BY | yes | n/a | older | WRAP | web license notes |
| LAION CLAP HTSAT unfused | huggingface.co/laion/clap-htsat-unfused | embeddings | Apache-2.0 integration | Apache-2.0 (model card) | LAION data caveats; default revision 8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a | yes, subject to card/terms | yes | yes | n/a | checkpoint/runtime size | WRAP | HF model card checked 2026-09-21 |
| MuQ / MuQ-MuLan | github.com/tencent-ailab/MuQ | code + weights | MIT code | CC-BY-NC-4.0 | MSD | weights no | NC | NC | n/a | NC | REJECT | HF + LICENSE_weights |
| Basic Pitch | github.com/spotify/basic-pitch | AMT | Apache-2.0 | Apache-2.0 (in-repo) | Spotify paper data | yes | yes | yes | n/a | quality limits | WRAP | GitHub LICENSE |
| Qwen3-ASR | github.com/QwenLM/Qwen3-ASR | code | Apache-2.0 | Apache-2.0 | Qwen data | yes | yes | yes | n/a | VRAM | WRAP | HF cards apache-2.0 |
| Demucs (Meta) | github.com/facebookresearch/demucs | archived | MIT | MIT models | MusDB + extra | yes | yes | yes | n/a | unmaintained | REJECT | archived 2025-01-01 |
| Demucs (Défossez) | github.com/adefossez/demucs | maintained fork | MIT | MIT | same family | yes | yes | yes | n/a | 6 GB VRAM | WRAP | GitHub README 2026 note |
| audio-separator | pypi audio-separator | UVR/Demucs wrapper | MIT | **per-model** | UVR models vary | only if model allows | per model | per model | n/a | model mix | WRAP with pin | PyPI MIT + UVR note |
| ACE-Step 1.5 | github.com/ace-step/ACE-Step-1.5 | code | MIT | n/a | authors claim licensed/RF/synthetic | code yes | yes | yes | n/a | disclaimer on style copy | WRAP | raw LICENSE fetch |
| ACE-Step v15-base | huggingface.co/ACE-Step/acestep-v15-base | weights | n/a | MIT (card) | claimed licensed data | claimed yes for outputs | MIT | MIT | n/a | unaudited data | WRAP | HF card 2026-09-13 |
| ACE-Step 1.0 | github.com/ace-step/ACE-Step | code | Apache-2.0 | check v1 cards | — | check each | check | check | n/a | superseded | REFERENCE_ONLY | GitHub |
| Stable Audio Open 1.0 | huggingface.co/stabilityai/stable-audio-open-1.0 | weights | Stability license | NC unless membership | Stability AUP | no by default | limited | limited | no hosted API | NC | REJECT | HF LICENSE |
| Stable Audio 3 Medium | huggingface.co/stabilityai/stable-audio-3-medium | weights | Community License | commercial < $1M | registration required | conditional | conditional | yes | enterprise above cap | revenue cliff | REFERENCE_ONLY | HF LICENSE.md |
| Suno | n/a official public API | cloud | n/a | n/a | lawsuits / ToS | unofficial APIs unsafe | n/a | n/a | no official API | legal + ToS | REJECT | 2026 API reports |
| Google Lyria | Gemini / Vertex | cloud | n/a | n/a | Google claims licensed; SynthID | per Google ToS | n/a | n/a | Gemini/Vertex terms | ToS + watermark | WRAP later | Google docs |
| MidiTok | github.com/Natooz/MidiTok | tokenizer | MIT | n/a | n/a | yes | yes | yes | n/a | low | WRAP later | GitHub |
| MIDI-RWKV | github.com/christianazinn/MIDI-RWKV | model | MIT | check pth | GigaMIDI CC-BY-NC-4.0 | likely no | NC data | NC data | n/a | NC pretrain | REJECT | paper + repo |
| Text2MIDI | github.com/AMAAI-Lab/text2midi | model | MIT | Apache-2.0 card | MidiCaps / Lakh CC-BY-4.0 | likely yes | yes | yes | n/a | research | REFERENCE_ONLY | GH + HF |
| Matchering | github.com/sergree/matchering | mastering | GPL-3.0 | n/a | n/a | GPL obligations if combined | GPL | GPL | n/a | copyleft | REJECT | GH LICENSE |
| Diff-MST | github.com/sai-soum/Diff-MST | research mix | CC-BY-NC-SA-4.0 | same | — | no | SA | SA | n/a | NC-SA | REJECT | repo README |
