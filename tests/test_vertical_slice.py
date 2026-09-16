from __future__ import annotations

from copilot.agent.slices import (
    run_conservative_bass_cut,
    run_create_c3_clip,
    run_listen_region,
    run_undo_last,
)
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.mock import MockAbletonAdapter
from copilot.schemas.transaction import TransactionStatus


def _tools() -> AgentTools:
    daw = MockAbletonAdapter()
    daw.connect()
    return AgentTools(daw, TransactionManager(daw))


def test_slice1_create_clip_and_verify_notes() -> None:
    tools = _tools()
    result = run_create_c3_clip(tools)
    assert result["status"] == TransactionStatus.VERIFIED.value
    assert "AI Test" in result["after_tracks"]
    assert result["verification"]["note_count"] == 16
    assert result["verification"]["pitch"] == 60
    txn = tools.transactions.last_own()
    assert txn is not None
    assert len(txn.actions) == 3


def test_slice1_rollback_restores_original_state() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    undone = run_undo_last(tools)
    assert undone["status"] == TransactionStatus.ROLLED_BACK.value
    assert "AI Test" not in undone["tracks"]
    state = tools.get_session_snapshot()
    assert state.tracks == []


def test_error_when_track_already_exists() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    try:
        run_create_c3_clip(tools)
        raise AssertionError("expected failure")
    except Exception as exc:
        assert "already exists" in str(exc)
    failed = tools.transactions.history[-1]
    assert failed.status == TransactionStatus.FAILED


def test_listen_measures_real_audio() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    result = run_listen_region(tools, "AI Test")
    obs = result["observation"]
    assert result["sample_count"] > 1000
    assert obs["signal"]["duration_seconds"] > 7.0
    assert obs["signal"]["rms"] > 0
    assert obs["signal"]["peak"] > 0
    assert obs["signal"]["lufs"] is not None
    assert obs["signal"]["spectral_centroid_hz"] is not None
    assert obs["key"] is None
    assert obs["confidence"]["key"] == 0.0
    names = {claim["name"] for claim in obs["claims"]}
    assert "rms" in names
    assert "lufs" in names


def test_listen_modify_compare_and_rollback() -> None:
    tools = _tools()
    run_create_c3_clip(tools)
    result = run_conservative_bass_cut(tools, "AI Test")
    assert result["comparison"]["bass_ratio_delta"] is not None
    assert result["comparison"]["bass_ratio_delta"] < -0.05
    notes_before = tools.daw.get_clip_notes(0, 0)["note_count"]
    run_undo_last(tools, expected_track="__none__")
    notes_after = tools.daw.get_clip_notes(0, 0)["note_count"]
    assert notes_before == notes_after == 16
    state = tools.get_session_snapshot()
    assert state.tracks[0].devices[0].parameters[0].value == 0.5
