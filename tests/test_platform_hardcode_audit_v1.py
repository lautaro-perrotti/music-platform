from __future__ import annotations

import ast
from pathlib import Path

import pytest

import copilot.audio.live_capture as live_capture
import copilot.importing.m4l_runtime_v1 as m4l_runtime
from copilot.audio.capture_scalability_v2 import discover_capacity
from copilot.audio.cross_project_bootstrap_v1 import (
    discover_topology,
    missing_topology,
)
from copilot.audio.generic_source_isolation_v1 import inventory_generic_sources
from copilot.audio.project_ready_v1 import generic_preflight
from copilot.daw.object_ref import ref_from_track, resolve_track
from copilot.daw.state_errors import StateTrustError
from copilot.schemas.session import (
    DeviceState,
    MixerState,
    RoutingState,
    SessionState,
    TrackState,
    TransportState,
)


ROOT = Path(__file__).parents[1]


def _user(name: str, *, stable_id: str, role: str = "audio") -> TrackState:
    return TrackState(
        stable_id=stable_id,
        index=0,
        name=name,
        role=role,  # type: ignore[arg-type]
        mixer=MixerState(),
    )


def _infra(name: str, slot: int, index: int) -> TrackState:
    return TrackState(
        stable_id=f"infra-{slot}",
        index=index,
        name=name,
        role="audio",
        devices=[
            DeviceState(
                stable_id=f"tap-{slot}",
                index=0,
                name="Copilot Audio Tap",
            )
        ],
        routing=RoutingState(
            input_type="Resampling",
            output_type="Sends Only",
            monitoring="off",
        ),
    )


def _ready_session(user_names: list[str]) -> SessionState:
    tracks = [
        _user(name, stable_id=f"user-{i}")
        for i, name in enumerate(user_names)
    ]
    tracks.extend(
        [
            _infra("Copilot Capture", 1, len(tracks)),
            _infra("Copilot Capture Bass", 2, len(tracks) + 1),
        ]
    )
    return SessionState(
        project_path="C:/controlled/random-project/song.als",
        project_name="random-project",
        project_identity="controlled-project",
        project_token="target-token",
        audible_token="audible-token",
        transport=TransportState(tempo=97.0, signature_numerator=3, signature_denominator=4),
        tracks=tracks,
    )


def _ready_discovery(session: SessionState) -> dict:
    inventory = [
        {
            "track_name": "MASTER",
            "track_index": -1,
            "slot": 0,
            "rec": 0.0,
            "tap_protocol": 4,
        },
        {
            "track_name": "Copilot Capture",
            "track_index": session.track_by_name("Copilot Capture").index,
            "slot": 1,
            "rec": 0.0,
            "tap_protocol": 4,
        },
        {
            "track_name": "Copilot Capture Bass",
            "track_index": session.track_by_name("Copilot Capture Bass").index,
            "slot": 2,
            "rec": 0.0,
            "tap_protocol": 4,
        },
    ]
    return discover_topology(
        session=session,
        inventory=inventory,
        master_pos={
            "tap": {"name": "Copilot Audio Tap", "index": 1},
            "is_last": True,
            "devices": ["Copilot Audio Tap"],
        },
    )


def test_user_track_rename_does_not_change_generic_readiness() -> None:
    before = _ready_discovery(_ready_session(["alpha", "beta"]))
    after = _ready_discovery(_ready_session(["anything", "unrelated"]))
    assert not missing_topology(before)
    assert not missing_topology(after)
    assert len(before["eligible_source_names"]) == len(after["eligible_source_names"]) == 2


def test_track_reorder_and_extra_track_do_not_break_readiness() -> None:
    session = _ready_session(["alpha", "beta"])
    reordered = SessionState.model_validate(
        {
            **session.model_dump(),
            "tracks": [
                {**track.model_dump(), "index": i}
                for i, track in enumerate(reversed(session.tracks))
            ],
        }
    )
    extra = _ready_session(["alpha", "beta", "unrelated-extra"])
    assert not missing_topology(_ready_discovery(reordered))
    assert not missing_topology(_ready_discovery(extra))


@pytest.mark.parametrize("names", [["x1", "x2"], ["left", "right"]])
def test_no_recognizable_roles_still_supports_mixture_readiness(names: list[str]) -> None:
    discovery = _ready_discovery(_ready_session(names))
    result = generic_preflight(discovery=discovery, terminal={"ok": True, "failures": []})
    assert result["pass"] is True


