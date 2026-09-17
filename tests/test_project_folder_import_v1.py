from pathlib import Path

from copilot.importing.project_folder_import_v1 import import_project_folder
from copilot.importing.project_folder_resolver_v1 import resolve_ableton_project
from copilot.importing.working_copy_manager_v1 import create_working_copy


def _write_als(path: Path, payload: bytes = b"als") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_resolver_picks_single_als_and_skips_backup(tmp_path: Path) -> None:
    project = tmp_path / "Song Project"
    (project / "Samples").mkdir(parents=True)
    (project / "Ableton Project Info").mkdir()
    _write_als(project / "Song.als", b"primary")
    _write_als(project / "Backup" / "Song [yesterday].als", b"backup")
    out = resolve_ableton_project(tmp_path)
    assert out["status"] == "RESOLVED"
    assert out["source_als"].endswith("Song.als")
    assert "Backup" not in out["source_als"]
    assert out["copy_scope"] == "project_directory"


def test_resolver_nested_project_ignores_outer_preview(tmp_path: Path) -> None:
    outer = tmp_path / "Abletunes - Groove Rider"
    project = outer / "Abletunes - Groove Rider Project"
    (project / "Samples" / "Imported").mkdir(parents=True)
    (project / "Ableton Project Info").mkdir()
    (outer / "Preview.mp3").write_bytes(b"mp3")
    _write_als(project / "Abletunes - Groove Rider.als", b"groove")
    out = resolve_ableton_project(outer)
    assert out["status"] == "RESOLVED"
    assert Path(out["source_als"]).name == "Abletunes - Groove Rider.als"
    assert Path(out["project_root"]).name == "Abletunes - Groove Rider Project"


def test_resolver_does_not_guess_two_independent_sets(tmp_path: Path) -> None:
    _write_als(tmp_path / "a.als")
    _write_als(tmp_path / "b.als")
    out = resolve_ableton_project(tmp_path)
    assert out["status"] == "PROJECT_SELECTION_REQUIRED"
    assert len(out["candidates"]) == 2


def test_resolver_alp_only_is_not_installed(tmp_path: Path) -> None:
    (tmp_path / "pack.alp").write_bytes(b"alp")
    out = resolve_ableton_project(tmp_path)
    assert out["status"] == "ABLETON_PACK_INSTALL_REQUIRED"


def test_working_copy_leaves_original_bytes(tmp_path: Path) -> None:
    source_root = tmp_path / "Source Project"
    samples = source_root / "Samples" / "Imported"
    samples.mkdir(parents=True)
    als = source_root / "Source.als"
    wav = samples / "kick.wav"
    als.write_bytes(b"set-bytes")
    wav.write_bytes(b"wav-bytes")
    workspace = tmp_path / "CopilotProjects"
    first = create_working_copy(
        source_als=als,
        project_root=source_root,
        copy_scope="project_directory",
        workspace=workspace,
    )
    assert first["status"] == "CREATED"
    assert als.read_bytes() == b"set-bytes"
    assert wav.read_bytes() == b"wav-bytes"
    working_als = Path(first["working_als"])
    assert working_als.is_file()
    working_als.write_bytes(b"mutated-copy")
    assert als.read_bytes() == b"set-bytes"
    second = create_working_copy(
        source_als=als,
        project_root=source_root,
        copy_scope="project_directory",
        workspace=workspace,
    )
    assert second["status"] == "REUSED"
    assert Path(second["working_als"]) == working_als


