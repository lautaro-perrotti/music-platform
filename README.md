# AI Music Production Copilot

Reuse-first Ableton copilot: SessionState, typed DAW tools, agent transactions, and a measure → plan → modify → verify loop.

PRE-LIVE hardening is `VERIFIED`. LIVE-1 MIDI is `VERIFIED` on Ableton Live 12.4.5 Trial. Production capture (TapProtocol 3, Main + 2 source hosts, batching) is `VERIFIED / FROZEN` on the Groove Rider working copy. Musical generalization to a second real song is **unproven**.

Constitution: `AGENTS.md`, `docs/CURRENT_STATE.md`, `docs/ROADMAP_100.md`.

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-copilot.ps1
.\.venv\Scripts\python.exe -m copilot.cli doctor
```

macOS:

```bash
chmod +x scripts/install-copilot.sh
./scripts/install-copilot.sh
.venv/bin/python -m copilot.cli doctor
```

Do not copy `.env` between machines. See `docs/installation/WINDOWS_INSTALL.md` and `docs/installation/MAC_INSTALL.md`.

Agent-facing analysis is one call:

```bash
python -m copilot.cli analyze-project "<folder>"
```

`producer-analyze` remains a low-level debug command. See `docs/architecture/PRODUCER_RUNTIME_V1.md` and `docs/CURRENT_STATE.md`.

```bash
python -m copilot.cli detect
python -m copilot.cli install-script
python -m copilot.cli probe
python -m copilot.cli slice1
python -m copilot.cli undo
```

See `docs/discovery/LIVE_STATUS.md`.

## Performance and robustness

`python -m copilot.cli performance-report` writes `logs/performance_report_v1.json`
and prints where the seconds go. It is read-only — Ableton reads plus recorded
evidence — so it is safe to run while you are working in Live. Add `--offline` to
skip the live reads.

Architecture audit and measured baselines:

- `docs/architecture/PERFORMANCE_AND_ROBUSTNESS_AUDIT_V1.md`
- `docs/architecture/EDGE_CASE_MATRIX_V1.md`
- `docs/architecture/LUCAS_BRANCH_INTEGRATION_AUDIT_V1.md`
- `logs/performance_baseline_v1.json`, `logs/critical_path_v1.json`,
  `logs/wait_inventory_v1.json`, `logs/runtime_execution_graph_v1.json`
