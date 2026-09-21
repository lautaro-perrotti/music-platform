"""Bootstrap plan/idempotency fixtures. No Ableton required."""

from __future__ import annotations

from pathlib import Path

from copilot.agent.journal import DurableJournal
from copilot.audio.cross_project_bootstrap_v1 import (
    classify_project,
    discover_topology,
    is_lookalike_user_track,
    missing_topology,
    plan_bootstrap,
)
from copilot.audio.terminal_state_v1 import unresolved_bootstrap_journals
from copilot.importing.working_copy_manager_v1 import create_working_copy
from copilot.schemas.session import (
    DeviceState,
    MixerState,
    RoutingState,
    SessionState,
    TrackState,
    TransportState,
)


def _track(
    index: int,
    name: str,
    *,
    role: str = "audio",
    devices: list[DeviceState] | None = None,
    routing: RoutingState | None = None,
    sends=None,
    mute: bool = False,
) -> TrackState:
    return TrackState(
        stable_id=f"trk_{index}_{name}",
        index=index,
        name=name,
        role=role,  # type: ignore[arg-type]
        mixer=MixerState(mute=mute),
        routing=routing or RoutingState(),
        devices=devices or [],
        sends=list(sends or []),
    )


def _session(tracks: list[TrackState], *, path: str = r"C:\song\new.als", playing: bool = False) -> SessionState:
    return SessionState(
        project_path=path,
        project_name="new",
        project_identity="proj_test",
        project_token="ptok",
        audible_token="atok",
        transport=TransportState(playing=playing),
        tracks=tracks,
    )


def _tap(name: str, slot: int, rec: float = 0.0) -> dict:
    return {
        "track_name": name,
        "track_index": 0,
        "slot": slot,
        "rec": rec,
        "tap_protocol": 3,
        "device_index": 0,
    }


def _master(last: bool = True) -> dict:
    return {
        "tap": {"name": "Copilot Audio Tap", "index": 1} if last else None,
        "is_last": last,
        "devices": ["Utility", "Copilot Audio Tap"] if last else [],
    }


