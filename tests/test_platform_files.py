from pathlib import Path

from copilot.platform.files import exclusive_open_ok, lock_owners, shared_read_ok


def test_file_probes_are_platform_neutral(tmp_path: Path):
    path = tmp_path / "capture.wav"
    path.write_bytes(b"RIFF")

    exclusive = exclusive_open_ok(path)
    readable = shared_read_ok(path)

    assert exclusive["exists"] is True
    assert exclusive["exclusive"] is True
    assert readable["exists"] is True
    assert readable["readable"] is True
    assert isinstance(lock_owners(path), list)


def test_file_probes_report_missing_files_without_platform_imports(tmp_path: Path):
    path = tmp_path / "missing.wav"

    assert exclusive_open_ok(path)["exists"] is False
    assert shared_read_ok(path)["exists"] is False
    assert lock_owners(path) == []
