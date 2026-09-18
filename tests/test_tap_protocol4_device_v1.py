"""TAP_PROTOCOL4_LIVE_ACTIVATION_V1 — device artifact, not Live jobs."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from copilot.audio.capture_scalability_v2 import staging_for_slot

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "devices"
MAXPAT = DEVICES / "Copilot Audio Tap.maxpat"
AMXD = DEVICES / "Copilot Audio Tap.amxd"


def _ptch(amxd: bytes) -> bytes:
    pos = 0
    while pos + 8 <= len(amxd):
        tag = amxd[pos : pos + 4]
        size = struct.unpack_from("<I", amxd, pos + 4)[0]
        body = amxd[pos + 8 : pos + 8 + size]
        if tag == b"ptch":
            return body
        pos += 8 + size
    raise AssertionError("amxd missing ptch")


def test_canonical_amxd_wraps_current_maxpat() -> None:
    maxpat = MAXPAT.read_bytes()
    ptch = _ptch(AMXD.read_bytes())
    assert ptch == maxpat
    assert maxpat.startswith(b'{\r\n\t"patcher" :')


def test_v4_patcher_keeps_c74_dialect_and_live_params() -> None:
    text = MAXPAT.read_text(encoding="utf-8")
    assert '"patcher" :' in text
    assert '"parameter_longname" : "Slot"' in text
    assert '"parameter_enable" : 1' in text
    assert '"varname" : "Slot"' in text
    assert '"parameter_mmax" : 8.0' in text
    assert '"parameter_mmin" : 0.0' in text
    assert '"parameter_type" : 1' in text
    assert '"parameter_longname" : "TapProtocol"' in text
    assert '"parameter_initial" : [ 4 ]' in text
    assert '"text" : "sel 0 1 2 3 4 5 6 7 8"' in text
    assert '"maxclass" : "live.numbox"' in text
    assert '"patcher":' not in text


def test_v4_file_mapping_preserves_legacy_and_unique_slots() -> None:
    text = MAXPAT.read_text(encoding="utf-8")
    expected = {
        0: "_next.wav",
        1: "_next_kick.wav",
        2: "_next_bass.wav",
        3: "_next_s3.wav",
        4: "_next_s4.wav",
        5: "_next_s5.wav",
        6: "_next_s6.wav",
        7: "_next_s7.wav",
        8: "_next_s8.wav",
    }
    for slot, name in expected.items():
        assert name in text
        assert staging_for_slot(slot) == name
    assert len(set(expected.values())) == 9


def test_extend_script_does_not_json_dump() -> None:
    source = (DEVICES / "extend_tap_slots_v2.py").read_text(encoding="utf-8")
    assert "json.dumps(" not in source
    assert "v3_baseline_ptch" in source


def test_v4_slot_table_is_unique() -> None:
    text = MAXPAT.read_text(encoding="utf-8")
    table = {
        0: ("obj-open", "_next.wav", "Main"),
        1: ("obj-openk", "_next_kick.wav", "source slot 1"),
        2: ("obj-openb", "_next_bass.wav", "source slot 2"),
        3: ("obj-open3", "_next_s3.wav", "source slot 3"),
        4: ("obj-open4", "_next_s4.wav", "source slot 4"),
        5: ("obj-open5", "_next_s5.wav", "source slot 5"),
        6: ("obj-open6", "_next_s6.wav", "source slot 6"),
        7: ("obj-open7", "_next_s7.wav", "source slot 7"),
        8: ("obj-open8", "_next_s8.wav", "source slot 8"),
    }
    for slot, (obj_id, filename, _role) in table.items():
        assert f'"id" : "{obj_id}"' in text
        assert filename in text
        assert staging_for_slot(slot) == filename


def test_amxd_sha_is_stable_wrapper() -> None:
    digest = hashlib.sha256(AMXD.read_bytes()).hexdigest()
    assert len(digest) == 64
    assert AMXD.stat().st_size == 8 + 4 + 8 + 4 + 8 + len(MAXPAT.read_bytes())
