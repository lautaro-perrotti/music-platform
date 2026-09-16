from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.fullmix import (
    ANALYZER_ID,
    compute_fullmix_observation,
    configuration_hash,
)
from copilot.reasoning.from_fullmix import fullmix_items, merge_lowend_and_fullmix
from copilot.reasoning.from_dsp import pack_from_lowend_features
from copilot.schemas.fullmix import EnergyEventKind


def _write_stereo(path: Path, left: np.ndarray, right: np.ndarray | None = None, sr: int = 44100) -> None:
    if right is None:
        right = left
    data = np.column_stack([left.astype(np.float32), right.astype(np.float32)])
    sf.write(path, data, sr)


def _tone(sr: int, duration_s: float, freq: float = 110.0, amp: float = 0.2) -> np.ndarray:
    t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float64)


def test_true_silence(tmp_path: Path) -> None:
    path = tmp_path / "silence.wav"
    _write_stereo(path, np.zeros(44100, dtype=np.float64))
    obs = compute_fullmix_observation(path, region_id="SIL", use_cache=False)
    assert any(ev.kind is EnergyEventKind.SILENCE for ev in obs.energy_events)
    assert obs.analyzer_id == ANALYZER_ID


def test_near_silence(tmp_path: Path) -> None:
    path = tmp_path / "near.wav"
    _write_stereo(path, np.full(44100, 5e-4, dtype=np.float64))
    obs = compute_fullmix_observation(path, region_id="NEAR", use_cache=False)
    assert any(ev.kind is EnergyEventKind.NEAR_SILENCE for ev in obs.energy_events)


def test_single_isolated_dip(tmp_path: Path) -> None:
    sr = 44100
    base = _tone(sr, 2.0, amp=0.25)
    # insert a deep dip at 0.8-1.0s only
    start = int(0.8 * sr)
    end = int(1.0 * sr)
    base[start:end] *= 0.01
    path = tmp_path / "dip.wav"
    _write_stereo(path, base, sr=sr)
    obs = compute_fullmix_observation(path, region_id="DIP", use_cache=False)
    dips = [ev for ev in obs.energy_events if ev.kind is EnergyEventKind.STRONG_ENERGY_DIP]
    assert dips
    # isolated => low similarity relative to a repeating pattern
    assert max(ev.event_similarity_count for ev in dips) <= 2


def test_repeating_rhythmic_dips(tmp_path: Path) -> None:
    sr = 44100
    dur = 4.0
    base = _tone(sr, dur, amp=0.3)
    # dips every 0.5s lasting 0.1s
    for t0 in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        a = int(t0 * sr)
        b = int((t0 + 0.1) * sr)
        # Keep absolute level above NEAR_SILENCE so the event is a relative dip.
        base[a:b] *= 0.05
    path = tmp_path / "repeat.wav"
    _write_stereo(path, base, sr=sr)
    obs = compute_fullmix_observation(path, region_id="REP", use_cache=False)
    dips = [ev for ev in obs.energy_events if ev.kind is EnergyEventKind.STRONG_ENERGY_DIP]
    assert len(dips) >= 3
    assert max(ev.event_similarity_count for ev in dips) >= 3
    assert any((ev.approx_period_s or 0) > 0 for ev in dips)


def test_constant_level(tmp_path: Path) -> None:
    path = tmp_path / "flat.wav"
    _write_stereo(path, _tone(44100, 1.5, amp=0.2))
    obs = compute_fullmix_observation(path, region_id="FLAT", use_cache=False)
    assert not any(ev.kind is EnergyEventKind.SILENCE for ev in obs.energy_events)
    assert not any(ev.kind is EnergyEventKind.STRONG_ENERGY_DIP for ev in obs.energy_events)


def test_spectral_only_change(tmp_path: Path) -> None:
    sr = 44100
    low = _tone(sr, 1.0, freq=80.0, amp=0.3)
    high = _tone(sr, 1.0, freq=4000.0, amp=0.3)
    sig = np.concatenate([low, high])
    path = tmp_path / "spec.wav"
    _write_stereo(path, sig, sr=sr)
    obs = compute_fullmix_observation(path, region_id="SPEC", use_cache=False)
    assert obs.spectral_trajectory
    text = " ".join([obs.analyzer_id, *[ev.band for ev in obs.spectral_events], *obs.limitations]).lower()
    assert "groove problem" not in text
    assert "dropout problem" not in text


def test_transient_density_change(tmp_path: Path) -> None:
    sr = 44100
    sig = np.zeros(int(sr * 2.0), dtype=np.float64)
    # sparse clicks then dense clicks
    for t in (0.2, 0.8):
        i = int(t * sr)
        sig[i : i + 40] = 0.9
    for t in np.linspace(1.1, 1.9, 16):
        i = int(t * sr)
        sig[i : i + 20] = 0.9
    path = tmp_path / "trans.wav"
    _write_stereo(path, sig, sr=sr)
    obs = compute_fullmix_observation(path, region_id="TR", use_cache=False)
    assert obs.transient is not None
    assert obs.transient.transient_count >= 2


def test_stereo_width_change(tmp_path: Path) -> None:
    sr = 44100
    mono = _tone(sr, 1.0, amp=0.2)
    wide_l = _tone(sr, 1.0, freq=220.0, amp=0.2)
    wide_r = _tone(sr, 1.0, freq=330.0, amp=0.2)
    left = np.concatenate([mono, wide_l])
    right = np.concatenate([mono, wide_r])
    path = tmp_path / "stereo.wav"
    _write_stereo(path, left, right, sr=sr)
    obs = compute_fullmix_observation(path, region_id="ST", use_cache=False)
    assert obs.stereo_frames
    assert any(f.correlation is not None for f in obs.stereo_frames)


def test_cache_hit(tmp_path: Path, monkeypatch) -> None:
    from copilot.audio import fullmix as fm

    monkeypatch.setattr(fm, "CACHE_DIR", tmp_path / "cache")
    path = tmp_path / "c.wav"
    _write_stereo(path, _tone(44100, 0.5, amp=0.1))
    first = compute_fullmix_observation(path, region_id="C", use_cache=True)
    second = compute_fullmix_observation(path, region_id="C", use_cache=True)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.configuration_hash == configuration_hash()


def test_observation_not_diagnosis_language(tmp_path: Path) -> None:
    path = tmp_path / "ok.wav"
    _write_stereo(path, _tone(44100, 0.4, amp=0.1))
    obs = compute_fullmix_observation(path, region_id="OK", use_cache=False)
    text = " ".join(
        [
            obs.analyzer_id,
            obs.region_id,
            *obs.limitations,
            *[ev.kind.value for ev in obs.energy_events],
        ]
    ).lower()
    for term in ("bad", "good", "groove problem", "dropout problem", "needs fixing", "masking", "boring"):
        assert term not in text.split() if " " not in term else term not in text


def test_merge_into_evidence_pack(tmp_path: Path) -> None:
    path = tmp_path / "m.wav"
    _write_stereo(path, _tone(44100, 0.5, amp=0.15))
    obs = compute_fullmix_observation(path, region_id="REGION_X", region_label="0->8qn", use_cache=False)
    from copilot.reasoning.fixtures import pack_clear_no_action

    low = pack_clear_no_action()
    low.pack_id = "REGION_X"
    merged = merge_lowend_and_fullmix(low, obs)
    assert any(item.evidence_id.startswith("fm.") for item in merged.items)
    assert merged.domain.startswith("fullmix")
    items = fullmix_items(obs, project_token="p", audible_token="a")
    assert items
