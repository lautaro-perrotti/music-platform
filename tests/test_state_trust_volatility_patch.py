"""STATE_TRUST_VOLATILITY_PATCH_AUDIT — regression for V2 softening.

Core State Trust (state_tokens / musicplan_gate) must stay exact-match.
V2 briefly accepted PROJECT mismatch when host_restore_ok — that softening
is reverted. These tests lock the contract.
"""

from __future__ import annotations

from copy import deepcopy

from copilot.daw.mock import MockAbletonAdapter
from copilot.daw.state_tokens import (
    audible_token,
    attach_tokens,
    canonical_audible,
    canonical_project,
    canonical_target,
    project_token,
    target_token,
)
from copilot.reasoning.musicplan_gate import (
    STALE_AUDIBLE_STATE,
    STALE_PROJECT_STATE,
    STALE_TARGET_STATE,
    evaluate_musicplan_gate,
)


def _lab(*names: str) -> MockAbletonAdapter:
    daw = MockAbletonAdapter()
    daw.connect()
    daw.session_path = r"D:\sets\trust_lab.als"
    daw.session_name = "trust_lab"
    for name in names:
        daw.create_midi_track(name)
    return daw


def test_playback_position_does_not_change_tokens() -> None:
    daw = _lab("Pad")
    before = daw.snapshot()
    attach_tokens(before)
    daw.transport.playing = True
    daw.transport.position_beats = 64.0
    after = daw.snapshot()
    attach_tokens(after)
    assert before.project_token == after.project_token
    assert before.audible_token == after.audible_token
    pad = before.track_by_name("Pad")
    assert pad is not None
    assert target_token(pad) == target_token(after.track_by_name("Pad"))


def test_volume_mutation_changes_audible_and_target_not_project() -> None:
    daw = _lab("Pad")
    before = daw.snapshot()
    attach_tokens(before)
    pad = before.track_by_name("Pad")
    assert pad is not None
    t_before = target_token(pad)
    a_before = before.audible_token
    p_before = before.project_token

    daw.set_mixer_volume(pad.index, max(0.05, pad.mixer.volume * 0.5))
    after = daw.snapshot()
    attach_tokens(after)
    pad_after = after.track_by_name("Pad")
    assert pad_after is not None
    assert after.project_token == p_before  # volume not in PROJECT
    assert after.audible_token != a_before
    assert target_token(pad_after) != t_before


def test_mute_mutation_detected_in_audible_and_target() -> None:
    daw = _lab("Pad")
    before = daw.snapshot()
    attach_tokens(before)
    daw.tracks[0]["mixer"]["mute"] = True
    after = daw.snapshot()
    attach_tokens(after)
    assert after.audible_token != before.audible_token
    assert target_token(after.tracks[0]) != target_token(before.tracks[0])


def test_routing_mutation_changes_project() -> None:
    daw = _lab("Pad")
    before = daw.snapshot()
    attach_tokens(before)
    daw.set_track_output_routing(0, "Sends Only", "")
    after = daw.snapshot()
    attach_tokens(after)
    assert after.project_token != before.project_token
    assert after.audible_token != before.audible_token


def test_structural_track_insert_changes_project() -> None:
    daw = _lab("Pad")
    before = daw.snapshot()
    attach_tokens(before)
    daw.create_midi_track("Other")
    after = daw.snapshot()
    attach_tokens(after)
    assert after.project_token != before.project_token


def test_gate_exact_match_no_tolerance_no_path_override() -> None:
    live_p = "proj_live"
    live_a = "aud_live"
    live_t = "tgt_live"
    # Exact match opens when diagnosis supports.
    opened = evaluate_musicplan_gate(
        diagnosis_accepted=True,
        diagnosis_status="SUPPORTED",
        actionable=True,
        cause_status="CAUSE_SUPPORTED",
        evidence_project_token=live_p,
        evidence_audible_token=live_a,
        evidence_target_token=live_t,
        live_project_token=live_p,
        live_audible_token=live_a,
        live_target_token=live_t,
        project_path_match=True,
    )
    assert opened.gate == "OPEN"

    # Near-miss tokens still STALE (no tolerance).
    stale_p = evaluate_musicplan_gate(
        diagnosis_accepted=True,
        diagnosis_status="SUPPORTED",
        actionable=True,
        evidence_project_token=live_p + "x",
        evidence_audible_token=live_a,
        evidence_target_token=live_t,
        live_project_token=live_p,
        live_audible_token=live_a,
        live_target_token=live_t,
        project_path_match=True,  # path match must NOT override
    )
    assert stale_p.gate == "CLOSED"
    assert stale_p.code == STALE_PROJECT_STATE

    stale_a = evaluate_musicplan_gate(
        diagnosis_accepted=True,
        diagnosis_status="SUPPORTED",
        actionable=True,
        evidence_project_token=live_p,
        evidence_audible_token=live_a + "x",
        evidence_target_token=live_t,
        live_project_token=live_p,
        live_audible_token=live_a,
        live_target_token=live_t,
        project_path_match=True,
    )
    assert stale_a.code == STALE_AUDIBLE_STATE

    stale_t = evaluate_musicplan_gate(
        diagnosis_accepted=True,
        diagnosis_status="SUPPORTED",
        actionable=True,
        evidence_project_token=live_p,
        evidence_audible_token=live_a,
        evidence_target_token=live_t + "x",
        live_project_token=live_p,
        live_audible_token=live_a,
        live_target_token=live_t,
        project_path_match=True,
    )
    assert stale_t.code == STALE_TARGET_STATE


def test_v2_restore_softening_not_present_in_source() -> None:
    """Guard: the improper OR host_restore substitute must not reappear."""
    from pathlib import Path

    src = Path("src/copilot/audio/audible_effect_verification_v2.py").read_text(
        encoding="utf-8"
    )
    assert "project_ok or host_restore_ok" not in src
    assert "SOFTENING_REVERTED" in src
    assert "No host_restore substitute" in src


def test_canonical_still_includes_musical_mixer_and_devices() -> None:
    daw = _lab("Pad")
    session = daw.snapshot()
    track = session.tracks[0]
    proj = canonical_project(session)
    aud = canonical_audible(session)
    tgt = canonical_target(track)
    # Musical fields remain in canon (not stripped by a volatility patch).
    assert "volume" in aud["tracks"][0]["mixer"]
    assert "mute" in aud["tracks"][0]["mixer"]
    assert "routing" in proj["tracks"][0]
    assert "devices" in tgt
    assert "clips" in tgt


def test_device_parameter_change_changes_audible_and_target() -> None:
    daw = _lab("Pad")
    # Give the track a device parameter surface in mock if available.
    session = daw.snapshot()
    attach_tokens(session)
    before_a = session.audible_token
    before_t = target_token(session.tracks[0])
    # Mutate device enabled flag (structural+audible) via raw track dict.
    if daw.tracks[0].get("devices"):
        daw.tracks[0]["devices"][0]["enabled"] = not bool(
            daw.tracks[0]["devices"][0].get("enabled", True)
        )
    else:
        # Insert a device-like structure the snapshot maps if supported;
        # otherwise mutate mixer pan as audible musical state.
        daw.tracks[0]["mixer"]["pan"] = 0.25
    after = daw.snapshot()
    attach_tokens(after)
    assert after.audible_token != before_a
    assert target_token(after.tracks[0]) != before_t
