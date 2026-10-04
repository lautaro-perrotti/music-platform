# Copilot Audio Tap — build / provision

Repeatable rule:

```
devices/Copilot Audio Tap.maxpat (preserve Cycling '74 patcher dialect)
  → plugin~ 1 2             (both Live audio input channels)
  → devices/build_amxd.py            (ampf/meta/ptch wrapper)
  → devices/Copilot Audio Tap.amxd
  → SHA256
  → ensure_m4l_runtime / User Library copy
  → Live insert via browser URI
  → READ_DEVICE_PARAMETERS
```

## Commands

```
python -c "from copilot.audio.live_capture import rebuild_audio_tap_device; print(rebuild_audio_tap_device())"
```

Do not rerun `extend_tap_slots_v2.py` for normal builds: it starts from a
historical HEAD container and would overwrite the stereo fix. Do not
`json.dumps` the patcher. Live does not enumerate `live.numbox` parameters
from a standard-JSON rewrite of this homemade `.amxd`.

The checked-in patcher uses the portable `__COPILOT_CAPTURE_DIR__` token. The
runtime replaces that token with the discovered host capture directory while
provisioning the User Library device. The repository must not contain a
developer-specific Windows or macOS capture path.

## Verification

Repo `devices/Copilot Audio Tap.amxd` and User Library
`.../Copilot/Copilot Audio Tap.amxd` must share SHA256 after provision.

Live compiles Max devices by browser URI. Overwriting the same filename
does not update an already-compiled broken URI. Provision also writes
identical bytes to `Copilot Audio Tap 5.amxd` (new URI). Core prefers
that alias when inserting. Instantiated devices in a set do not pick up
new bytes in place.

Do not `json.dumps` the patcher. Do not use Max GUI unless a Live defect
remains after this pipeline. This is not a Max project build system.
