from __future__ import annotations

from pathlib import Path

from copilot.daw.detect import AbletonDetection
from copilot.daw.session_ready_v1 import SESSION_READY, SessionReadyProbe
from copilot.runtime.environment_autonomy_v1 import (
    HUMAN_ACTION_REQUIRED,
    PROJECT_READY,
    discover_environment,
    ensure_ableton_ready,
)


def _detection(*, found: bool = True, port_open: bool = False) -> AbletonDetection:
    return AbletonDetection(
        found=found,
        version="12",
        exe_path="/detected/Ableton Live",
        prefs_root="/detected/preferences",
        user_remote_scripts="/detected/Remote Scripts",
        process_running=False,
        port_open=port_open,
        evidence=["test detector"],
    )


def _ready(target: Path) -> SessionReadyProbe:
    return SessionReadyProbe(
        status=SESSION_READY,
        port_open=True,
        handshake_ok=True,
        request_id_ok=True,
        snapshot_ok=True,
        project_identity="project-token",
        project_path=str(target),
        project_name=target.name,
        reason=SESSION_READY,
        zombie_port=False,
        writes_permitted=True,
        handshake_protocol="2",
    )


def test_discovery_is_host_agnostic_and_uses_injected_detector(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def detector(port: int, *, include_start_menu: bool) -> AbletonDetection:
        seen.update(port=port, include_start_menu=include_start_menu)
        return _detection()

    profile = discover_environment(repo=tmp_path, port=19999, detector=detector)
    assert profile.repository_path == str(tmp_path.resolve())
    assert profile.architecture
    assert profile.user
    assert seen == {"port": 19999, "include_start_menu": False}


def test_raw_als_is_rejected_without_working_copy_manifest(tmp_path: Path) -> None:
    als = tmp_path / "original.als"
    als.write_bytes(b"original")
    report = ensure_ableton_ready(
        working_als=als,
        provisioner=lambda: {"status": "ALREADY_CURRENT"},
        detector=lambda *args, **kwargs: _detection(),
    )
    assert report["status"] == "BLOCKED"
    assert report["reason"] == "WORKING_COPY_REQUIRED"
    assert report["MUSICAL WRITES"] == 0


def test_source_project_is_copied_and_reaches_project_ready(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Source Project"
    source_root.mkdir()
    source = source_root / "Source.als"
    source.write_bytes(b"set")
    target_workspace = tmp_path / "CopilotProjects"

    def launcher(target: Path, **kwargs):
        return {"status": SESSION_READY, "working_als": str(target)}

    monkeypatch.setattr(
        "copilot.runtime.environment_autonomy_v1.probe_session_ready",
        lambda host, port: _ready(target_workspace / "Source Project" / "Source.als"),
    )
    report = ensure_ableton_ready(
        source_als=source,
        project_root=source_root,
        workspace=target_workspace,
        provisioner=lambda: {"status": "ALREADY_CURRENT"},
        launcher=launcher,
        detector=lambda *args, **kwargs: _detection(port_open=True),
    )
    assert report["status"] == PROJECT_READY
    assert report["PROJECT_READY"] == "VERIFIED"
    assert Path(report["working_als"]).is_file()
    assert source.read_bytes() == b"set"
    assert report["lifecycle"][-1] == PROJECT_READY


def test_unconfigured_control_surface_is_reported_as_human_boundary(tmp_path: Path) -> None:
    source_root = tmp_path / "Source"
    source_root.mkdir()
    source = source_root / "Source.als"
    source.write_bytes(b"set")

    report = ensure_ableton_ready(
        source_als=source,
        project_root=source_root,
        workspace=tmp_path / "workspace",
        provisioner=lambda: {"status": "ALREADY_CURRENT"},
        launcher=lambda *args, **kwargs: {"status": "LIVE_UNAVAILABLE"},
        detector=lambda *args, **kwargs: _detection(port_open=False),
    )
    assert report["status"] == "BLOCKED"
    assert report["reason"] == HUMAN_ACTION_REQUIRED
    assert report["control_surface"]["attempted"]

