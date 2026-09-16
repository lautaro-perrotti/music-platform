"""Wrap a Max patcher JSON as a Max Audio Effect .amxd."""

from __future__ import annotations

import struct
from pathlib import Path

AUDIO_EFFECT = b"aaaa"


def build_audio_effect_amxd(maxpat_path: Path, amxd_path: Path) -> int:
    json_bytes = maxpat_path.read_bytes()
    header = (
        b"ampf"
        + struct.pack("<I", 4)
        + AUDIO_EFFECT
        + b"meta"
        + struct.pack("<I", 4)
        + struct.pack("<I", 1)
        + b"ptch"
        + struct.pack("<I", len(json_bytes))
    )
    payload = header + json_bytes
    amxd_path.write_bytes(payload)
    return len(payload)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    src = root / "Copilot Audio Tap.maxpat"
    dest = root / "Copilot Audio Tap.amxd"
    size = build_audio_effect_amxd(src, dest)
    print(f"wrote {dest} ({size} bytes)")
