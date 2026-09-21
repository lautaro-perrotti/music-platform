"""Widen Copilot Audio Tap slot selector 0-8 without destroying C74 dialect.

A full JSON dump of the patcher produces standard JSON. Live then loads a device
shell whose live.numbox parameters are not enumerated. Always start from the
Max-saved V3 ptch bytes and apply surgical C74-format edits.
"""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAXPAT = ROOT / "Copilot Audio Tap.maxpat"
AMXD = ROOT / "Copilot Audio Tap.amxd"
MAX_SLOT = 8
LEGACY_OPEN = {0: "obj-open", 1: "obj-openk", 2: "obj-openb"}
V3_SEL = '"text" : "sel 0 1 2"'
V4_SEL = '"text" : "sel 0 1 2 3 4 5 6 7 8"'
GIT_AMXD = "devices/Copilot Audio Tap.amxd"
CAPTURE_DIR_TOKEN = "__COPILOT_CAPTURE_DIR__"


def extract_ptch(amxd: bytes) -> bytes:
    pos = 0
    while pos + 8 <= len(amxd):
        tag = amxd[pos : pos + 4]
        size = struct.unpack_from("<I", amxd, pos + 4)[0]
        body = amxd[pos + 8 : pos + 8 + size]
        if tag == b"ptch":
            return body
        pos += 8 + size
    raise ValueError("amxd has no ptch chunk")


def v3_baseline_ptch() -> bytes:
    raw = subprocess.check_output(["git", "show", f"HEAD:{GIT_AMXD}"], cwd=ROOT.parent)
    # Older committed templates used a developer-specific Windows path. Keep
    # the surgical upgrader portable even when its baseline comes from HEAD.
    return extract_ptch(raw).replace(
        b"D:/MusicCopilot/captures", CAPTURE_DIR_TOKEN.encode("ascii")
    )


def _c74_open_box(slot: int) -> str:
    name = f"_next_s{slot}.wav"
    y = 216.0 + (slot - 3) * 24.0
    return (
        ", \t\t\t{\n"
        '\t\t\t\t"box" : \t\t\t\t{\n'
        f'\t\t\t\t\t"id" : "obj-open{slot}",\n'
        '\t\t\t\t\t"maxclass" : "message",\n'
        '\t\t\t\t\t"numinlets" : 2,\n'
        '\t\t\t\t\t"numoutlets" : 1,\n'
        '\t\t\t\t\t"outlettype" : [ "" ],\n'
        f'\t\t\t\t\t"patching_rect" : [ 56.0, {y}, 290.0, 22.0 ],\n'
        f'\t\t\t\t\t"text" : "open {CAPTURE_DIR_TOKEN}/{name}"\n'
        "\t\t\t\t}\n"
        "\n"
        "\t\t\t}\n"
    )


def _c74_line(source_id: str, source_outlet: int, dest_id: str, dest_inlet: int = 0) -> str:
    return (
        ", \t\t\t{\n"
        '\t\t\t\t"patchline" : \t\t\t\t{\n'
        f'\t\t\t\t\t"destination" : [ "{dest_id}", {dest_inlet} ],\n'
        f'\t\t\t\t\t"source" : [ "{source_id}", {source_outlet} ]\n'
        "\t\t\t\t}\n"
        "\n"
        "\t\t\t}\n"
    )


def is_c74_dialect(text: str) -> bool:
    return '"patcher" :' in text and (V3_SEL in text or V4_SEL in text)


def apply_v4(text: str) -> str:
    text = text.replace("\r\n", "\n")
    if V4_SEL in text and '"parameter_mmax" : 8.0' in text and '"parameter_initial" : [ 4 ]' in text:
        return text
    if V3_SEL not in text:
        raise ValueError("refusing to edit a patcher that is not the V3 sel 0 1 2 baseline")
    if '"parameter_mmax" : 2.0' not in text:
        raise ValueError("Slot parameter_mmax 2.0 not found")
    if '"parameter_initial" : [ 3 ]' not in text:
        raise ValueError("TapProtocol initial 3 not found")

    text = text.replace('"parameter_mmax" : 2.0', '"parameter_mmax" : 8.0', 1)
    text = text.replace('"parameter_initial" : [ 3 ]', '"parameter_initial" : [ 4 ]', 1)
    text = text.replace(
        '\t\t\t\t\t"numoutlets" : 4,\n'
        '\t\t\t\t\t"outlettype" : [ "bang", "bang", "bang", "" ],\n'
        '\t\t\t\t\t"patching_rect" : [ 200.0, 112.0, 73.0, 22.0 ],\n'
        '\t\t\t\t\t"text" : "sel 0 1 2"',
        '\t\t\t\t\t"numoutlets" : 10,\n'
        '\t\t\t\t\t"outlettype" : [ "bang", "bang", "bang", "bang", "bang", "bang", "bang", "bang", "bang", "" ],\n'
        '\t\t\t\t\t"patching_rect" : [ 200.0, 112.0, 160.0, 22.0 ],\n'
        '\t\t\t\t\t"text" : "sel 0 1 2 3 4 5 6 7 8"',
        1,
    )
    if V4_SEL not in text:
        raise ValueError("sel rewrite failed")

    boxes_close = "\t\t\t}\n ],\n\t\t\"lines\""
    idx = text.find(boxes_close)
    if idx < 0:
        raise ValueError("boxes close marker not found")
    extra_boxes = "".join(_c74_open_box(slot) for slot in range(3, MAX_SLOT + 1))
    text = text[:idx] + "\t\t\t}\n" + extra_boxes + " ],\n\t\t\"lines\"" + text[idx + len(boxes_close) :]

    lines_close = "\t\t\t}\n ]\n\t}\n\n}"
    lidx = text.rfind(lines_close)
    if lidx < 0:
        raise ValueError("lines close marker not found")
    extra_lines = []
    for slot in range(3, MAX_SLOT + 1):
        open_id = LEGACY_OPEN.get(slot, f"obj-open{slot}")
        extra_lines.append(_c74_line("obj-slotsel", slot, open_id, 0))
        extra_lines.append(_c74_line(open_id, 0, "obj-rec", 0))
    text = text[:lidx] + "\t\t\t}\n" + "".join(extra_lines) + " ]\n\t}\n\n}"
    return text


def write_crlf(path: Path, text: str) -> None:
    normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(normalized.encode("utf-8"))


def build_amxd() -> int:
    import importlib.util

    spec = importlib.util.spec_from_file_location("copilot_build_amxd", ROOT / "build_amxd.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ROOT / 'build_amxd.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.build_audio_effect_amxd(MAXPAT, AMXD))


def main() -> None:
    baseline = v3_baseline_ptch().decode("utf-8")
    if '"patcher" :' not in baseline:
        raise SystemExit("HEAD amxd ptch is not Cycling '74 dialect")
    updated = apply_v4(baseline)
    write_crlf(MAXPAT, updated)
    size = build_amxd()
    print(f"updated {MAXPAT}")
    print(f"wrote {AMXD} ({size} bytes)")
    print("dialect=c74")
    print("tap_protocol=4")
    print("slot_mmax=8")
    print("sel=0..8")


if __name__ == "__main__":
    main()
