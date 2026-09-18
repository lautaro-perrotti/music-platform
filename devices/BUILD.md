# Copilot Audio Tap — build / provision

Repeatable rule:

```
git HEAD V3 .amxd ptch  (Cycling '74 dialect)
  → devices/extend_tap_slots_v2.py   (surgical V4 slot edits only)
  → devices/Copilot Audio Tap.maxpat
  → devices/build_amxd.py            (ampf/meta/ptch wrapper)
  → devices/Copilot Audio Tap.amxd
  → SHA256
  → ensure_m4l_runtime / User Library copy
  → Live insert via browser URI
  → READ_DEVICE_PARAMETERS
```

## Commands

```
python devices/extend_tap_slots_v2.py
python -c "from copilot.audio.live_capture import rebuild_audio_tap_device; print(rebuild_audio_tap_device())"
```

`extend_tap_slots_v2.py` always starts from `HEAD:devices/Copilot Audio Tap.amxd`.
Do not `json.dumps` the patcher. Live does not enumerate `live.numbox`
parameters from a standard-JSON rewrite of this homemade `.amxd`.

## Verification

Repo `devices/Copilot Audio Tap.amxd` and User Library
`.../Copilot/Copilot Audio Tap.amxd` must share SHA256 after provision.

Live compiles Max devices by browser URI. Overwriting the same filename
does not update an already-compiled broken URI. Provision also writes
identical bytes to `Copilot Audio Tap 4.amxd` (new URI). Core prefers
that alias when inserting. Instantiated devices in a set do not pick up
new bytes in place.

Do not `json.dumps` the patcher. Do not use Max GUI unless a Live defect
remains after this pipeline. This is not a Max project build system.
