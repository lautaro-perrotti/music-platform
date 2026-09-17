from __future__ import annotations

from copilot.audio.generic_source_isolation_v1 import (
    inventory_generic_sources,
    select_activity_region,
)
from copilot.daw.object_ref import PersistentObjectRef
from copilot.schemas.session import (
    DeviceState,
    MixerState,
    RoutingState,
    SessionState,
    TrackState,
)


def _track(
    index: int,
    name: str,
    *,
    role: str = "audio",
    grouped: bool = False,
    foldable: bool = False,
    mute: bool = False,
    devices: list[DeviceState] | None = None,
) -> TrackState:
    return TrackState(
        stable_id=f"id_{index}",
        index=index,
        name=name,
        role=role,  # type: ignore[arg-type]
        grouped=grouped,
        foldable=foldable,
        mixer=MixerState(mute=mute),
        routing=RoutingState(),
        devices=devices or [DeviceState(stable_id=f"d{index}", index=0, name="Simpler")],
    )


def test_generic_inventory_does_not_require_pista_names() -> None:
    session = SessionState(
        project_identity="p1",
        project_token="pt",
        tracks=[
            _track(0, "Audio Bed", role="audio"),
            _track(1, "Keys", role="midi"),
            _track(2, "Drums Group", role="audio", foldable=True),
            _track(3, "Pad Lane", role="midi", grouped=True),
            _track(4, "Muted Pad", role="audio", mute=True),
            _track(5, "Empty Lane", role="audio", devices=[]),
            _track(6, "Copilot Capture", role="audio"),
        ],
    )
    clips = [
        {"track": "Audio Bed", "group": None, "start_qn": 0.0, "end_qn": 16.0},
        {"track": "Keys", "group": None, "start_qn": 8.0, "end_qn": 24.0},
        {"track": "Pad Lane", "group": "Drums Group", "start_qn": 0.0, "end_qn": 8.0},
        {"track": "Muted Pad", "group": None, "start_qn": 0.0, "end_qn": 8.0},
    ]
    out = inventory_generic_sources(session=session, clips=clips, start_qn=0.0, end_qn=16.0)
    names = {row["display_name"] for row in out["sources"]}
    assert "Copilot Capture" not in names
    assert "Drums" not in names
    assert "Rose Bass" not in names
    assert "Sub Sub Bass" not in names
    assert "Kick 808 Deep" not in names
    by_name = {row["display_name"]: row for row in out["sources"]}
    assert by_name["Audio Bed"]["arrangement_material"] == "HAS_MATERIAL"
    assert by_name["Empty Lane"]["arrangement_material"] == "NO_MATERIAL"
    assert by_name["Muted Pad"]["capture_eligibility"]["eligible"] is False
    assert by_name["Muted Pad"]["capture_eligibility"]["reason"] == "muted_source"
    assert PersistentObjectRef.model_validate(by_name["Keys"]["ref"])
    assert by_name["Keys"]["resolve_status"] == "RESOLVED"
    assert by_name["Audio Bed"]["post_mixer_source"] is True


def test_unknown_without_clips() -> None:
    session = SessionState(
        project_identity="p1",
        tracks=[_track(0, "Lead")],
    )
    out = inventory_generic_sources(session=session, clips=[], start_qn=0.0, end_qn=32.0)
    assert out["sources"][0]["arrangement_material"] == "UNKNOWN"


def test_select_activity_region() -> None:
    region = select_activity_region(
        [{"track": "A", "start_qn": 17.2, "end_qn": 20.0}]
    )
    assert region is not None
    assert region["start_qn"] == 16.0
    assert region["end_qn"] == 48.0
