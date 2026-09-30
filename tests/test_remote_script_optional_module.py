from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


BRIDGE_DIR = (
    Path(__file__).resolve().parents[1]
    / "vendor" / "abletonmcp_remote_script" / "AbletonMCP"
)


def _load_bridge(tmp_path: Path, monkeypatch, *, include_units: bool):
    package_dir = tmp_path / "AbletonMCP"
    package_dir.mkdir()
    shutil.copy2(BRIDGE_DIR / "__init__.py", package_dir / "__init__.py")
    if include_units:
        shutil.copy2(BRIDGE_DIR / "parameter_units.py", package_dir / "parameter_units.py")

    framework = ModuleType("_Framework")
    control_surface_module = ModuleType("_Framework.ControlSurface")

    class ControlSurface:
        def __init__(self, *_args, **_kwargs):
            pass

    control_surface_module.ControlSurface = ControlSurface
    framework.ControlSurface = control_surface_module
    monkeypatch.setitem(sys.modules, "_Framework", framework)
    monkeypatch.setitem(sys.modules, "_Framework.ControlSurface", control_surface_module)

    module_name = "test_optional_remote_" + str(id(package_dir))
    spec = importlib.util.spec_from_file_location(
        module_name, package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_missing_optional_parameter_module_keeps_bridge_loadable_and_withholds_capability(
    tmp_path: Path, monkeypatch,
) -> None:
    bridge = _load_bridge(tmp_path, monkeypatch, include_units=False)
    instance = object.__new__(bridge.AbletonMCP)
    instance._song = SimpleNamespace()
    instance.log_message = lambda _message: None

    capabilities = instance._advertised_capabilities()
    assert "session.read" in capabilities
    assert "device.physical_units_v1" not in capabilities
    assert "session.save" not in capabilities
    assert bridge._PARAMETER_UNITS_IMPORT_ERROR


def test_save_capability_is_runtime_negotiated_not_inferred_from_transport(
    tmp_path: Path, monkeypatch,
) -> None:
    bridge = _load_bridge(tmp_path, monkeypatch, include_units=False)
    instance = object.__new__(bridge.AbletonMCP)
    instance.log_message = lambda _message: None
    instance._song = SimpleNamespace()
    assert "session.save" not in instance._advertised_capabilities()

    instance._song = SimpleNamespace(save=lambda: None)
    assert "session.save" in instance._advertised_capabilities()
