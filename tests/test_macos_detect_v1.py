from __future__ import annotations

from pathlib import Path

from copilot.daw.detect import (
    _first_live_bundle,
    default_user_library_candidates,
    prefs_search_roots,
)


def test_default_user_library_includes_mac_music_path() -> None:
    tails = [path.parts[-3:] for path in default_user_library_candidates()]
    assert ("Documents", "Ableton", "User Library") in tails
    assert ("Music", "Ableton", "User Library") in tails


def test_prefs_search_includes_mac_library_preferences() -> None:
    tails = [path.parts[-3:] for path in prefs_search_roots()]
    assert ("AppData", "Roaming", "Ableton") in tails
    assert ("Library", "Preferences", "Ableton") in tails


def test_first_live_bundle_resolves_macos_app_layout(tmp_path: Path) -> None:
    exe = tmp_path / "Ableton Live 12 Suite.app" / "Contents" / "MacOS" / "Live"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"live")
    found = _first_live_bundle(tmp_path)
    assert found == exe


def test_detect_module_imports_without_requiring_winreg() -> None:
    import copilot.daw.detect as detect

    assert hasattr(detect, "detect_ableton")
    assert detect.default_user_library_candidates()
