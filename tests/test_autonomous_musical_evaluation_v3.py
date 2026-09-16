"""Unit tests for AUTONOMOUS_MUSICAL_EVALUATION_V3 (no Ableton)."""

from __future__ import annotations

import json
from pathlib import Path

from copilot.audio.arrangement_active_source_isolation import (
    FROZEN as ISOLATION_FROZEN,
    STATUS as ISOLATION_STATUS,
)
from copilot.audio.autonomous_musical_evaluation_v2 import map_gate_to_milestone
from copilot.audio.autonomous_musical_evaluation_v3 import (
    FORBIDDEN_QN_RANGES,
    SOURCE_EVIDENCE_GAP,
    forbidden_hit,
    persist_holdout_v3,
    select_holdout_from_als,
    select_holdout_from_clips,
    window_interaction,
)
from copilot.audio.arrangement_activity import merge_intervals
from copilot.audio.first_autonomous_musical_improvement_v1 import evaluate_action_gate

WORKING_COPY = Path(r"C:\Users\lsper\Desktop\pista Project\pista_copilot_eval.als")


def _clip(track: str, start: float, end: float, *, group: str | None = None) -> dict:
    return {
        "track": track,
        "group": group,
        "is_group": False,
        "kind": "MidiClip",
        "start_qn": start,
        "end_qn": end,
        "name": track,
    }


def test_isolation_v1_is_frozen() -> None:
    assert ISOLATION_STATUS == "VERIFIED"
    assert ISOLATION_FROZEN is True
    assert SOURCE_EVIDENCE_GAP == "CLOSED"


def test_forbidden_includes_prior_holdouts_and_isolation_engineering() -> None:
    labels = {lab for _, _, lab in FORBIDDEN_QN_RANGES}
    assert forbidden_hit(32.0, 64.0)
    assert forbidden_hit(96.0, 128.0)
    assert forbidden_hit(128.0, 160.0)
    assert forbidden_hit(256.0, 288.0)
    assert forbidden_hit(320.0, 352.0)
    assert any("SOURCE_ISOLATION" in lab or "AME_V2" in lab for lab in labels)


def test_sequential_drums_then_rose_is_not_simultaneous() -> None:
    drums = merge_intervals([_clip("Drums", 224.0, 256.0, group="Drums")])
    rose = merge_intervals([_clip("Rose Bass", 160.0, 224.0)])
    sub: list = []
    row = window_interaction(
        {"Drums": drums, "Rose Bass": rose, "Sub Sub Bass": sub},
        208.0,
        240.0,
    )
    assert row["useful"] is False
    assert row["simultaneous_sources"] == 0


def test_select_unused_three_source_window() -> None:
    clips = [
        _clip("Drums", 384.0, 416.0, group="Drums"),
        _clip("Kick", 384.0, 416.0, group="Drums"),
        _clip("Rose Bass", 384.0, 416.0),
        _clip("Sub Sub Bass", 384.0, 416.0),
        _clip("Drums", 256.0, 288.0, group="Drums"),
        _clip("Rose Bass", 256.0, 288.0),
        _clip("Sub Sub Bass", 256.0, 288.0),
    ]
    out = select_holdout_from_clips(clips)
    assert out["ok"] is True
    assert out["region"]["start_qn"] == 384.0
    assert out["region"]["end_qn"] == 416.0
    assert out["region"]["id"] == "HOLDOUT_384_416"


def test_select_refuses_used_regions() -> None:
    clips = [
        _clip("Drums", 320.0, 352.0, group="Drums"),
        _clip("Rose Bass", 320.0, 352.0),
        _clip("Sub Sub Bass", 320.0, 352.0),
        _clip("Drums", 256.0, 288.0, group="Drums"),
        _clip("Rose Bass", 256.0, 288.0),
        _clip("Sub Sub Bass", 256.0, 288.0),
        _clip("Drums", 96.0, 128.0, group="Drums"),
        _clip("Sub Sub Bass", 96.0, 128.0),
    ]
    out = select_holdout_from_clips(clips)
    assert out["ok"] is False
    assert out["status"] == "NO_INDEPENDENT_ACTIVE_HOLDOUT"
    assert out["region"] is None


def test_persist_holdout_before_diagnosis(tmp_path: Path) -> None:
    selection = select_holdout_from_clips(
        [
            _clip("Drums", 384.0, 416.0, group="Drums"),
            _clip("Rose Bass", 384.0, 416.0),
            _clip("Sub Sub Bass", 384.0, 416.0),
        ]
    )
    path = persist_holdout_v3(tmp_path, selection=selection, project_token="tok")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["region"]["id"] == "HOLDOUT_384_416"
    assert payload["PROJECT_STATE_TOKEN_at_selection"] == "tok"
    assert payload["NO_ANALYZER_RETUNE"] is True
    assert payload["not_selected_for_known_defect"] is True


def test_persist_no_holdout(tmp_path: Path) -> None:
    selection = select_holdout_from_clips([])
    path = persist_holdout_v3(tmp_path, selection=selection, project_token="tok")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["region"] is None
    assert payload["selection_status"] == "NO_INDEPENDENT_ACTIVE_HOLDOUT"


def test_ie_not_collapsed_to_no_action() -> None:
    g = evaluate_action_gate(
        diagnosis_status="INSUFFICIENT_EVIDENCE",
        diagnosis_accepted=True,
        category="INSUFFICIENT_EVIDENCE",
        candidate_actions=[{"action_type": "NO_CHANGE"}],
        cause_status=None,
    )
    assert map_gate_to_milestone(g, "INSUFFICIENT_EVIDENCE") == "INSUFFICIENT_EVIDENCE"


def test_supported_non_volume_is_action_not_available() -> None:
    g = evaluate_action_gate(
        diagnosis_status="SUPPORTED",
        diagnosis_accepted=True,
        category="SPECTRAL_MASKING",
        candidate_actions=[{"action_type": "REDUCE_LOW_BAND_ENERGY", "target": "Rose Bass"}],
        cause_status="CAUSE_SUPPORTED",
    )
    assert g["milestone_status"] == "ACTION_NOT_AVAILABLE"
    assert g["proceed_to_write"] is False


def test_current_song_has_no_independent_multi_source_holdout() -> None:
    if not WORKING_COPY.is_file():
        return
    out = select_holdout_from_als(WORKING_COPY)
    assert out["ok"] is False
    assert out["status"] == "NO_INDEPENDENT_ACTIVE_HOLDOUT"
    # The only 3-source 32-qn windows are already used (256–288, 320–352).
    useful = [r for r in out["scanned"] if r["useful"]]
    assert useful
    assert all(r["forbidden"] for r in useful)
