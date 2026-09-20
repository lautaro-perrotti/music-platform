from __future__ import annotations

from copilot.daw.mock import MockAbletonAdapter
from copilot.producer.soniq_surface import (
    VstParamWatcher,
    coalesce_writes,
    read_vst_params,
    read_vst_schema,
    set_vst_params_batch,
    apply_patch,
)


def _seed_session_with_serum_like_device() -> tuple[MockAbletonAdapter, object]:
    daw = MockAbletonAdapter()
    daw.connect()
    daw.create_midi_track("Synth", 0)
    # add a "Serum 2"-named device with params including MIDI passthrough style
    track = daw.tracks[0]
    track["devices"].append(
        {
            "name": "Serum 2",
            "class_name": "Serum2",
            "enabled": True,
            "parameters": [
                {"index": 0, "name": "Cutoff", "value": 0.25, "min": 0.0, "max": 1.0},
                {"index": 1, "name": "Resonance", "value": 0.5, "min": 0.0, "max": 1.0},
                {"index": 2, "name": "CC74 Chan 1", "value": 0.0, "min": 0.0, "max": 1.0},
            ],
        }
    )
    session = daw.snapshot()
    return daw, session


def test_read_vst_schema_filters_midi_passthrough_params() -> None:
    daw, session = _seed_session_with_serum_like_device()
    schema = read_vst_schema(
        daw,
        session=session,
        track_name="Synth",
        device_name="Serum 2",
        filter_midi_passthrough=True,
    )
    names = [p["name"] for p in schema["parameters"]]
    assert "CC74 Chan 1" not in names
    assert "Cutoff" in names and "Resonance" in names


def test_read_vst_params_reads_specific_indices() -> None:
    daw, session = _seed_session_with_serum_like_device()
    out = read_vst_params(
        daw,
        session=session,
        track_name="Synth",
        device_name="Serum 2",
        indices=[1, 999],
    )
    assert len(out["params"]) == 1
    assert out["params"][0]["name"] == "Resonance"


def test_set_vst_params_batch_writes_and_verifies() -> None:
    daw, session = _seed_session_with_serum_like_device()
    report = set_vst_params_batch(
        daw,
        session=session,
        track_name="Synth",
        device_name="Serum 2",
        writes=[{"index": 0, "value": 0.7}, {"index": 1, "value": 0.2}],
    )
    assert report["ok"] is True
    by_idx = {r["index"]: r for r in report["readback"]}
    assert by_idx[0]["actual"] == 0.7
    assert by_idx[1]["actual"] == 0.2



def test_coalesce_writes_last_value_wins_per_index() -> None:
    out = coalesce_writes([
        {"index": 0, "value": 0.1},
        {"index": 1, "value": 0.2},
        {"index": 0, "value": 0.7},
    ])
    assert out == [{"index": 0, "value": 0.7}, {"index": 1, "value": 0.2}]


def test_set_vst_params_batch_coalesces_before_write() -> None:
    daw, session = _seed_session_with_serum_like_device()
    report = set_vst_params_batch(
        daw,
        session=session,
        track_name="Synth",
        device_name="Serum 2",
        writes=[{"index": 0, "value": 0.1}, {"index": 0, "value": 0.8}],
    )
    assert report["ok"] is True
    assert report["writes"] == 1
    assert report["readback"][0]["actual"] == 0.8


def test_vst_param_watcher_reports_param_changed_events() -> None:
    daw, session = _seed_session_with_serum_like_device()
    watcher = VstParamWatcher(track_name="Synth", device_name="Serum 2", indices=[0, 1])
    boot = watcher.bootstrap(daw, session=session)
    assert boot["ok"] is True

    # mutate one param
    set_vst_params_batch(
        daw,
        session=daw.snapshot(),
        track_name="Synth",
        device_name="Serum 2",
        writes=[{"index": 1, "value": 0.9}],
    )

    ev = watcher.poll(daw, session=daw.snapshot())
    assert ev["ok"] is True
    assert ev["count"] == 1
    assert ev["events"][0]["event"] == "param_changed"
    assert ev["events"][0]["index"] == 1
    assert ev["events"][0]["value"] == 0.9



def test_apply_patch_name_based_flow_includes_events() -> None:
    daw, session = _seed_session_with_serum_like_device()
    report = apply_patch(
        daw,
        session=session,
        track_name="Synth",
        device_name="Serum 2",
        writes=[{"name": "cutoff", "value": 0.66}, {"name": "cutoff", "value": 0.77}],
        throttle_ms=0,
        filter_midi_passthrough=False,
    )
    assert report["ok"] is True
    assert report["resolved"] == 1  # coalesced
    assert report["event_count"] >= 1
    rb = report["write"]["readback"][0]
    assert rb["index"] == 0
    assert rb["actual"] == 0.77
