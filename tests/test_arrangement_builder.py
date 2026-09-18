"""Arrangement builder pure-logic tests (no DAW)."""
from copilot.musicplan.arrangement_builder import (
    LOOP_DIVISION,
    _loop_division,
    build_arrangement,
    section_timeline,
)
from copilot.musicplan.arrangement import TECH_HOUSE_ARRANGEMENT


def test_section_timeline_covers_96_bars():
    tl = section_timeline()
    names = [s["name"] for s in tl]
    assert names == ["INTRO", "GROOVE", "BASS", "DROP", "BREAK", "DROP2", "OUTRO"]
    total = tl[-1]["start_beat"] + tl[-1]["length_beats"]
    assert total == 384.0  # 96 bars * 4 beats


def test_section_offsets_are_contiguous():
    tl = section_timeline()
    cursor = 0.0
    for s in tl:
        assert s["start_beat"] == cursor
        cursor += s["length_beats"]


def test_loop_division_defaults():
    assert _loop_division("Kick") == 1.0
    assert _loop_division("Clap") == 2.0
    assert _loop_division("Closed Hat") == 0.5
    assert _loop_division("Bass") == 4.0  # default 1 bar
    assert _loop_division("Texture") == 4.0


def test_build_arrangement_counts_and_skips():
    class FakeTrack:
        def __init__(self, name, index, clips):
            self.name = name
            self.index = index
            self.clips = clips

    class FakeSession:
        def __init__(self):
            self.tracks = [
                FakeTrack("Kick", 0, [object()]),
                FakeTrack("Clap", 1, [object()]),
                FakeTrack("Bass", 2, [object()]),
                FakeTrack("Sax", 3, [object()]),
                FakeTrack("Ghost", 4, []),  # no clip
            ]

    class FakeDaw:
        def __init__(self):
            self.loop_calls = []
            self.dup_calls = []
            self.mute_calls = []

        def set_track_mute(self, i, m):
            self.mute_calls.append((i, m))
            return {}

        def set_clip_loop(self, i, ci, s, e, l):
            self.loop_calls.append((i, e))
            return {}

        def duplicate_clip_to_arrangement(self, i, ci, t, length):
            self.dup_calls.append((i, t, length))
            return {"copies": int(length // 4)}

    daw = FakeDaw()
    session = FakeSession()
    r = build_arrangement(daw, session=session, arrangement=TECH_HOUSE_ARRANGEMENT)

    assert r["unmuted"] == 4  # only tracks with clips
    assert r["looped"] == 4
    assert r["placed"] > 0
    assert len(r["skipped"]) > 0  # sections reference tracks absent from the fake session
