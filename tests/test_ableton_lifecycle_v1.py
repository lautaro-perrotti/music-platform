from __future__ import annotations

from pathlib import Path

from copilot.importing.crash_recovery_v1 import (
    inspect_recovery_metadata,
    quarantine_controlled_recovery_metadata,
)
from copilot.platform.ableton import driver_for_system


def test_shutdown_targets_exact_pid_and_keeps_force_as_last_resort() -> None:
    driver = driver_for_system("Windows")
    assert driver.request_shutdown_command(20344) == [
        "taskkill",
        "/PID",
        "20344",
        "/T",
    ]
    assert driver.force_shutdown_command(20344) == [
        "taskkill",
        "/PID",
        "20344",
        "/T",
        "/F",
    ]


def test_controlled_recovery_metadata_is_classified_and_quarantined(tmp_path: Path) -> None:
    prefs = tmp_path / "Live 12" / "Preferences"
    prefs.mkdir(parents=True)
    metadata = prefs / "CrashRecoveryInfo.cfg"
    controlled = Path("C:/Users/tester/CopilotProjects/fresh/song.als")
    metadata.write_bytes(
        ("OriginalFileRef\x00" + controlled.as_posix() + "\x00").encode("utf-16le")
    )

    observed = inspect_recovery_metadata(prefs.parent, controlled)
    assert observed["classification"] == "RECOVERY_OF_CONTROLLED_WORKING_COPY"
    result = quarantine_controlled_recovery_metadata(prefs.parent, controlled)
    assert result["quarantined"] is True
    assert not metadata.exists()
    assert Path(result["backup"]).is_file()


def test_original_recovery_metadata_is_not_quarantined(tmp_path: Path) -> None:
    prefs = tmp_path / "Live 12" / "Preferences"
    prefs.mkdir(parents=True)
    metadata = prefs / "CrashRecoveryInfo.cfg"
    original = Path("C:/Users/tester/Downloads/original/song.als")
    metadata.write_bytes((original.as_posix() + "\x00").encode("utf-16le"))

    controlled_target = Path("C:/Users/tester/CopilotProjects/fresh/song.als")
    result = quarantine_controlled_recovery_metadata(prefs.parent, controlled_target)
    assert result["classification"] == "RECOVERY_OF_ORIGINAL_PROJECT"
    assert result["quarantined"] is False
    assert metadata.exists()