def _working_pair(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "Source Project"
    source_root.mkdir()
    source = source_root / "Source.als"
    source.write_bytes(b"set")
    copy = create_working_copy(
        source_als=source,
        project_root=source_root,
        copy_scope="project_directory",
        workspace=tmp_path / "CopilotProjects",
    )
    return source, Path(str(copy["working_als"]))


def test_classify_project_kinds(tmp_path: Path, monkeypatch) -> None:
    source, working = _working_pair(tmp_path)
    monkeypatch.setenv("COPILOT_WORKING_COPY_ROOT", str(tmp_path / "CopilotProjects"))
    assert classify_project(str(working))["kind"] == "development_working_copy"
    assert classify_project(str(source))["refuse_original"] is True
    assert classify_project(r"C:\x\copilot_bootstrap_fixture.als")["kind"] == "bootstrap_fixture"
    assert classify_project(r"C:\other\song.als")["kind"] == "external"
    assert classify_project(r"C:\x\Sin título.als")["kind"] == "untitled_scratch"


def test_lookalike_is_not_infra() -> None:
    assert is_lookalike_user_track("Copilot Capture Vocals") is True
    assert is_lookalike_user_track("Copilot Capture") is False


def test_a_empty_project_plans_infra_only() -> None:
    session = _session([])
    discovery = discover_topology(session=session, inventory=[], master_pos=_master(False))
    missing = missing_topology(discovery)
    plan = plan_bootstrap(discovery)
    assert "Main has no Copilot Audio Tap" in missing
    assert any("capture track missing" in item for item in missing)
    assert plan["status"] == "CHANGES_REQUIRED"
    assert {row["op"] for row in plan["actions"]} >= {"ENSURE_MASTER_TAP", "ENSURE_HOST_TRACK"}
    assert all(row.get("name") != "User Lead" for row in plan["actions"])


def test_b_partial_host_without_tap() -> None:
    session = _session(
        [
            _track(0, "Lead"),
            _track(1, "Copilot Capture"),
        ]
    )
    discovery = discover_topology(
        session=session,
        inventory=[],
        master_pos=_master(True),
    )
    plan = plan_bootstrap(discovery)
    ops = [row["op"] for row in plan["actions"]]
    assert "ENSURE_HOST_TAP" in ops
    assert "ENSURE_HOST_TRACK" in ops  # bass still missing
    assert "ENSURE_MASTER_TAP" not in ops


def _parked() -> RoutingState:
    return RoutingState(
        input_type="Resampling",
        input_channel="",
        output_type="Sends Only",
        monitoring="off",
    )


def test_c_fully_bootstrapped_no_changes() -> None:
    routing = _parked()
    session = _session(
        [
            _track(0, "Lead"),
            _track(
                1,
                "Copilot Capture",
                devices=[DeviceState(stable_id="d1", index=0, name="Copilot Audio Tap")],
                routing=routing,
            ),
            _track(
                2,
                "Copilot Capture Bass",
                devices=[DeviceState(stable_id="d2", index=0, name="Copilot Audio Tap")],
                routing=routing,
            ),
        ]
    )
    inventory = [
        _tap("MASTER", 0),
        _tap("Copilot Capture", 1),
        _tap("Copilot Capture Bass", 2),
    ]
    discovery = discover_topology(
        session=session,
        inventory=inventory,
        master_pos=_master(True),
    )
    assert plan_bootstrap(discovery)["status"] == "NO_CHANGES_REQUIRED"
    # second run converges
    assert plan_bootstrap(discovery)["status"] == "NO_CHANGES_REQUIRED"


def test_d_duplicate_looking_track_names_use_identity() -> None:
    session = _session(
        [
            _track(0, "Lead"),
            _track(1, "Lead"),
        ]
    )
    discovery = discover_topology(session=session, inventory=[], master_pos=_master(True))
    refs = [row["ref"]["content_fingerprint"] for row in discovery["sources"] if not row["infra"]]
    assert refs[0] == refs[1]
    assert discovery["sources"][0]["index_locator_only"] != discovery["sources"][1]["index_locator_only"]


def test_e_interrupted_bootstrap_journal_is_open(tmp_path: Path) -> None:
    journal = DurableJournal(tmp_path / "boot1.jsonl")
    journal.append({"status": "PREPARED", "pass_id": "boot1"})
    open_rows = unresolved_bootstrap_journals(tmp_path)
    assert len(open_rows) == 1
    assert open_rows[0]["last_status"] == "PREPARED"


def test_f_stale_journal_does_not_claim_verified(tmp_path: Path) -> None:
    journal = DurableJournal(tmp_path / "boot2.jsonl")
    journal.append({"status": "APPLYING"})
    assert unresolved_bootstrap_journals(tmp_path)
    journal.append({"status": "VERIFIED"})
    assert unresolved_bootstrap_journals(tmp_path) == []


def test_g_lookalike_user_track_is_not_taken_over() -> None:
    session = _session(
        [
            _track(0, "Copilot Capture Vocals", role="audio"),
            _track(1, "Lead"),
        ]
    )
    discovery = discover_topology(session=session, inventory=[], master_pos=_master(True))
    assert "Copilot Capture Vocals" in discovery["lookalike_user_tracks"]
    assert "Copilot Capture Vocals" not in discovery["eligible_source_names"]
    plan = plan_bootstrap(discovery)
    assert all(row.get("name") != "Copilot Capture Vocals" for row in plan["actions"])


def test_exact_infra_name_with_user_devices_is_collision() -> None:
    session = _session(
        [
            _track(
                0,
                "Copilot Capture",
                devices=[DeviceState(stable_id="wav", index=0, name="Wavetable")],
            )
        ]
    )
    discovery = discover_topology(session=session, inventory=[], master_pos=_master(True))
    plan = plan_bootstrap(discovery)
    assert plan["status"] == "BLOCKED"
    assert plan["reason"] == "USER_TRACK_NAME_COLLISION"


def test_original_set_refused(tmp_path: Path, monkeypatch) -> None:
    source, _working = _working_pair(tmp_path)
    monkeypatch.setenv("COPILOT_WORKING_COPY_ROOT", str(tmp_path / "CopilotProjects"))
    session = _session([], path=str(source))
    discovery = discover_topology(session=session, inventory=[], master_pos=_master(True))
    assert plan_bootstrap(discovery)["status"] == "BLOCKED"


def test_existing_isolated_routing_is_not_parked() -> None:
    routing = RoutingState(
        input_type="LIVE22 Kick",
        input_channel="Post Mixer",
        output_type="Sends Only",
        monitoring="In",
    )
    session = _session(
        [
            _track(0, "1-MIDI", role="midi"),
            _track(
                1,
                "Copilot Capture",
                devices=[DeviceState(stable_id="d1", index=0, name="Copilot Audio Tap")],
                routing=routing,
            ),
            _track(
                2,
                "Copilot Capture Bass",
                devices=[DeviceState(stable_id="d2", index=0, name="Copilot Audio Tap")],
                routing=RoutingState(
                    input_type="LIVE22 Bass",
                    input_channel="Post Mixer",
                    output_type="Sends Only",
                    monitoring="In",
                ),
            ),
        ]
    )
    inventory = [_tap("MASTER", 0), _tap("Copilot Capture", 1), _tap("Copilot Capture Bass", 2)]
    discovery = discover_topology(
        session=session, inventory=inventory, master_pos=_master(True)
    )
    plan = plan_bootstrap(discovery)
    assert plan["status"] == "CHANGES_REQUIRED"
    assert {row["op"] for row in plan["actions"]} == {"ENSURE_HOST_PARKED"}
    assert all(row["op"] != "ENSURE_HOST_ROUTING" for row in plan["actions"])


def test_canonical_parked_hosts_are_no_changes() -> None:
    routing = _parked()
    session = _session(
        [
            _track(0, "1-MIDI", role="midi"),
            _track(
                1,
                "Copilot Capture",
                devices=[DeviceState(stable_id="d1", index=0, name="Copilot Audio Tap")],
                routing=routing,
            ),
            _track(
                2,
                "Copilot Capture Bass",
                devices=[DeviceState(stable_id="d2", index=0, name="Copilot Audio Tap")],
                routing=routing,
            ),
        ]
    )
    inventory = [_tap("MASTER", 0), _tap("Copilot Capture", 1), _tap("Copilot Capture Bass", 2)]
    discovery = discover_topology(
        session=session, inventory=inventory, master_pos=_master(True)
    )
    assert plan_bootstrap(discovery)["status"] == "NO_CHANGES_REQUIRED"


def test_development_working_copy_does_not_reroute(tmp_path: Path, monkeypatch) -> None:
    _source, working = _working_pair(tmp_path)
    monkeypatch.setenv("COPILOT_WORKING_COPY_ROOT", str(tmp_path / "CopilotProjects"))
    session = _session(
        [_track(0, "Lead")],
        path=str(working),
    )
    discovery = discover_topology(session=session, inventory=[], master_pos=_master(True))
    plan = plan_bootstrap(discovery)
    assert discovery["project"]["is_development_working_copy"] is True
    assert all(row["op"] != "ENSURE_HOST_ROUTING" for row in plan["actions"])


def test_transport_playing_blocks() -> None:
    session = _session([_track(0, "Lead")], playing=True)
    discovery = discover_topology(session=session, inventory=[], master_pos=_master(True))
    assert plan_bootstrap(discovery)["status"] == "BLOCKED"
    assert "transport is playing" in missing_topology(discovery)[0]
