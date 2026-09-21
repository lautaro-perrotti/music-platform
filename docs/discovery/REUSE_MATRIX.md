# Reuse Matrix

Decisions are exclusive. Status is evidence-based.

| Capability | Candidate | Status | Reuse | Fork | Wrapper | Build | License | Risk | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ableton LOM transport | jpoindexter Remote Script protocol | PARTIALLY_VERIFIED | No | No | Yes | Adapter only | MIT | Index IDs, no audio tap, Live untested | WRAP |
| Ableton MCP server as agent | jpoindexter FastMCP | PARTIALLY_VERIFIED | No | No | No | Our typed tools | MIT | 128 tools ≠ 200; stringly tools | REJECT |
| Ableton REST | jpoindexter FastAPI | PARTIALLY_VERIFIED | No | No | Optional later | — | MIT | Extra surface | REFERENCE_ONLY |
| Ableton M4L chat | jpoindexter M4L | DOCUMENTED_ONLY | No | No | No | — | MIT | Cloud keys in Live | REJECT |
| Ableton OSC fallback | AbletonOSC | PARTIALLY_VERIFIED | No | No | Yes later | OSC backend | MIT | UDP, index IDs | WRAP |
| Ableton MCP original | ahujasid/ableton-mcp | DOCUMENTED_ONLY | No | No | No | — | MIT | Telemetry | REJECT |
| Generic LOM RPC | codex-live-bridge | DOCUMENTED_ONLY | No | No | No | — | unknown here | Arbitrary API | REJECT |
| SessionState / IDs | none adequate | VERIFIED (ours) | No | No | No | Yes | MIT (ours) | — | BUILD |
| Agent transactions | Live undo | PARTIALLY_VERIFIED | No | No | No | Yes | — | Global undo | BUILD |
| LUFS/RMS/peak | pyloudnorm + numpy | VERIFIED | Yes | No | Yes | Feature set | MIT | No true-peak yet | WRAP |
| Full MIR suite | Essentia | DOCUMENTED_ONLY | No | No | Separate process only | Prefer DSP | AGPL-3.0 | Copyleft | REJECT |
| Music embeddings | LAION CLAP HTSAT unfused | VERIFIED (provider) | No | No | Yes | Transformers boundary | Apache-2.0 model card | Pin revision per release | WRAP |
| Music embeddings | OpenL3 | DOCUMENTED_ONLY | No | No | Yes | — | MIT + CC-BY-4.0 | Older | WRAP |
| Music embeddings | MERT / MuQ | DOCUMENTED_ONLY | No | No | No | — | CC-BY-NC-4.0 | Non-commercial weights | REJECT |
| Transcription | Basic Pitch | DOCUMENTED_ONLY | No | No | Yes | — | Apache-2.0 | Weak velocity; mixture errors | WRAP |
| ASR / singing | Qwen3-ASR-0.6B | DOCUMENTED_ONLY | No | No | Yes | — | Apache-2.0 | 6 GB VRAM tight | WRAP |
| Stems | adefossez/demucs | DOCUMENTED_ONLY | No | No | Yes | Interface | MIT | Meta repo archived; VRAM | WRAP |
| Local music gen | ACE-Step 1.5 base | DOCUMENTED_ONLY | No | No | Yes | Provider iface | MIT code+card | Dataset unaudited; disk | WRAP |
| Local music gen XL | ACE-Step 1.5 XL | DOCUMENTED_ONLY | No | No | No | — | MIT claimed | ≥12 GB VRAM | REJECT |
| Cloud music gen | Suno official API | DOCUMENTED_ONLY | No | No | No | — | no public API | Unofficial wrappers | REJECT |
| Cloud music gen | Google Lyria | DOCUMENTED_ONLY | No | No | Yes later | Provider iface | Google API terms | Watermark, ToS churn | WRAP |
| Sound design | Stable Audio Open 1.0 | DOCUMENTED_ONLY | No | No | No | — | NC + no hosted API | License | REJECT |
| Sound design | Stable Audio 3 community | DOCUMENTED_ONLY | No | No | Gate later | — | Community / $1M cap | Revenue cliff | REFERENCE_ONLY |
| Symbolic tokenize | MidiTok | DOCUMENTED_ONLY | Yes later | No | Yes | — | MIT | Not a generator | WRAP |
| MIDI LM | MIDI-RWKV | DOCUMENTED_ONLY | No | No | No | — | MIT code; NC data | GigaMIDI NC | REJECT |
| Text-to-MIDI | Text2MIDI | DOCUMENTED_ONLY | No | No | Later | MusicPlan first | MIT / Apache | Research quality | REFERENCE_ONLY |
| Reference master | Matchering | DOCUMENTED_ONLY | No | No | Isolated later | Measure/plan | GPL-3.0 | Copyleft | REJECT |
| Mix style transfer | Diff-MST | DOCUMENTED_ONLY | No | No | No | — | CC-BY-NC-SA | Research + NC | REJECT |
| Conversation LLM | OpenAI-compatible slot | DOCUMENTED_ONLY | No | No | Yes | ModelRouter | vendor ToS | Swappable | WRAP |
| Audio capture from Live | none found | BLOCKED_BY_ENVIRONMENT | No | No | No | Yes | — | Bridges cannot bounce | BUILD |
| Project memory | none selected | — | No | No | Later | Schema first | — | No vector DB yet | BUILD |
| Agent core / policy | none adequate | VERIFIED (slice) | No | No | No | Yes | MIT (ours) | — | BUILD |
