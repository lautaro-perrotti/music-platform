# Live regression fixtures

Hand-saved Ableton sets. Do not invent sidechain/group/warp links through the Remote Script.

Save each once from Live 12 (File → Save Live Set) into this folder.

| Set | Purpose |
| --- | --- |
| `fixture_master_chain.als` | Bass → Main Utility/Compressor/Limiter → Copilot Audio Tap **last** |
| `fixture_sidechain.als` | Kick sidechain input on Bass Compressor, wired in Live UI |
| `fixture_group_return.als` | Group(Bass, Synth) with group FX + Bass Send A → Reverb Return |
| `fixture_latency.als` | Click → device with lookahead/PDC. Note Delay Compensation and Reduced Latency When Monitoring |
| `fixture_boundary.als` | Region that starts with silence / pad / reverb tail, not a transient |

`python -m copilot.cli live22e` uses the open set if these files are not loaded.
