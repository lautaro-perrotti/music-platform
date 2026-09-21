"""Mix + mastering tweaks (MIXING_V1) — balance + master chain, native devices.

Priority: BALANCE first (static levels), then the master chain (EQ -> Glue ->
Saturator -> Limiter) with a final limiter ceiling. Best-effort: failures are
collected, never fatal, so a broken device name never blocks the track build.
"""

from __future__ import annotations

from typing import Any

from copilot.producer.parameter_registry import (
    get_device_spec,
    limiter_ceiling_db_to_normalized,
    resolve_parameter_index,
    track_volume_db_to_linear,
)
from copilot.producer.soniq_surface import apply_patch, read_vst_schema

# Static balance (dB). Kick is the anchor; everything else sits under it.
# Target premaster peak ~-6 dBFS (loudness is the LAST priority).
TRACK_VOLUMES: dict[str, float] = {
    "Kick": 0.0,
    "Clap": -10.0,
    "Closed Hat": -14.0,
    "Shaker": -16.0,
    "Conga": -14.0,
    "Clave": -12.0,
    "Perc Loop": -14.0,
    "Bass": -6.0,
    "Vocal": -12.0,
    "Stab": -14.0,
    "Guitar": -13.0,
    "Sax": -14.0,
    "FX": -16.0,
    "Impact": -10.0,
    "Downlifter": -14.0,
    "Texture": -16.0,
}

MASTER_CHAIN: list[str] = ["EQ Eight", "Glue Compressor", "Saturator", "Limiter"]

MASTER_LIMITER_CEILING = -0.3  # dB

# Parameter aliases/scales are centralized in producer.parameter_registry.


def _set_master_limiter(daw, report: dict[str, Any]) -> None:
    """Find the master Limiter + set its ceiling. Best-effort."""
    for di in range(6):
        try:
            params = daw.get_device_parameters(-1, di)
        except Exception:  # noqa: BLE001
            continue
        spec = get_device_spec(str(params.get("device_name") or ""))
        if spec is None or "ceiling" not in spec.parameters:
            continue
        pidx = resolve_parameter_index(
            params.get("parameters") or [],
            spec.parameters["ceiling"].aliases,
        )
        if pidx is None:
            continue
        value = limiter_ceiling_db_to_normalized(MASTER_LIMITER_CEILING)
        daw.set_device_parameter(-1, di, pidx, value)
        report["master_tweaks"] += 1
        return




def _autonomous_batch_mix_pass(daw, *, session, report: dict[str, Any]) -> None:
    """ASTRAL batch pass: apply small multi-param patches on key effects.

    Uses soniq-style apply_patch (schema->coalesce->batch->readback->events).
    Best effort only.
    """
    targets = [
        ("Bass", "Compressor"),
        ("Kick", "Drum Buss"),
    ]

    for track_name, device_name in targets:
        try:
            schema = read_vst_schema(
                daw,
                session=session,
                track_name=track_name,
                device_name=device_name,
                filter_midi_passthrough=False,
            )
            names = [str(p.get("name") or "").lower() for p in schema.get("parameters", [])]

            writes = []
            if any("threshold" in n or "umbral" in n for n in names):
                writes.append({"name": "threshold", "value": 0.45})
            if any("ratio" in n for n in names):
                writes.append({"name": "ratio", "value": 0.55})
            if any("release" in n or "liber" in n for n in names):
                writes.append({"name": "release", "value": 0.40})
            if any("mix" in n or "dry/wet" in n for n in names):
                writes.append({"name": "mix", "value": 0.6})

            if not writes:
                # Fallback to generic multi-param tweak excluding Device On (index 0)
                idxs = [p.get("index") for p in schema.get("parameters", []) if int(p.get("index", -1)) > 0][:2]
                writes = [{"index": int(i), "value": 0.55} for i in idxs]

            if not writes:
                continue

            patch = apply_patch(
                daw,
                session=session,
                track_name=track_name,
                device_name=device_name,
                writes=writes,
                throttle_ms=40,
                filter_midi_passthrough=False,
            )
            report.setdefault("batch_patches", []).append({"target": f"{track_name}:{device_name}", "patch": patch})
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"batch_patch {track_name}/{device_name}: {exc}")


def apply_mix(daw, *, session) -> dict[str, Any]:
    """Balance track volumes + load and configure the master chain."""
    report: dict[str, Any] = {
        "volumes": 0,
        "master_devices": 0,
        "master_tweaks": 0,
        "errors": [],
    }
    by_name = {t.name: t for t in session.tracks}

    # 1) static balance (volumes)
    for name, vol in TRACK_VOLUMES.items():
        t = by_name.get(name)
        if t is None:
            continue
        try:
            daw.set_mixer_volume(int(t.index), track_volume_db_to_linear(vol))
            report["volumes"] += 1
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"volume {name}: {exc}")

    # 2) master chain
    for dev in MASTER_CHAIN:
        try:
            daw.load_instrument_or_effect(-1, dev)
            report["master_devices"] += 1
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"master {dev}: {exc}")

    # 3) limiter ceiling
    try:
        _set_master_limiter(daw, report)
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"limiter: {exc}")

    # 4) autonomous batch patch pass (Soniq-style)
    try:
        _autonomous_batch_mix_pass(daw, session=session, report=report)
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"batch_pass: {exc}")

    return report