def test_no_kick_or_bass_is_a_limited_source_capability_not_a_readiness_failure() -> None:
    session = _ready_session(["texture-a", "texture-b"])
    clips = [
        {"track": "texture-a", "start_qn": 0.0, "end_qn": 8.0},
        {"track": "texture-b", "start_qn": 0.0, "end_qn": 8.0},
    ]
    inventory = inventory_generic_sources(
        session=session,
        clips=clips,
        start_qn=0.0,
        end_qn=8.0,
    )
    assert inventory["eligible_count"] == 2
    assert not any("kick" in row["display_name"].lower() for row in inventory["sources"])
    assert not any("bass" in row["display_name"].lower() for row in inventory["sources"])


def test_multiple_identical_candidates_are_ambiguous_not_first_match() -> None:
    session = SessionState(
        project_identity="p",
        tracks=[_user("same", stable_id="a"), _user("same", stable_id="b")],
    )
    ref = ref_from_track(session.tracks[0], project_identity="p")
    resolved = resolve_track(session, ref)
    assert resolved.status.value == "TARGET_AMBIGUOUS"
    assert resolved.track_index is None


def test_host_device_reorder_does_not_change_topology_identity() -> None:
    session = _ready_session(["texture"])
    host = session.track_by_name("Copilot Capture")
    host.devices = [
        DeviceState(stable_id="tap-1", index=0, name="Copilot Audio Tap"),
        DeviceState(stable_id="utility", index=1, name="Utility"),
    ]
    before = _ready_discovery(session)
    host.devices = [
        DeviceState(stable_id="utility", index=0, name="Utility"),
        DeviceState(stable_id="tap-1", index=1, name="Copilot Audio Tap"),
    ]
    after = _ready_discovery(session)
    assert missing_topology(before) == missing_topology(after)
    assert before["hosts"]["Copilot Capture"]["tap"] == after["hosts"]["Copilot Capture"]["tap"]


def test_main_sidecar_capability_fails_closed_without_observed_main_tap() -> None:
    capacity = discover_capacity(advertised_protocol=4, inventory=[])
    assert capacity.main_sidecar_supported is False


def test_tap_readback_polls_observable_state_without_reissuing_mutation(monkeypatch) -> None:
    states = [[], [], [{"index": 0, "name": "Copilot Audio Tap"}]]
    calls = 0

    def fake_find(_daw, _track_index, *, refresh=False):
        nonlocal calls
        calls += 1
        return states.pop(0) if states else [{"index": 0, "name": "Copilot Audio Tap"}]

    monkeypatch.setattr(live_capture, "find_taps_on_track", fake_find)
    result = live_capture.wait_for_tap_readback(object(), 4, timeout_s=0.2, poll_s=0.001)
    assert result[0]["name"] == "Copilot Audio Tap"
    assert calls == 3


def test_tap_readback_has_typed_deadline_failure(monkeypatch) -> None:
    monkeypatch.setattr(live_capture, "find_taps_on_track", lambda *_args, **_kwargs: [])
    with pytest.raises(live_capture.AudioCaptureError, match="TAP_READBACK_TIMEOUT"):
        live_capture.wait_for_tap_readback(object(), 4, timeout_s=0.001, poll_s=0.001)


def test_browser_resolution_does_not_fake_success_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(m4l_runtime, "find_canonical_tap_uri", lambda _daw: None)
    result = m4l_runtime.verify_live_browser(object(), wait=False)
    assert result["LIVE_BROWSER_RESOLUTION"] == "BLOCKED"
    assert result["uri"] is None


def test_project_ready_has_one_bootstrap_issue_and_observation_only_convergence() -> None:
    tree = ast.parse((ROOT / "src/copilot/audio/project_ready_v1.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "project_ready")
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "bootstrap_project"
    ]
    assert len(calls) == 1


def test_active_runtime_has_no_groove_rider_fixture_literals() -> None:
    active = [
        ROOT / "src/copilot/audio/project_ready_v1.py",
        ROOT / "src/copilot/audio/cross_project_bootstrap_v1.py",
        ROOT / "src/copilot/audio/capture_scalability_v2.py",
        ROOT / "src/copilot/audio/source_capture_batch_v1.py",
        ROOT / "src/copilot/audio/producer_analyze_v1.py",
        ROOT / "src/copilot/audio/music_analyzer.py",
    ]
    forbidden = ("Groove Rider", "Abletunes", "SC Trigger", "Drum Loop")
    hits = [
        f"{path.name}:{literal}"
        for path in active
        for literal in forbidden
        if literal in path.read_text(encoding="utf-8")
    ]
    assert hits == []


def test_write_path_stays_compiler_safe_write_authority() -> None:
    compiler = (ROOT / "src/copilot/runtime/production_compiler.py").read_text(encoding="utf-8")
    safe_write = (ROOT / "src/copilot/runtime/safe_write.py").read_text(encoding="utf-8")
    assert "MutationIntent" in compiler
    assert "SafeWriteExecutor" in safe_write
    assert "def run" in safe_write
