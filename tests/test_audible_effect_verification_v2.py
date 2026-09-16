"""AUDIBLE_EFFECT_VERIFICATION_V2 — offline design helpers."""

from __future__ import annotations

from copilot.audio.audible_effect_verification_v2 import (
    _choose_high_snr_mutation,
    _classify_baseline,
)


def test_baseline_stable_vs_variable() -> None:
    stable = _classify_baseline(0.01, 0.01005)
    assert stable["stable"] is True
    noisy = _classify_baseline(0.01, 0.012)
    assert noisy["stable"] is False


def test_high_snr_mutation_frozen_before_after() -> None:
    mut = _choose_high_snr_mutation(before=0.75, spread_db=0.2)
    assert mut["after_value"] < mut["before_value"]
    assert mut["rollback_value"] == 0.75
    assert mut["parameter_ratio"] < 0.85
    assert "fader_law" in mut["selection_method"] or "no_authoritative" in mut["selection_method"]
    assert "linear-approx reduction" not in mut["rationale"]
    assert "high-SNR preselected" in mut["rationale"]
