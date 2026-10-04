"""The direct kit reuses the existing musical plan, without Live in tests."""

from pathlib import Path

from copilot.core_v2.direct import is_owned_smoke_set
from copilot.integration.tech_house_kit_direct_v1 import make_batch
from copilot.producer.tech_house_kit_v1 import KIT, groove_plan
from copilot.schemas.session import ClipState, DeviceState, SessionState, TrackState


def test_direct_batch_is_exact_seven_role_plan() -> None:
    batch = make_batch(groove_plan())
    batch.validate()
    assert batch.tempo == 126.0 and batch.length_beats == 64.0
    assert [track.name for track in batch.tracks] == [f"KIT DIRECT V1 - {s.role}" for s in KIT]
    assert [track.source_name for track in batch.tracks] == [s.browser_name for s in KIT]
    assert [track.fallback_source_name for track in batch.tracks] == [s.fallback_browser_name for s in KIT]
    assert sum(len(track.notes) for track in batch.tracks) == 480


def test_only_exact_prior_smoke_can_be_reused(tmp_path: Path) -> None:
    defaults = [("1-MIDI", "midi"), ("2-MIDI", "midi"),
                ("3-Audio", "audio"), ("4-Audio", "audio")]
    tracks = [TrackState(stable_id=str(i), index=i, name=name, role=role)
              for i, (name, role) in enumerate(defaults)]
    for index, name in enumerate(("FAST V2 - Kick", "FAST V2 - Hat", "FAST V2 - Bass"), start=4):
        tracks.append(TrackState(stable_id=str(index), index=index, name=name, role="midi",
                                 devices=[DeviceState(stable_id=f"device-{index}", index=0, name=name)],
                                 clips=[ClipState(stable_id=f"clip-{index}", slot_index=0,
                                                  name=name, length_beats=16)]))
    session = SessionState(tracks=tracks)
    arrangement = [{"track_index": i} for i in (4, 5, 6)]
    path = str(tmp_path / "smoke.als")
    assert is_owned_smoke_set(session, arrangement, path, tmp_path)
    assert not is_owned_smoke_set(session, arrangement, path, tmp_path / "other")
    session.tracks[4].name = "User edits"
    assert not is_owned_smoke_set(session, arrangement, path, tmp_path)
