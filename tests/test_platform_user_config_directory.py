from pathlib import Path

import copilot.platform.system as host


def test_user_config_directory_override_wins_on_every_platform(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSIC_PLATFORM_CONFIG_DIR", str(tmp_path / "explicit"))
    monkeypatch.setattr(host, "host_system", lambda: "Windows")
    assert host.user_config_directory(
        "MusicPlatform", override_variable="MUSIC_PLATFORM_CONFIG_DIR"
    ) == tmp_path / "explicit"


def test_user_config_directory_uses_platform_owned_native_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MUSIC_PLATFORM_CONFIG_DIR", raising=False)

    monkeypatch.setattr(host, "host_system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert host.user_config_directory(
        "MusicPlatform", override_variable="MUSIC_PLATFORM_CONFIG_DIR"
    ) == tmp_path / "Roaming" / "MusicPlatform"

    monkeypatch.setattr(host, "host_system", lambda: "Darwin")
    assert host.user_config_directory(
        "MusicPlatform", override_variable="MUSIC_PLATFORM_CONFIG_DIR"
    ) == Path.home() / "Library" / "Application Support" / "MusicPlatform"

    monkeypatch.setattr(host, "host_system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert host.user_config_directory(
        "MusicPlatform",
        override_variable="MUSIC_PLATFORM_CONFIG_DIR",
        linux_directory_name="music-platform",
    ) == tmp_path / "xdg" / "music-platform"
