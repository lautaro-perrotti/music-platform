from copilot.audio.project_ready_v1 import generic_preflight
from copilot.audio.cross_project_bootstrap_v1 import classify_project


def test_generic_preflight_blocks_on_missing() -> None:
    discovery = {
        "transport_playing": False,
        "project": classify_project(r"C:\songs\new.als"),
        "project_identity": "p",
        "track_count": 2,
        "main": {"tap": None, "is_last": False},
        "hosts": {},
        "eligible_source_names": [],
        "lookalike_user_tracks": [],
        "infra_name_collisions": [],
        "slot_collisions": [],
    }
    out = generic_preflight(
        discovery=discovery,
        terminal={"ok": True, "failures": []},
    )
    assert out["pass"] is False
    assert out["kind"] == "GENERIC_PREFLIGHT_V1"
    assert out["NO ASTRA"] is True


def test_generic_preflight_passes_when_complete() -> None:
    discovery = {
        "transport_playing": False,
        "project": classify_project(r"C:\songs\new.als"),
        "project_identity": "p",
        "track_count": 3,
        "main": {"tap": {"name": "Copilot Audio Tap"}, "is_last": True, "devices": ["Tap"]},
        "hosts": {
            "Copilot Capture": {
                "present": True,
                "name_collision": False,
                "tap": {"slot": 1, "rec": 0.0, "tap_protocol": 3},
                "routing": {"already_correct": True},
            },
            "Copilot Capture Bass": {
                "present": True,
                "name_collision": False,
                "tap": {"slot": 2, "rec": 0.0, "tap_protocol": 3},
                "routing": {"already_correct": True},
            },
        },
        "eligible_source_names": ["Lead"],
        "lookalike_user_tracks": [],
        "infra_name_collisions": [],
        "slot_collisions": [],
    }
    out = generic_preflight(
        discovery=discovery,
        terminal={"ok": True, "failures": []},
    )
    assert out["pass"] is True
    assert out["status"] == "PRE-FLIGHT VERIFIED"
