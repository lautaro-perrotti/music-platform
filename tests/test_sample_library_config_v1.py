from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.cli import main
from copilot.sample_library.config import add_root, config_path, load_config, remove_root
from copilot.sample_library.library_v1 import load_index


def _make_sample(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = np.zeros(2400, dtype=np.float32)
    audio[200:260] = np.linspace(0.8, 0.0, 60, dtype=np.float32)
    sf.write(path, audio, 24_000, subtype="PCM_16")


def test_library_roots_are_explicit_deduplicated_and_persistent(tmp_path):
    config_file = tmp_path / "config" / "sample-library.json"
    root = tmp_path / "samples"
    root.mkdir()

    config, added = add_root(root, path=config_file)
    assert added is True
    assert config.roots == [str(root.resolve())]

    config, added = add_root(root / ".", path=config_file)
    assert added is False
    assert load_config(config_file).roots == [str(root.resolve())]

    config, removed = remove_root(root, path=config_file)
    assert removed is True
    assert config.roots == []


def test_add_root_rejects_non_directory_and_config_corruption_fails_closed(tmp_path):
    file_path = tmp_path / "not-a-library.wav"
    _make_sample(file_path)
    config_file = tmp_path / "sample-library.json"

    try:
        add_root(file_path, path=config_file)
    except ValueError as exc:
        assert str(exc) == "SAMPLE_LIBRARY_ROOT_NOT_DIRECTORY"
    else:
        raise AssertionError("a file must not be accepted as a library root")

    config_file.write_text("{not json", encoding="utf-8")
    try:
        load_config(config_file)
    except ValueError as exc:
        assert "SAMPLE_LIBRARY_CONFIG_INVALID" in str(exc)
    else:
        raise AssertionError("invalid config must fail closed")


def test_cli_indexes_only_configured_roots_and_deduplicates_by_hash(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "user-config"
    library = tmp_path / "explicit-library"
    first = library / "Kicks" / "kick-a.wav"
    duplicate = library / "Archive" / "same-content.wav"
    _make_sample(first)
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    duplicate.write_bytes(first.read_bytes())
    monkeypatch.setenv("MUSIC_PLATFORM_CONFIG_DIR", str(config_dir))
    monkeypatch.chdir(tmp_path)

    assert main(["sample-library", "add", str(library)]) == 0
    added_report = json.loads(capsys.readouterr().out)
    assert added_report["status"] == "ROOT_ADDED"
    assert added_report["disk_scan"] is False

    assert main(["sample-library", "index"]) == 0
    index_report = json.loads(capsys.readouterr().out)
    assert index_report["status"] == "INDEXED"
    assert index_report["NEW"] == 2
    assert index_report["duplicates"] == 1
    assert index_report["total_indexed"] == 1

    index = load_index(config_dir / "sample-library-index.json")
    assert index is not None
    asset = next(iter(index.assets.values()))
    assert asset.sha256
    assert asset.size_bytes == first.stat().st_size
    assert asset.descriptors.duration_s == 0.1
    assert asset.tags == ["Kicks"]
    assert set(index.duplicates[asset.sha256]) | {asset.path} == {str(first), str(duplicate)}


def test_cli_index_without_configured_root_does_not_scan_disks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MUSIC_PLATFORM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)

    assert main(["sample-library", "index"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == "SAMPLE_LIBRARY_NO_ROOTS_CONFIGURED"
    assert report["disk_scan"] is False


def test_cli_listing_reports_missing_configured_root_without_removing_it(tmp_path, monkeypatch, capsys):
    root = tmp_path / "samples"
    root.mkdir()
    config_dir = tmp_path / "config"
    monkeypatch.setenv("MUSIC_PLATFORM_CONFIG_DIR", str(config_dir))
    add_root(root, path=config_path(config_dir))
    root.rename(tmp_path / "samples-offline")
    monkeypatch.chdir(tmp_path)

    assert main(["sample-library", "list"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["roots"] == [{"path": str(root.resolve()), "available": False}]
    assert load_config(config_path(config_dir)).roots == [str(root.resolve())]
