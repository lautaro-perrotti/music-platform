# AI Music Production Copilot

Reuse-first Ableton copilot: SessionState, typed DAW tools, agent transactions, and a measure → plan → modify → verify loop.

PRE-LIVE hardening is `VERIFIED`. LIVE-1 MIDI (connection/read/write/read-back/transaction/rollback) is `VERIFIED` on Ableton Live 12.4.5 Trial. Audio capture is `NOT_STARTED`.

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

```bash
python -m copilot.cli detect
python -m copilot.cli install-script
python -m copilot.cli probe
python -m copilot.cli slice1
python -m copilot.cli undo
```

See `docs/discovery/LIVE_STATUS.md`.
