from __future__ import annotations

import re
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


def test_ui_colors_are_tokenized_outside_tokens_file() -> None:
    hex_color = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    offenders = []
    for path in UI.rglob("*"):
        if path.is_file() and path.name != "tokens.css":
            if hex_color.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path))
    assert offenders == []


def test_static_entrypoints_are_present() -> None:
    for name in ("index.html", "catalog.html", "catalog.js", "app.js", "page.html", "page.js"):
        assert (UI.parent / name if name == "index.html" else UI / name).is_file()
