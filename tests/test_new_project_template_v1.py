from __future__ import annotations

import gzip
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from copilot.importing.new_project_v1 import (
    open_new_project,
    prepare_new_project,
    reopen_and_verify_saved_project,
    save_new_project_via_windows_ui,
    verify_saved_project,
)


@pytest.fixture
def tmp_path() -> Path:
    # Keep test artifacts inside this worktree, never in the system temp dir.
    root = Path.cwd() / f".new-project-test-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _template(path: Path, tracks: str = "") -> Path:
    path.write_bytes(gzip.compress(f"<Ableton><LiveSet><Tracks>{tracks}</Tracks></LiveSet></Ableton>".encode()))
    return path


class _Bridge:
    def __init__(self, path: Path, *, count: int = 0, browser: bool = False):
        self.path = path
        self.count = count
        self.handshake_info = {
            "protocol_version": "1",
            "capabilities": ["session.read"] + (["browser.load"] if browser else []),
        }

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def get_session_path(self) -> dict:
        return {"path": str(self.path)}

    def get_session_info(self) -> dict:
        return {"track_count": self.count}


def _ready(path: Path, pid: int = 10) -> dict:
    return {
        "status": "PROJECT_READY",
        "same_process_pid": pid,
        "session": {
            "project_path": str(path),
            "project_identity": "path-bound-id",
            "track_count": 0,
        },
    }


def test_isolated_unique_copies_and_manifest(tmp_path: Path) -> None:
    template = _template(tmp_path / "Blank.als")
    workspace = tmp_path / "authorized"
    a = prepare_new_project(template_als=template, workspace=workspace)
    b = prepare_new_project(template_als=template, workspace=workspace)
    assert a["status"] == b["status"] == "CREATED"
    assert a["working_als"] != b["working_als"]
    assert Path(a["working_als"]).read_bytes() == template.read_bytes()
    assert json.loads((Path(a["working_root"]) / "copilot_import.json").read_text()) == a


def test_rejects_nonempty_malformed_and_sample_templates(tmp_path: Path) -> None:
    for tracks in ("<MidiTrack/>", "<AudioTrack/>", "<GroupTrack/>",
                   "<ReturnTrack><DeviceChain><Devices><Reverb/></Devices></DeviceChain></ReturnTrack>",
                   "<ReturnTrack><Clip/></ReturnTrack>"):
        template = _template(tmp_path / "Blank.als", tracks)
        assert prepare_new_project(template_als=template, workspace=tmp_path / "work")["status"] == "BLOCKED"
    (tmp_path / "Blank.als").write_bytes(b"not a saved Live set")
    assert prepare_new_project(template_als=tmp_path / "Blank.als", workspace=tmp_path / "work")["status"] == "BLOCKED"
    _template(tmp_path / "Blank.als")
    (tmp_path / "Samples").mkdir()
    (tmp_path / "Samples" / "beat.wav").write_bytes(b"audio")
    assert prepare_new_project(template_als=tmp_path / "Blank.als", workspace=tmp_path / "work")["reason"] == "TEMPLATE_SAMPLES_NOT_EMPTY"
    assert not (tmp_path / "work").exists()


def test_empty_return_tracks_do_not_count_as_musical_tracks(tmp_path: Path) -> None:
    template = _template(
        tmp_path / "Blank.als",
        "<ReturnTrack><DeviceChain><Devices/></DeviceChain></ReturnTrack>",
    )
    copy = prepare_new_project(template_als=template, workspace=tmp_path / "work")
    assert copy["status"] == "CREATED"
    target = Path(copy["working_als"])
    assert open_new_project(
        copy, readiness=lambda **_: _ready(target), daw_factory=lambda: _Bridge(target),
    )["status"] == "OPENED_EMPTY"


def test_open_requires_exact_live_path_zero_tracks_and_real_advertisement(tmp_path: Path) -> None:
    copy = prepare_new_project(template_als=_template(tmp_path / "Blank.als"), workspace=tmp_path / "work")
    target = Path(copy["working_als"])
    opened = open_new_project(copy, readiness=lambda **_: _ready(target), daw_factory=lambda: _Bridge(target))
    assert opened["status"] == "OPENED_EMPTY"
    assert opened["browser_load_advertised"] is False
    assert opened["browser_load_verified"] is False
    assert open_new_project(copy, readiness=lambda **_: _ready(target), daw_factory=lambda: _Bridge(target, browser=True))["browser_load_advertised"] is True
    other = tmp_path / "other" / target.name
    assert open_new_project(copy, readiness=lambda **_: _ready(other), daw_factory=lambda: _Bridge(target))["status"] == "BLOCKED"
    assert open_new_project(copy, readiness=lambda **_: _ready(target), daw_factory=lambda: _Bridge(target, count=1))["status"] == "BLOCKED"
    target.write_bytes(b"tampered")
    assert open_new_project(copy, readiness=lambda **_: _ready(target), daw_factory=lambda: _Bridge(target))["reason"] == "COPY_NOT_VERIFIED"


