from __future__ import annotations

from copilot.daw.mock import MockAbletonAdapter
import copilot.producer.soniq_surface as ss
from copilot.producer.soniq_surface import (
    VstParamWatcher,
    coalesce_writes,
    read_vst_params,
    read_vst_schema,
    set_vst_params_batch,
    apply_patch,
    apply_patch_contract,
    capture_param_snapshot,
    restore_param_snapshot,
    load_preset,
    detect_surface_completeness,
    apply_patch_contract_auto_mode,
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
    assert out == [{"index": 0, "value": 0.7, "normalized": True}, {"index": 1, "value": 0.2, "normalized": True}]


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



def test_apply_patch_contract_enforces_write_count_and_device_on_guard() -> None:
    daw, session = _seed_session_with_serum_like_device()
    report = apply_patch_contract(
        daw,
        session=session,
        contract={
            "track": "Synth",
            "device": "Serum 2",
            "writes": [
                {"index": 0, "value": 0.0},  # should be blocked (Device On guard by index)
                {"index": 1, "value": 0.1},
                {"index": 1, "value": 0.2},
                {"index": 1, "value": 0.3},
            ],
            "constraints": {"max_writes": 2, "max_delta_norm": 0.5, "forbid_device_on_toggle": True},
        },
        throttle_ms=0,
    )
    assert report["ok"] is True
    assert report["applied"] == 1
    assert any("blocked_device_on_toggle" in v for v in report["violations"])



def test_capture_and_restore_param_snapshot_roundtrip() -> None:
    daw, session = _seed_session_with_serum_like_device()
    snap = capture_param_snapshot(
        daw,
        session=session,
        track_name="Synth",
        device_name="Serum 2",
        filter_midi_passthrough=False,
    )
    assert snap["ok"] is True

    # mutate
    _ = apply_patch(
        daw,
        session=daw.snapshot(),
        track_name="Synth",
        device_name="Serum 2",
        writes=[{"index": 0, "value": 0.9}],
        throttle_ms=0,
        filter_midi_passthrough=False,
    )

    restored = restore_param_snapshot(daw, session=daw.snapshot(), snapshot=snap, throttle_ms=0)
    assert restored["ok"] is True


def test_load_preset_on_mock() -> None:
    daw, session = _seed_session_with_serum_like_device()
    rep = load_preset(
        daw,
        session=session,
        track_name="Synth",
        device_name="Serum 2",
        preset_uri="query:UserPresets#Serum2#WarmPad01",
    )
    assert rep["ok"] is True



def test_detect_surface_completeness_fallback_for_small_serum_surface() -> None:
    daw, session = _seed_session_with_serum_like_device()
    det = detect_surface_completeness(
        daw,
        session=session,
        track_name="Synth",
        device_name="Serum 2",
        filter_midi_passthrough=False,
    )
    assert det["mode"] == "limited_surface"
    assert det["is_full_surface"] is False


def test_apply_patch_contract_auto_mode_routes_fallback() -> None:
    daw, session = _seed_session_with_serum_like_device()
    rep = apply_patch_contract_auto_mode(
        daw,
        session=session,
        contract={
            "track": "Synth",
            "device": "Serum 2",
            "writes": [{"index": 1, "value": 0.6}],
            "constraints": {},
        },
        throttle_ms=0,
    )
    assert rep["ok"] is True
    assert rep["routing_mode"] == "fallback_surface"


def test_apply_patch_contract_auto_mode_routes_full_for_large_surface() -> None:
    daw, session = _seed_session_with_serum_like_device()
    # Inflate params to mimic a full Serum2 surface like Soniq (~2623)
    dev = daw.tracks[0]["devices"][-1]
    dev["parameters"] = [
        {"index": i, "name": f"P{i}", "value": 0.0, "min": 0.0, "max": 1.0}
        for i in range(2400)
    ]
    session = daw.snapshot()
    rep = apply_patch_contract_auto_mode(
        daw,
        session=session,
        contract={
            "track": "Synth",
            "device": "Serum 2",
            "writes": [{"index": 5, "value": 0.6}],
            "constraints": {},
        },
        throttle_ms=0,
    )
    assert rep["ok"] is True
    assert rep["routing_mode"] == "full_surface"


def test_auto_mode_tries_soniq_for_limited_complex_plugin(monkeypatch) -> None:
    daw, session = _seed_session_with_serum_like_device()

    monkeypatch.setattr(ss, "_soniq_ws_url", lambda: "ws://127.0.0.1:9123")
    monkeypatch.setattr(ss, "_apply_patch_via_soniq_ws", lambda contract: {"ok": True, "applied": 1})

    rep = apply_patch_contract_auto_mode(
        daw,
        session=session,
        contract={
            "track": "Synth",
            "device": "Serum 2",
            "writes": [{"index": 1, "value": 0.6}],
            "constraints": {},
        },
        throttle_ms=0,
    )
    assert rep["ok"] is True
    assert rep["routing_mode"] == "soniq_full_surface"
    assert rep["soniq"]["applied"] == 1


def test_auto_mode_falls_back_when_soniq_fails(monkeypatch) -> None:
    daw, session = _seed_session_with_serum_like_device()

    monkeypatch.setattr(ss, "_soniq_ws_url", lambda: "ws://127.0.0.1:9123")
    monkeypatch.setattr(ss, "_apply_patch_via_soniq_ws", lambda contract: {"ok": False, "error": "down"})

    rep = apply_patch_contract_auto_mode(
        daw,
        session=session,
        contract={
            "track": "Synth",
            "device": "Serum 2",
            "writes": [{"index": 1, "value": 0.6}],
            "constraints": {},
        },
        throttle_ms=0,
    )
    assert rep["ok"] is True
    assert rep["routing_mode"] == "fallback_surface"
    assert rep["soniq_attempted"] is True
    assert rep["soniq"]["error"] == "down"
