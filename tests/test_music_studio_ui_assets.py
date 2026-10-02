from __future__ import annotations

from pathlib import Path


UI = Path(__file__).parents[1] / "src" / "copilot" / "studio" / "static" / "ui"


def test_music_studio_ui_layers_and_required_components_exist() -> None:
    for layer in ("atoms", "molecules", "organisms", "templates", "pages", "lib"):
        assert (UI / layer).is_dir()
    required = {
        "atoms": {"ms-button.js", "ms-status.js", "ms-waveform.js"},
        "molecules": {"ms-transport.js", "ms-ab-switch.js", "ms-stage-list.js"},
        "organisms": {"ms-top-bar.js", "ms-sidebar.js", "ms-candidate-card.js", "ms-in-doubt-panel.js"},
        "templates": {"ms-app-shell.js", "ms-overlay.js"},
    }
    for layer, names in required.items():
        assert names <= {path.name for path in (UI / layer).iterdir()}
    assert len(list((UI / "pages").glob("ms-page-*.js"))) == 28


def test_claude_source_is_the_visual_source_of_truth() -> None:
    source = UI.parents[4] / "docs" / "design" / "music-studio-v1" / "source"
    assert (source / "TopBar.dc.html").is_file()
    assert (source / "Sidebar.dc.html").is_file()
    assert (source / "Player.dc.html").is_file()
    assert (source / "Projects.dc.html").is_file()
    app = (UI / "app.js").read_text(encoding="utf-8")
    assert "/ui/app.css" not in app
    assert "/ui/tokens.css" not in app
    assert "/ui/base.css" not in app


def test_static_entrypoints_are_present() -> None:
    for name in ("index.html", "catalog.html", "catalog.js", "app.js", "page.html", "page.js"):
        assert (UI.parent / name if name == "index.html" else UI / name).is_file()


def test_real_drum_workbench_entrypoint_is_separate_from_mock_workspace() -> None:
    page = (UI / "drums.html").read_text(encoding="utf-8")
    script = (UI / "drums.js").read_text(encoding="utf-8")
    assert 'src="/ui/drums.js"' in page
    assert "REAL LOCAL ANALYSIS" in script
    assert "READ ONLY" in script
    assert "MOCK" not in script
    assert "api/drums/events" in script
    assert "api/ableton/status" in script
    assert "Connect Live Play / Stop events" in script
    assert "Drum workbench" in (UI / "organisms" / "ms-sidebar.js").read_text(encoding="utf-8")
