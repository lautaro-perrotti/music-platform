from __future__ import annotations

import hashlib
import json
from pathlib import Path

from copilot.daw.install_remote_script import (
    MANIFEST_NAME,
    SCRIPT_FOLDER,
    install_remote_script,
    uninstall_remote_script,
)
from copilot.installing.second_machine_installer_v1 import (
    CONFIG_EXAMPLE_REL,
    LOCAL_ENV_REL,
    credential_status,
    discover_environment,
    ensure_config_template,
    inspect_control_surface,
    run_installer,
    uninstall_copilot_owned,
    verify_imports,
)
from copilot.importing.m4l_runtime_v1 import ensure_m4l_runtime, runtime_paths


def test_discovery_reports_windows_fields() -> None:
    env = discover_environment()
    assert env["architecture"]
    assert env["python"]["version"]
    assert env["python"]["supported_target"] == "3.12"
    assert env["documents_path"]
    assert env["repository_path"]
    assert "repository" in env["permissions"]
    blob = json.dumps(env)
    assert "sk-" not in blob
    assert "COPILOT_REASONING_API_KEY=" not in blob


def test_installer_sources_have_no_developer_hardcodes() -> None:
    files = [
        Path("src/copilot/installing/second_machine_installer_v1.py"),
        Path("src/copilot/daw/install_remote_script.py"),
        Path("scripts/install-copilot.ps1"),
        Path("scripts/uninstall-copilot.ps1"),
        Path("install.bat"),
        Path("docs/installation/WINDOWS_INSTALL.md"),
        Path("config/copilot.env.example"),
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "lsper" not in text
        assert "D:\\MusicCopilot" not in text
        assert "D:\\Ableton" not in text
        assert "sk-" not in text


def test_remote_script_install_is_idempotent_and_owned(tmp_path: Path) -> None:
    dest = tmp_path / "Remote Scripts"
    first = install_remote_script(dest_parent=dest)
    assert first["status"] == "INSTALLED"
    assert first["REMOTE_SCRIPT"] == "INSTALLED"
    script = dest / SCRIPT_FOLDER / "__init__.py"
    assert script.is_file()
    assert (dest / SCRIPT_FOLDER / MANIFEST_NAME).is_file()
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    assert digest == first["sha256"]
    second = install_remote_script(dest_parent=dest)
    assert second["status"] == "ALREADY_CURRENT"
    copies = list(dest.rglob("__init__.py"))
    assert len(copies) == 1


def test_remote_script_blocks_unknown_user_file(tmp_path: Path) -> None:
    dest_dir = tmp_path / "Remote Scripts" / SCRIPT_FOLDER
    dest_dir.mkdir(parents=True)
    target = dest_dir / "__init__.py"
    target.write_text("not-copilot", encoding="utf-8")
    result = install_remote_script(dest_parent=tmp_path / "Remote Scripts")
    assert result["status"] == "BLOCKED"
    assert result["error"] == "USER_OWNED_CONFLICT"
    assert target.read_text(encoding="utf-8") == "not-copilot"


def test_remote_script_updates_owned_old_hash(tmp_path: Path) -> None:
    dest = tmp_path / "Remote Scripts"
    first = install_remote_script(dest_parent=dest)
    script = dest / SCRIPT_FOLDER / "__init__.py"
    script.write_text("# old copilot-owned copy\n", encoding="utf-8")
    updated = install_remote_script(dest_parent=dest)
    assert updated["status"] == "UPDATED"
    assert hashlib.sha256(script.read_bytes()).hexdigest() == first["sha256"]


def test_config_template_created_then_preserved(tmp_path: Path) -> None:
    example = tmp_path / CONFIG_EXAMPLE_REL
    example.parent.mkdir(parents=True)
    example.write_text("COPILOT_REASONING_API_KEY=\n", encoding="utf-8")
    first = ensure_config_template(repo=tmp_path)
    assert first["status"] == "CREATED"
    local = tmp_path / LOCAL_ENV_REL
    local.write_text("COPILOT_REASONING_API_KEY=keep-me\n", encoding="utf-8")
    second = ensure_config_template(repo=tmp_path)
    assert second["status"] == "PRESERVED"
    assert "keep-me" in local.read_text(encoding="utf-8")


def test_missing_credentials_do_not_block_and_are_redacted(tmp_path: Path) -> None:
    creds = credential_status(repo=tmp_path)
    assert creds["blocks_install"] is False
    assert "COPILOT_REASONING_API_KEY" in creds["expected_keys"]
    assert creds["values_redacted"] is True
    blob = json.dumps(creds)
    assert "sk-" not in blob


def test_control_surface_cannot_autoload() -> None:
    report = inspect_control_surface()
    assert report["loads_without_control_surface"] is False
    assert "ControlSurface" in report["reason"]
    assert report["steps"]


def test_uninstall_removes_only_copilot_owned_remote_script(tmp_path: Path) -> None:
    dest = tmp_path / "Remote Scripts"
    install_remote_script(dest_parent=dest)
    other = dest / "UserScript" / "__init__.py"
    other.parent.mkdir()
    other.write_text("user", encoding="utf-8")
    removed = uninstall_remote_script(dest_parent=dest)
    assert (dest / SCRIPT_FOLDER).exists() is False
    assert other.is_file()
    assert removed["removed"]


def test_isolated_installer_second_run_is_idempotent(tmp_path: Path) -> None:
    example = tmp_path / CONFIG_EXAMPLE_REL
    example.parent.mkdir(parents=True)
    example.write_text(Path("config/copilot.env.example").read_text(encoding="utf-8"), encoding="utf-8")
    library = tmp_path / "User Library"
    (library / "Presets").mkdir(parents=True)
    remote = tmp_path / "Remote Scripts"
    python_env = {
        "status": "ALREADY_CURRENT",
        "venv_status": "ALREADY_CURRENT",
        "deps_status": "ALREADY_CURRENT",
        "version": "3.12.0",
        "executable": "python",
    }
    first = run_installer(
        repo=tmp_path,
        evidence=tmp_path / "logs",
        python_env=python_env,
        remote_dest_parent=remote,
        user_library=library,
    )
    assert first["REMOTE_SCRIPT"] == "INSTALLED"
    assert first["M4L RUNTIME"] == "INSTALLED"
    assert first["CONFIG"] == "CREATED"
    assert first["SECRETS NOT COPIED"] is True
    assert first["NO MUSICAL WRITE"] is True
    local = tmp_path / LOCAL_ENV_REL
    local.write_text("COPILOT_REASONING_API_KEY=keep-secret\n", encoding="utf-8")
    second = run_installer(
        repo=tmp_path,
        evidence=tmp_path / "logs2",
        python_env=python_env,
        remote_dest_parent=remote,
        user_library=library,
    )
    assert second["REMOTE_SCRIPT"] == "ALREADY_CURRENT"
    assert second["M4L RUNTIME"] == "ALREADY_CURRENT"
    assert second["CONFIG"] == "PRESERVED"
    assert local.read_text(encoding="utf-8") == "COPILOT_REASONING_API_KEY=keep-secret\n"
    assert len(list(remote.rglob("__init__.py"))) == 1
    assert len(list(library.rglob("Copilot Audio Tap.amxd"))) == 1
    blob = json.dumps(second)
    assert "keep-secret" not in blob
    assert "sk-" not in blob


def test_uninstall_skips_unowned_m4l(tmp_path: Path) -> None:
    library = tmp_path / "User Library"
    (library / "Presets").mkdir(parents=True)
    paths = runtime_paths(library)
    paths["device"].parent.mkdir(parents=True)
    paths["device"].write_bytes(b"user-owned")
    out = uninstall_copilot_owned(
        repo=tmp_path,
        remove_venv=False,
        remove_config=False,
        remote_dest_parent=tmp_path / "Remote Scripts",
        user_library=library,
    )
    assert paths["device"].is_file()
    assert paths["device"].read_bytes() == b"user-owned"


def test_verify_imports_include_project() -> None:
    report = verify_imports()
    assert report["ok"] is True
    assert "copilot" in report["loaded"]
