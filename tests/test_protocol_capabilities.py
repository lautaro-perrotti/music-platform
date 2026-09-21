from __future__ import annotations

import re
from pathlib import Path

import pytest

from copilot.daw.protocol import COMMAND_CAPABILITY


def _bridge_whitelist_commands() -> set[str]:
    bridge = Path("vendor/abletonmcp_remote_script/AbletonMCP/__init__.py")
    src = bridge.read_text(encoding="utf-8")
    m = re.search(r"elif command_type in \[(.*?)\]:", src, re.S)
    assert m, "bridge whitelist block not found"
    return set(re.findall(r'"([^\"]+)"', m.group(1)))


def test_all_bridge_whitelist_commands_are_mapped_in_protocol() -> None:
    whitelist = _bridge_whitelist_commands()
    mapped = set(COMMAND_CAPABILITY.keys())
    missing = sorted(whitelist - mapped)
    assert not missing, (
        "COMMAND_CAPABILITY is missing mapped bridge commands: "
        + ", ".join(missing)
    )


def test_core_transport_commands_have_non_read_capability() -> None:
    # Guardrail: mutating transport controls should not be mapped as session.read.
    mutating = {
        "start_playback",
        "stop_playback",
        "set_tempo",
        "set_current_song_time",
        "set_arrangement_loop",
    }
    for cmd in mutating:
        cap = COMMAND_CAPABILITY.get(cmd)
        assert cap and cap != "session.read", f"{cmd} mapped to unexpected capability {cap!r}"


def test_browser_sample_loading_is_advertised_as_a_typed_capability() -> None:
    from copilot.daw.protocol import CAPABILITIES

    assert "browser.load" in CAPABILITIES
    assert COMMAND_CAPABILITY["load_browser_item_by_path"] == "browser.load"


def test_browser_sample_path_rejects_filesystem_escape_before_rpc() -> None:
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.daw.adapter import DawError

    with pytest.raises(DawError, match="safe relative"):
        AbletonTcpAdapter().load_browser_item(0, "../outside.wav")
