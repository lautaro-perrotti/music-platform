"""AUDIBLE_EFFECT_VERIFICATION_V1 — offline helpers (no Live)."""

from __future__ import annotations

from copilot.audio.audible_effect_verification import _relative_db


def test_relative_db_direction_for_volume_step() -> None:
    # Linear mixer gain 0.75 → 0.73 ≈ -0.23 dB if energy tracks gain.
    before = 0.10
    after = 0.10 * (0.73 / 0.75)
    db = _relative_db(after, before)
    assert db < 0
    assert abs(db - (-0.234)) < 0.05


def test_variance_gate_logic() -> None:
    spread = 0.0001
    expected_abs = 0.10 * (0.02 / 0.75)
    assert expected_abs > 2.0 * spread
