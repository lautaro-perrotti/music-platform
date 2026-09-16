from __future__ import annotations

import json

from copilot.cli import main
from copilot.daw.detect import detect_ableton
from copilot.daw.install_remote_script import install_remote_script


def test_detect_reports_environment() -> None:
    detection = detect_ableton()
    if not detection.found:
        assert detection.exe_path is None
        assert detection.process_running is False
        return
    assert detection.exe_path
    assert detection.prefs_root
    assert "Live Reports" not in (detection.prefs_root or "")
    assert detection.user_remote_scripts
    assert "Live Reports" not in detection.user_remote_scripts


def test_install_script_status_matches_detection() -> None:
    detection = detect_ableton()
    result = install_remote_script()
    assert result["port"] == 9877
    assert result["protocol"] == "tcp-json"
    if not detection.found:
        assert result["status"] == "BLOCKED_BY_ENVIRONMENT"
        return
    assert result["status"] == "INSTALLED"
    assert result["sha256"]


def test_cli_probe_never_uses_mock(capsys) -> None:
    code = main(["probe"])
    captured = json.loads(capsys.readouterr().out)
    assert code == 2
    assert captured["status"] in {
        "BLOCKED_BY_ENVIRONMENT",
        "MANUAL_CONFIGURATION_REQUIRED",
    }
    assert captured.get("backend") != "mock"
    detection = detect_ableton()
    if detection.found and not detection.port_open:
        assert captured["status"] == "MANUAL_CONFIGURATION_REQUIRED"


def test_cli_slice1_never_falls_back_to_mock(capsys) -> None:
    code = main(["slice1"])
    captured = json.loads(capsys.readouterr().out)
    assert code == 2
    assert captured["verification_class"] in {
        "BLOCKED_BY_ENVIRONMENT",
        "MANUAL_CONFIGURATION_REQUIRED",
    }
    assert captured.get("backend") != "mock"
