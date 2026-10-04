import hashlib
import json
from pathlib import Path

from copilot.audio.live_capture import tap_requires_refresh

from copilot.importing.m4l_runtime_v1 import (
    DEVICE_NAME,
    _configured_tap_asset,
    canonical_tap_asset,
    ensure_m4l_runtime,
    item_is_canonical_tap,
    runtime_paths,
)


def test_canonical_tap_asset_is_repo_device() -> None:
    asset = canonical_tap_asset()
    assert asset["status"] == "VERIFIED"
    assert asset["device_name"] == DEVICE_NAME
    assert asset["tap_protocol_expected"] == 3
    path = Path(asset["asset_path"])
    assert path.is_file()
    assert path.name == "Copilot Audio Tap.amxd"
    assert "devices" in path.parts
    assert asset["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_ensure_m4l_runtime_installs_and_is_idempotent(tmp_path: Path) -> None:
    library = tmp_path / "User Library"
    (library / "Presets").mkdir(parents=True)
    first = ensure_m4l_runtime(user_library=library, evidence=tmp_path / "ev1")
    assert first["status"] == "INSTALLED"
    dest = Path(first["installed"])
    assert dest.is_file()
    assert dest.parent.name == "Copilot"
    assert dest.name == "Copilot Audio Tap.amxd"
    second = ensure_m4l_runtime(user_library=library, evidence=tmp_path / "ev2")
    assert second["status"] == "ALREADY_CURRENT"
    assert Path(second["installed"]) == dest
    copies = list(library.rglob("Copilot Audio Tap.amxd"))
    assert len(copies) == 1
    alias = dest.with_name("Copilot Audio Tap 5.amxd")
    assert alias.is_file()
    assert hashlib.sha256(alias.read_bytes()).hexdigest() == hashlib.sha256(dest.read_bytes()).hexdigest()


def test_tap_asset_binds_capture_directory_at_provision_time(tmp_path: Path) -> None:
    template = canonical_tap_asset()
    asset, payload = _configured_tap_asset(
        template=template,
        capture_root=tmp_path / "captures",
    )

    assert asset["configured_for_host"] is True
    assert asset["capture_dir"] == str((tmp_path / "captures").resolve())
    assert b"__COPILOT_CAPTURE_DIR__" not in payload
    assert b"D:/MusicCopilot/captures" not in payload
    assert (tmp_path / "captures").resolve().as_posix().encode() in payload


def test_ensure_m4l_runtime_blocks_unmanaged_conflict(tmp_path: Path) -> None:
    library = tmp_path / "User Library"
    paths = runtime_paths(library)
    paths["device"].parent.mkdir(parents=True)
    paths["device"].write_bytes(b"not-the-canonical-tap")
    out = ensure_m4l_runtime(user_library=library, evidence=tmp_path / "ev")
    assert out["status"] == "BLOCKED"
    assert out["reason"] == "USER_OWNED_CONFLICT"
    assert paths["device"].read_bytes() == b"not-the-canonical-tap"


def test_canonical_tap_match_is_exact() -> None:
    assert item_is_canonical_tap({"name": "Copilot Audio Tap", "is_loadable": True}) is True
    assert item_is_canonical_tap({"name": "Copilot Audio Tap.amxd", "is_loadable": True}) is True
    assert item_is_canonical_tap({"name": "Copilot Audio Tap 4", "is_loadable": True}) is True
    assert item_is_canonical_tap({"name": "Copilot Audio Tap 5", "is_loadable": True}) is True
    assert item_is_canonical_tap({"name": "Copilot Audio Tap", "is_loadable": False}) is False
    assert item_is_canonical_tap({"name": "Not Copilot Audio Tap", "is_loadable": True}) is False
    assert item_is_canonical_tap({"name": "Audio Tap", "is_loadable": True}) is False


def test_tap_patcher_routes_both_live_input_channels_to_recorder() -> None:
    root = Path(__file__).resolve().parents[1]
    patcher = json.loads((root / "devices" / "Copilot Audio Tap.maxpat").read_text(encoding="utf-8"))["patcher"]
    boxes = {box["box"]["id"]: box["box"] for box in patcher["boxes"]}
    assert boxes["obj-plugin"]["text"] == "plugin~ 1 2"
    assert boxes["obj-plugin"]["numoutlets"] == 2
    lines = {(tuple(line["patchline"]["source"]), tuple(line["patchline"]["destination"])) for line in patcher["lines"]}
    assert boxes["obj-rec"]["text"] == "sfrecord~ 2"
    assert (("obj-plugin", 0), ("obj-rec", 0)) in lines
    assert (("obj-plugin", 1), ("obj-rec", 1)) in lines


def test_compiled_v4_tap_requires_stereo_refresh() -> None:
    class StubDaw:
        def get_device_parameters(self, track_index, device_index):
            return {"parameters": [{"name": "Slot", "max": 8}, {"name": "TapProtocol", "value": 4}]}

    daw = StubDaw()
    assert tap_requires_refresh(daw, 0, 0, device_name="Copilot Audio Tap 4")["stale"] is True
    assert tap_requires_refresh(daw, 0, 0, device_name="Copilot Audio Tap 5")["stale"] is False
