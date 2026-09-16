from __future__ import annotations

from copilot.daw.mock import MockAbletonAdapter


def test_stable_id_survives_insert_before() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track("AI Test")
    first = daw.snapshot().track_by_name("AI Test")
    assert first is not None
    original = first.stable_id
    daw.create_midi_track("Inserted", index=0)
    after = daw.snapshot()
    moved = after.track_by_name("AI Test")
    inserted = after.track_by_name("Inserted")
    assert moved is not None and inserted is not None
    assert moved.index == 1
    assert inserted.index == 0
    assert moved.stable_id == original
    assert inserted.stable_id != original


def test_stable_id_survives_rename_when_fingerprint_unique() -> None:
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track("AI Test")
    before = daw.snapshot().track_by_name("AI Test")
    assert before is not None
    daw.set_track_name(before.index, "Renamed")
    after = daw.snapshot()
    renamed = after.track_by_name("Renamed")
    assert renamed is not None
    assert renamed.stable_id == before.stable_id
    assert after.track_by_name("AI Test") is None