def test_working_copy_versions_when_identity_differs(tmp_path: Path) -> None:
    workspace = tmp_path / "CopilotProjects"
    first_root = tmp_path / "Song Project"
    second_root = tmp_path / "Other" / "Song Project"
    (first_root / "Samples").mkdir(parents=True)
    (second_root / "Samples").mkdir(parents=True)
    first_als = first_root / "Song.als"
    second_als = second_root / "Song.als"
    first_als.write_bytes(b"one")
    second_als.write_bytes(b"two")
    one = create_working_copy(
        source_als=first_als,
        project_root=first_root,
        copy_scope="project_directory",
        workspace=workspace,
    )
    two = create_working_copy(
        source_als=second_als,
        project_root=second_root,
        copy_scope="project_directory",
        workspace=workspace,
    )
    assert one["status"] == "CREATED"
    assert two["status"] == "CREATED"
    assert Path(one["working_root"]) != Path(two["working_root"])
    assert Path(two["working_root"]).name.endswith("__2")
    assert first_als.read_bytes() == b"one"
    assert second_als.read_bytes() == b"two"


def test_import_blocks_without_guessing(tmp_path: Path) -> None:
    _write_als(tmp_path / "a.als")
    _write_als(tmp_path / "b.als")
    report = import_project_folder(tmp_path, evidence=tmp_path / "evidence")
    assert report["PROJECT_FOLDER_IMPORT_V1"] == "BLOCKED"
    assert report["reason"] == "PROJECT_SELECTION_REQUIRED"
    assert report["MUSICAL WRITES"] == 0


def test_import_copy_leaves_source_untouched(tmp_path: Path) -> None:
    outer = tmp_path / "Abletunes - Groove Rider"
    project = outer / "Abletunes - Groove Rider Project"
    (project / "Samples" / "Imported").mkdir(parents=True)
    (project / "Ableton Project Info").mkdir()
    als = project / "Abletunes - Groove Rider.als"
    wav = project / "Samples" / "Imported" / "kick.wav"
    als.write_bytes(b"groove-als")
    wav.write_bytes(b"groove-wav")
    report = import_project_folder(
        outer,
        evidence=tmp_path / "evidence",
        workspace=tmp_path / "CopilotProjects",
        launch=False,
    )
    assert report["reason"] == "LAUNCH_SKIPPED"
    assert report["ORIGINAL_UNTOUCHED"] is True
    assert als.read_bytes() == b"groove-als"
    assert wav.read_bytes() == b"groove-wav"
    assert Path(report["working_copy_path"]).is_dir()
    assert (Path(report["working_copy_path"]) / "Samples" / "Imported" / "kick.wav").read_bytes() == b"groove-wav"


def test_crash_recovery_discards_other_set() -> None:
    from copilot.importing.crash_recovery_v1 import recovered_set_name, recovery_action

    text = (
        'Live se ha cerrado inesperadamente mientras trabajabas en el Live Set '
        '"copilot_bootstrap_fixture.als". Deseas recuperar tu trabajo?'
    )
    assert recovered_set_name(text) == "copilot_bootstrap_fixture.als"
    assert recovery_action(text, r"C:\CopilotProjects\Abletunes - Groove Rider.als") == "discard"
    assert recovery_action(text, r"C:\x\copilot_bootstrap_fixture.als") == "recover"


def test_launcher_discards_same_set_after_force_kill() -> None:
    from copilot.importing.crash_recovery_v1 import (
        classify_live_dialog,
        recover_click_target,
    )

    recover = (
        'Live se ha cerrado inesperadamente mientras trabajabas en el Live Set '
        '"Abletunes - Groove Rider.als". ¿Deseas recuperar tu trabajo?'
    )
    fatal = (
        "A serious program error has occurred. "
        "Live will shut down after this message box is closed. "
        "Please restart Live and follow the instructions in the "
        "'Report a Crash' lesson that will appear in Live's Help View."
    )
    expected = r"C:\Users\lsper\CopilotProjects\Abletunes - Groove Rider Project\Abletunes - Groove Rider.als"
    assert classify_live_dialog(recover) == "RECOVER_WORK"
    assert classify_live_dialog(fatal) == "FATAL_ERROR"
    assert recover_click_target(recover, expected, recover_policy="open_command_line") == "discard"
    assert recover_click_target(recover, expected, recover_policy="match_expected") == "recover"
    assert recover_click_target(fatal, expected, recover_policy="open_command_line") == "accept"