def test_save_requires_disk_change_and_distinct_reopened_process(tmp_path: Path) -> None:
    copy = prepare_new_project(template_als=_template(tmp_path / "Blank.als"), workspace=tmp_path / "work")
    target = Path(copy["working_als"])
    opened = open_new_project(copy, readiness=lambda **_: _ready(target), daw_factory=lambda: _Bridge(target))
    assert verify_saved_project(opened, reopened=_ready(target, pid=11))["reason"] == "SAVE_NOT_OBSERVED_ON_DISK"
    target.write_bytes(target.read_bytes() + b"saved")
    assert verify_saved_project(opened, reopened=_ready(target))["status"] == "BLOCKED"
    assert verify_saved_project(opened, reopened=_ready(tmp_path / "foreign.als", pid=11))["status"] == "BLOCKED"
    assert verify_saved_project(opened, reopened=_ready(target, pid=11))["status"] == "SAVED_REOPENED"
    assert reopen_and_verify_saved_project(
        opened, readiness=lambda **_: _ready(target, pid=11), daw_factory=lambda: _Bridge(target)
    )["status"] == "SAVED_REOPENED"
    assert reopen_and_verify_saved_project(
        opened, readiness=lambda **_: _ready(target, pid=11), daw_factory=lambda: _Bridge(target, count=1)
    )["reason"] == "REOPEN_BRIDGE_READBACK_MISMATCH"


class _UI:
    def __init__(self, target: Path, *, modal: bool = False, focus: bool = True,
                 write: bool = True, close: bool = True):
        self.target = target
        self.modal = modal
        self.can_focus = focus
        self.write = write
        self.can_close = close
        self.foreground = False
        self.alive = True
        self.sent = False
        self.closed = False

    def observe(self, pid: int) -> dict:
        windows = [{"hwnd": 123, "title": f"{self.target.stem} - Ableton Live 12",
                    "enabled": True}]
        if self.modal:
            windows.append({"hwnd": 124, "title": "Save As", "enabled": True})
        return {
            "alive": self.alive, "process_token": 4567,
            "executable": r"C:\Program Files\Ableton Live\Live.exe",
            "windows": windows, "foreground_hwnd": 123 if self.foreground else 0,
            "foreground_pid": pid if self.foreground else 0,
        }

    def focus(self, hwnd: int) -> bool:
        self.foreground = self.can_focus
        return self.can_focus

    def send_save(self, hwnd: int, pid: int) -> bool:
        self.sent = True
        if self.write:
            stat = self.target.stat()
            self.target.write_bytes(self.target.read_bytes() + b"new save")
            os.utime(self.target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        return True

    def close_window(self, hwnd: int) -> bool:
        self.closed = True
        self.alive = not self.can_close
        return True


def _owned_opened(tmp_path: Path) -> tuple[dict, Path]:
    copy = prepare_new_project(template_als=_template(tmp_path / "Blank.als"),
                               workspace=tmp_path / "work")
    target = Path(copy["working_als"])
    ready = _ready(target)
    ready["launch"] = {
        "launch": "started",
        "process_lifecycle": {"owned": True, "pid": 10},
    }
    ready["environment"] = {"ableton": {"exe_path": r"C:\Program Files\Ableton Live\Live.exe"}}
    return open_new_project(copy, readiness=lambda **_: ready,
                            daw_factory=lambda: _Bridge(target)), target


def test_owned_windows_ui_save_and_reopen(tmp_path: Path) -> None:
    opened, target = _owned_opened(tmp_path)
    ui = _UI(target)
    result = save_new_project_via_windows_ui(
        opened, ui=ui, readiness=lambda **_: _ready(target, pid=11),
        daw_factory=lambda: _Bridge(target), timeout_s=0.2,
    )
    assert result["status"] == "SAVED_REOPENED"
    assert result["save_method"] == "OWNED_WINDOWS_UI"
    assert ui.sent and ui.closed


def test_windows_ui_save_fails_closed_before_or_after_keystroke(tmp_path: Path) -> None:
    opened, target = _owned_opened(tmp_path)
    cases = [
        (_UI(target, modal=True), "WINDOW_OR_MODAL_AMBIGUOUS", False),
        (_UI(target, focus=False), "FOREGROUND_NOT_VERIFIED", False),
        (_UI(target, write=False), "SAVE_NOT_OBSERVED_ON_DISK", True),
        (_UI(target, close=False), "OWNED_WINDOW_DID_NOT_CLOSE", True),
    ]
    for ui, reason, sent in cases:
        result = save_new_project_via_windows_ui(
            opened, ui=ui, readiness=lambda **_: _ready(target, pid=11),
            daw_factory=lambda: _Bridge(target), timeout_s=0.01,
            sleep=lambda _: None,
        )
        assert result["reason"] == reason
        assert ui.sent is sent
        if not sent:
            assert not ui.closed
    not_owned = {**opened, "readiness": {**opened["readiness"], "launch": {}}}
    ui = _UI(target)
    assert save_new_project_via_windows_ui(not_owned, ui=ui)["reason"] == "OWNED_LIVE_PROCESS_REQUIRED"
    assert not ui.sent


def test_windows_ui_save_blocks_foreign_window_and_reopen_mismatch(tmp_path: Path) -> None:
    opened, target = _owned_opened(tmp_path)
    ui = _UI(target)
    original = ui.observe

    def foreign_executable(pid: int) -> dict:
        return {**original(pid), "executable": r"C:\Other\Live.exe"}

    ui.observe = foreign_executable
    assert save_new_project_via_windows_ui(opened, ui=ui,
                                           daw_factory=lambda: _Bridge(target))["reason"] == "WINDOW_OR_MODAL_AMBIGUOUS"
    assert not ui.sent
    ui = _UI(target)
    other = tmp_path / "foreign.als"
    result = save_new_project_via_windows_ui(
        opened, ui=ui, readiness=lambda **_: _ready(other, pid=11),
        daw_factory=lambda: _Bridge(target), timeout_s=0.2,
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "REOPEN_IDENTITY_MISMATCH"
    assert ui.sent and ui.closed
