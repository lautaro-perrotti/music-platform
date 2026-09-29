"""Read-only native-unit attestation for Live device parameters."""

import re


def attested_native_unit(param, *, device_class=""):
    """Return a unit only when Live's displayed and native scales agree."""
    formatter = getattr(param, "str_for_value", None)
    if not callable(formatter) or param.is_quantized:
        return None
    minimum, maximum = float(param.min), float(param.max)
    if not minimum < maximum:
        return None
    q_control = (
        device_class in ("Eq8", "Eq Eight")
        and re.fullmatch(r"[1-8] Resonance(?: A)?", param.name, re.I) is not None
        and minimum > 0
    )
    unit = None
    for native in (minimum, float(param.value), maximum):
        try:
            display = formatter(native).strip()
        except (TypeError, ValueError, RuntimeError):
            return None
        match = re.fullmatch(
            r"([+-]?\d+(?:\.\d+)?)\s*(Hz|kHz|dB)" if not q_control
            else r"([+-]?\d+(?:\.\d+)?)", display, re.I,
        )
        if match is None:
            return None
        actual_unit = match.group(2).lower() if not q_control else "q"
        physical_unit = "q" if q_control else "hz" if actual_unit in ("hz", "khz") else "db"
        physical = float(match.group(1)) * (1000.0 if actual_unit == "khz" else 1.0)
        if unit is not None and unit != physical_unit:
            return None
        if abs(physical - native) > max(0.051, abs(native) * 0.005):
            return None
        unit = physical_unit
    return unit
