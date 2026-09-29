from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log10
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
    """Legacy assumed mapping; not evidence of Live's native parameter units."""
    db = _clamp(db, min_db, max_db)
    return _clamp((db - min_db) / (max_db - min_db), 0.0, 1.0)


def limiter_ceiling_normalized_to_db(
    value: float,
    *,
    min_db: float = LIMITER_CEILING_MIN_DB,
    max_db: float = LIMITER_CEILING_MAX_DB,
) -> float:
    """Legacy assumed mapping; not suitable for certified Live writes."""
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


@dataclass(frozen=True)
class CertifiedParameter:
    index: int
    name: str
    value: float
    minimum: float
    maximum: float
    unit: str


def introspect_mix_parameter(
    device_class: str,
    parameters: list[dict],
    *,
    control: str,
    band: int | None = None,
) -> tuple[CertifiedParameter | None, str | None]:
    """Certify a native Live parameter only when its name, bounds and unit are explicit.

    The current bridge provides numeric ranges but no physical unit. In that
    case frequency/gain/Q/ceiling must remain unavailable rather than treating
    a 0..1 native range as Hz, dB or a linear normalized mapping.
    """
    kind = normalize_name(device_class)
    if kind not in {"eq eight", "eq8", "limiter"}:
        return None, "DEVICE_CLASS_UNSUPPORTED"
    control = normalize_name(control)
    if control == "enabled":
        if band is not None:
            return None, "CONTROL_UNSUPPORTED"
        names, unit = {"device on"}, "boolean"
    elif kind in {"eq eight", "eq8"} and band is not None and isinstance(band, int) and not isinstance(band, bool) and 1 <= band <= 8:
        if control not in {"frequency", "gain", "q"}:
            return None, "CONTROL_UNSUPPORTED"
        # Exact names only; suffixes distinguish Live's A/B parameter banks.
        label = {"frequency": "frequency", "gain": "gain", "q": "resonance"}[control]
        names, unit = {f"{band} {label} a", f"{band} {label}"}, {"frequency": "hz", "gain": "db", "q": "q"}[control]
    elif kind == "limiter" and control == "ceiling" and band is None:
        names, unit = {"ceiling"}, "db"
    else:
        return None, "CONTROL_UNSUPPORTED"
    matches = [row for row in parameters if normalize_name(str(row.get("name") or "")) in names]
    if len(matches) != 1:
        return None, "PARAMETER_NOT_FOUND_OR_AMBIGUOUS"
    row = matches[0]
    try:
        index = row["index"]
        if isinstance(index, bool) or int(index) != index or int(index) < 0:
            raise ValueError
        value, minimum, maximum = (float(row[key]) for key in ("value", "min", "max"))
        if not all(map(isfinite, (value, minimum, maximum))) or not minimum < maximum or not minimum <= value <= maximum:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return None, "PARAMETER_METADATA_INVALID"
    if sum(other.get("index") == index for other in parameters) != 1:
        return None, "PARAMETER_INDEX_UNCERTIFIED"
    if row.get("is_enabled") is False:
        return None, "PARAMETER_METADATA_UNCERTIFIED"
    if unit == "boolean":
        if (minimum, maximum) != (0.0, 1.0) or row.get("is_quantized") is not True or value not in (0.0, 1.0):
            return None, "PARAMETER_METADATA_UNCERTIFIED"
    elif normalize_name(str(row.get("unit") or "")) != unit or row.get("is_quantized") is True:
        return None, "PHYSICAL_UNIT_UNCERTIFIED"
    if unit in {"hz", "q"} and minimum <= 0:
        return None, "PARAMETER_METADATA_INVALID"
    return CertifiedParameter(int(index), str(row["name"]), value, minimum, maximum, unit), None
