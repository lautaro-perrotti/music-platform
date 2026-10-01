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


def test_transport_event_capability_is_advertised_only_when_listener_setup_succeeds(
    tmp_path: Path, monkeypatch,
) -> None:
    bridge = _load_bridge(tmp_path, monkeypatch, include_units=False)
    instance = object.__new__(bridge.AbletonMCP)
    instance.log_message = lambda _message: None
    instance._song = SimpleNamespace(is_playing=False, tempo=120.0)
    assert "events.transport.v1" not in instance._advertised_capabilities()

    callbacks = {}

    def add_playing_listener(callback):
        callbacks["playing"] = callback

    def add_tempo_listener(callback):
        callbacks["tempo"] = callback

    def remove_listener(_callback):
        return None

    instance._song.add_is_playing_listener = add_playing_listener
    instance._song.remove_is_playing_listener = remove_listener
    instance._song.add_tempo_listener = add_tempo_listener
    instance._song.remove_tempo_listener = remove_listener
    instance._transport_event_lock = bridge.threading.RLock()
    instance._bridge_session_id = "bridge_test"
    instance._transport_listener_bindings = []
    instance._transport_subscribers = {}
    instance._transport_event_sequence = 0
    instance._transport_event_capable = False
    instance._setup_transport_event_listeners()

    assert instance._transport_event_capable is True
    assert "events.transport.v1" in instance._advertised_capabilities()

    from queue import Queue
    event_queue = Queue()
    instance._transport_subscribers[object()] = event_queue
    instance._song.is_playing = True
    callbacks["playing"]()
    event = event_queue.get_nowait()
    assert event["event_type"] == "TRANSPORT_CHANGED"
    assert event["sequence"] == 1
    assert event["state"] == {"playing": True, "tempo": 120.0}
