# macOS install — SECOND_MACHINE_INSTALLER_V1

The PowerShell installer is Windows-only. On a Mac with Ableton Live already installed:

```bash
git clone <repo>
cd music-platform
chmod +x scripts/install-copilot.sh
./scripts/install-copilot.sh
```

Do **not** copy `.env` from another machine. Git ignores it. The installer creates a local empty template.

After install:

```bash
.venv/bin/python -m copilot.cli doctor
```

or:

```bash
source .venv/bin/activate
python -m copilot.cli doctor
```

## Python

Target: Python 3.12+.

If missing and Homebrew is available, the script may run `brew install python@3.12`.
Otherwise it stops with `PYTHON_INSTALL_REQUIRED` and that exact command.

## Ableton paths used (not guessed when Library.cfg exists)

- App: `/Applications/Ableton Live *.app`
- Preferences: `~/Library/Preferences/Ableton/Live *`
- User Library default: `~/Music/Ableton/User Library`

Remote Script destination is Copilot-owned `User Library/Remote Scripts/AbletonMCP`.
M4L device: `Presets/Audio Effects/Max Audio Effect/Copilot/Copilot Audio Tap.amxd`.

## Control Surface

Same as Windows: copying files does not start TCP. After install:

1. Start Ableton Live (quit/reopen if it was already running).
2. Settings → Link, Tempo & MIDI → Control Surface = **AbletonMCP**.
3. Input = None, Output = None.
4. `.venv/bin/python -m copilot.cli doctor`

Live is ready only after `SESSION_READY`.

## Astra key

Set `COPILOT_REASONING_API_KEY` in the Mac `.env` created by the installer, or export it in the shell. Do not commit it.

First `import-project` on this machine is read-only. No musical writes.
