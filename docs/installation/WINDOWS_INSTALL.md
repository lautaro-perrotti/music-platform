# Windows install — SECOND_MACHINE_INSTALLER_V1

Windows only. macOS: `docs/installation/MAC_INSTALL.md`. No MSI and no GUI installer in V1.

A fresh machine with Ableton Live already installed:

```powershell
git clone <repo>
cd music-platform
powershell -ExecutionPolicy Bypass -File .\scripts\install-copilot.ps1
```

`install.bat` in the repo root only launches that script.

After a successful install, use the environment Python:

```powershell
.\.venv\Scripts\python.exe -m copilot.cli doctor
```

or:

```powershell
.\.venv\Scripts\Activate.ps1
python -m copilot.cli doctor
```

## What the installer does

1. Discovers Windows, architecture, Python, Ableton Live, preferences, Documents, and User Library.
2. Reuses Python 3.12+ when present. If missing, it may install **only** `Python.Python.3.12` via winget when winget is available. Otherwise it stops with `PYTHON_INSTALL_REQUIRED` and the exact winget command.
3. Creates `<repo>\.venv` idempotently and installs the project from `pyproject.toml`. It does not use global pip as the runtime.
4. Installs the Copilot-owned Ableton Remote Script (`AbletonMCP`) into the resolved User Library `Remote Scripts` folder (or the detected User Remote Scripts folder if User Library cannot be resolved). Hash-checked, atomic replace of Copilot-owned files, never overwrites unknown files.
5. Provisions Copilot Audio Tap (`TapProtocol = 3`) through existing `M4L_RUNTIME_PROVISIONING_V1`.
6. Creates `.env` from `config/copilot.env.example` only if `.env` is missing. It never copies secrets from another machine and never overwrites an existing `.env`.

Missing Astra credentials report `MODEL_CREDENTIALS_REQUIRED`. That does **not** block local runtime install.

## Ableton Control Surface

The Remote Script is a Live Control Surface. Copying files is not enough for TCP `127.0.0.1:9877`.

The listener starts only when Ableton instantiates `AbletonMCP` (`create_instance` → `ControlSurface.__init__`). User Library placement only makes **AbletonMCP** appear in the Control Surface list. V1 does not click Live preferences.

If the installer reports `ABLETON_CONTROL_SURFACE_CONFIGURATION_REQUIRED`:

1. Start Ableton Live.
2. If Live was open during install, quit and reopen it.
3. Settings → Link, Tempo & MIDI → Control Surface = **AbletonMCP**.
4. Input = None, Output = None.
5. `.\.venv\Scripts\python.exe -m copilot.cli doctor`

Live is ready only after `LIVE_SESSION_READINESS_V1` (`SESSION_READY`). A listening port is not ready.

## Credentials

Set a User environment variable (preferred) or fill `.env`:

- `COPILOT_REASONING_API_KEY` (or `OPENAI_API_KEY`)
- optional `COPILOT_REASONING_MODEL=gpt-6-astra`
- optional `COPILOT_REASONING_BASE_URL=https://api.openai.com/v1`

Do not commit `.env`.

## Idempotency

Run the installer a second time. Expected:

- Python environment = `ALREADY_CURRENT`
- dependencies = `ALREADY_CURRENT`
- Remote Script = `ALREADY_CURRENT`
- M4L runtime = `ALREADY_CURRENT`
- config = `PRESERVED`

No duplicate Remote Scripts, no duplicate M4L devices, no overwritten credentials.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall-copilot.ps1
```

Optional:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall-copilot.ps1 -RemoveVenv
```

`-RemoveConfig` deletes `.env` only when you pass that switch.

Uninstall removes only Copilot-owned Remote Script files and Copilot-owned M4L assets. It does not remove user projects, Ableton preferences, or unmanaged User Library content.

## Friend machine

First run on a second Windows computer is read-only:

```powershell
python -m copilot.cli doctor
python -m copilot.cli import-project "<Ableton project folder>"
```

No musical writes. `SECOND_MACHINE_PORTABILITY_V1` stays `PENDING_FRIEND_MACHINE` until that machine is actually used.
