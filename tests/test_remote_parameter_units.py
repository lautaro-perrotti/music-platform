import importlib.util
from pathlib import Path
from types import SimpleNamespace


_SOURCE = (
    Path(__file__).resolve().parents[1] / "vendor" /
    "abletonmcp_remote_script" / "AbletonMCP" / "parameter_units.py"
)
_SPEC = importlib.util.spec_from_file_location("remote_parameter_units", _SOURCE)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _param(minimum, value, maximum, display, *, quantized=False):
    return SimpleNamespace(
        min=minimum, value=value, max=maximum, str_for_value=display,
        is_quantized=quantized,
    )


def test_native_physical_unit_requires_live_display_parity():
    assert _MODULE.attested_native_unit(
        _param(-15, -3, 15, lambda value: f"{value:.1f} dB")
    ) == "db"
    assert _MODULE.attested_native_unit(
        _param(100, 500, 1000, lambda value: f"{value / 1000:.3f} kHz")
    ) == "hz"
    assert _MODULE.attested_native_unit(
        _param(0, .5, 1, lambda value: f"{20 + value * 19980:.0f} Hz")
    ) is None
    assert _MODULE.attested_native_unit(
        _param(0, .5, 1, lambda value: f"{value:.2f}", quantized=True)
    ) is None
    assert _MODULE.attested_native_unit(
        _param(-15, -3, 15, lambda _: "unknown")
    ) is None
    q = _param(.1, .7, 12, lambda value: f"{value:.2f}")
    q.name = "1 Resonance A"
    assert _MODULE.attested_native_unit(q, device_class="Eq8") == "q"
    q.name = "Unrelated"
    assert _MODULE.attested_native_unit(q, device_class="Eq8") is None
