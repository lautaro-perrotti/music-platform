from __future__ import annotations

from dataclasses import dataclass
from math import log10
from typing import Callable


@dataclass(frozen=True)
class ParameterSpec:
    unit: str
    aliases: tuple[str, ...]
    encode: Callable[[float], float]
    decode: Callable[[float], float]


@dataclass(frozen=True)
class DeviceSpec:
    aliases: tuple[str, ...]
    parameters: dict[str, ParameterSpec]


UNITY_LINEAR = 0.85  # Ableton mixer volume for ~0 dB
LIMITER_CEILING_MIN_DB = -36.0
LIMITER_CEILING_MAX_DB = 0.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def track_volume_db_to_linear(db: float, *, unity_linear: float = UNITY_LINEAR) -> float:
    """Engineering dB -> Ableton mixer linear value (0..1)."""
    return _clamp(unity_linear * (10 ** (float(db) / 20.0)), 0.0, 1.0)


def track_volume_linear_to_db(
    value: float, *, unity_linear: float = UNITY_LINEAR, floor_db: float = -120.0
) -> float:
    """Ableton mixer linear value (0..1) -> engineering dB."""
    value = _clamp(value, 0.0, 1.0)
    if value <= 0.0:
        return floor_db
    return 20.0 * log10(value / unity_linear)


def limiter_ceiling_db_to_normalized(
    db: float,
    *,
    min_db: float = LIMITER_CEILING_MIN_DB,
    max_db: float = LIMITER_CEILING_MAX_DB,
) -> float:
    """Engineering dB -> Limiter ceiling normalized parameter (0..1)."""
    db = _clamp(db, min_db, max_db)
    return _clamp((db - min_db) / (max_db - min_db), 0.0, 1.0)


def limiter_ceiling_normalized_to_db(
    value: float,
    *,
    min_db: float = LIMITER_CEILING_MIN_DB,
    max_db: float = LIMITER_CEILING_MAX_DB,
) -> float:
    """Limiter ceiling normalized parameter (0..1) -> engineering dB."""
    value = _clamp(value, 0.0, 1.0)
    return min_db + value * (max_db - min_db)


def normalize_name(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def any_alias_matches(name: str, aliases: tuple[str, ...]) -> bool:
    n = normalize_name(name)
    return any(a in n for a in aliases)


def resolve_parameter_index(
    parameters: list[dict], aliases: tuple[str, ...]
) -> int | None:
    """Find parameter index by EN/ES aliases over a parameter list from bridge."""
    for p in parameters or []:
        pname = str(p.get("name") or "")
        if any_alias_matches(pname, aliases):
            idx = p.get("index")
            if idx is not None:
                return int(idx)
    return None


DEVICE_REGISTRY: dict[str, DeviceSpec] = {
    "limiter": DeviceSpec(
        aliases=("limiter", "limitador"),
        parameters={
            "ceiling": ParameterSpec(
                unit="db",
                aliases=("ceiling", "techo", "output", "salida", "peak", "out"),
                encode=limiter_ceiling_db_to_normalized,
                decode=limiter_ceiling_normalized_to_db,
            ),
            "input_gain": ParameterSpec(
                unit="normalized",
                aliases=("input gain", "ganancia de entrada"),
                encode=lambda v: _clamp(v, 0.0, 1.0),
                decode=lambda v: _clamp(v, 0.0, 1.0),
            ),
        },
    ),
    "mixer": DeviceSpec(
        aliases=("mixer",),
        parameters={
            "volume": ParameterSpec(
                unit="db",
                aliases=("volume", "volumen"),
                encode=track_volume_db_to_linear,
                decode=track_volume_linear_to_db,
            )
        },
    ),
}


def get_device_spec(device_name: str) -> DeviceSpec | None:
    name = normalize_name(device_name)
    for spec in DEVICE_REGISTRY.values():
        if any(a in name for a in spec.aliases):
            return spec
    return None
