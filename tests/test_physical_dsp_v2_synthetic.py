from __future__ import annotations

from pathlib import Path

import numpy as np

from copilot.audio.physical_dsp_v2.audio import buffer_from_samples
from copilot.audio.physical_dsp_v2.fixtures import (
    dynamic_envelope,
    hard_pan_left,
    identical_stereo,
    impulse_train,
    mixture,
    mono_sine,
    phase_inverted,
    silence,
    sine,
)
from copilot.audio.physical_dsp_v2.pipeline import analyze_buffer, analyze_pair
from copilot.schemas.dsp import AnalyzerFamily, DspQuality


def _cache(tmp_path: Path) -> Path:
    return tmp_path / "pdsp_cache"


def test_silence_levels(tmp_path: Path) -> None:
    bundle = analyze_buffer(
        buffer_from_samples(silence(44100, 1.0), 44100),
        analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    )
    level = bundle.by_analyzer(AnalyzerFamily.LEVEL_DYNAMICS.value)[0]
    assert level.get("rms").value < 1e-6
    assert level.get("silence_fraction").value >= 0.9
    assert level.get("integrated_loudness").value is None
    assert "LUFS_MEASUREMENT_FAILED" in level.limitation_codes() or "LUFS" in " ".join(level.limitation_codes())


def test_sine_spectrum_and_tonal(tmp_path: Path) -> None:
    bundle = analyze_buffer(
        buffer_from_samples(sine(44100, 1.2, 440.0, 0.25), 44100),
        analyzers=(AnalyzerFamily.SPECTRUM.value, AnalyzerFamily.TONAL.value, AnalyzerFamily.TIMBRE.value),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    )
    spec = bundle.by_analyzer(AnalyzerFamily.SPECTRUM.value)[0]
    centroid = spec.get("spectral_centroid_hz").value
    assert centroid is not None
    assert abs(centroid - 440.0) < 40.0
    tonal = bundle.by_analyzer(AnalyzerFamily.TONAL.value)[0]
    chroma = tonal.get("chroma").value
    assert max(chroma, key=lambda k: chroma[k]) == "9"
    candidates = tonal.get("key_candidates").value
    assert candidates
    labels = {row["label"] for row in candidates}
    assert any(label.startswith("A ") for label in labels)
    assert tonal.get("selected_key").value is None
    assert "KEY_IS_CANDIDATE_SET_NOT_CERTAIN" in tonal.limitation_codes()
    timbre = bundle.by_analyzer(AnalyzerFamily.TIMBRE.value)[0]
    assert timbre.get("zero_crossing_rate").value > 0


def test_known_frequency_mixture(tmp_path: Path) -> None:
    audio = mixture(44100, 1.2, [(70.0, 0.3), (400.0, 0.2), (3000.0, 0.2), (9000.0, 0.15)])
    spec = analyze_buffer(
        buffer_from_samples(audio, 44100),
        analyzers=(AnalyzerFamily.SPECTRUM.value,),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    ).by_analyzer(AnalyzerFamily.SPECTRUM.value)[0]
    ratios = spec.get("band_energy_ratio").value
    assert ratios["low"] > 0
    assert ratios["mid"] > 0
    assert ratios["high_mid"] > 0
    assert ratios["high"] > 0


def test_impulse_train_transients_and_rhythm(tmp_path: Path) -> None:
    audio = impulse_train(44100, 2.0, 0.5)
    bundle = analyze_buffer(
        buffer_from_samples(audio, 44100),
        analyzers=(AnalyzerFamily.TRANSIENTS.value, AnalyzerFamily.RHYTHM.value),
        tempo_bpm=120.0,
        start_qn=0.0,
        use_cache=False,
        cache_dir=_cache(tmp_path),
    )
    trans = bundle.by_analyzer(AnalyzerFamily.TRANSIENTS.value)[0]
    assert trans.get("onset_count").value >= 3
    rhythm = bundle.by_analyzer(AnalyzerFamily.RHYTHM.value)[0]
    period = rhythm.get("periodicity_s").value
    assert period is not None
    assert abs(period - 0.5) < 0.08
    offsets = rhythm.get("timing_offsets_s").value
    assert offsets
    assert float(np.mean(np.abs(offsets))) < 0.06


def test_stereo_fixtures(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    mono = analyze_buffer(
        buffer_from_samples(mono_sine(), 44100),
        analyzers=(AnalyzerFamily.STEREO.value,),
        use_cache=False,
        cache_dir=cache,
    ).by_analyzer(AnalyzerFamily.STEREO.value)[0]
    assert mono.quality is DspQuality.LIMITED
    assert "MONO_OR_SINGLE_CHANNEL" in mono.limitation_codes()

    ident = analyze_buffer(
        buffer_from_samples(identical_stereo(), 44100),
        analyzers=(AnalyzerFamily.STEREO.value,),
        use_cache=False,
        cache_dir=cache,
    ).by_analyzer(AnalyzerFamily.STEREO.value)[0]
    assert ident.get("correlation").value > 0.99
    assert ident.get("side_energy").value < 1e-6

    inv = analyze_buffer(
        buffer_from_samples(phase_inverted(), 44100),
        analyzers=(AnalyzerFamily.STEREO.value,),
        use_cache=False,
        cache_dir=cache,
    ).by_analyzer(AnalyzerFamily.STEREO.value)[0]
    assert inv.get("correlation").value < -0.99

    pan = analyze_buffer(
        buffer_from_samples(hard_pan_left(), 44100),
        analyzers=(AnalyzerFamily.STEREO.value,),
        use_cache=False,
        cache_dir=cache,
    ).by_analyzer(AnalyzerFamily.STEREO.value)[0]
    assert pan.get("left_rms").value > 10 * max(pan.get("right_rms").value, 1e-12)


def test_known_level_difference_and_envelope(tmp_path: Path) -> None:
    loud = analyze_buffer(
        buffer_from_samples(sine(44100, 1.0, 200.0, 0.2), 44100),
        analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    ).by_analyzer(AnalyzerFamily.LEVEL_DYNAMICS.value)[0]
    quiet = analyze_buffer(
        buffer_from_samples(sine(44100, 1.0, 200.0, 0.1), 44100),
        analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    ).by_analyzer(AnalyzerFamily.LEVEL_DYNAMICS.value)[0]
    delta = loud.get("rms_dbfs").value - quiet.get("rms_dbfs").value
    assert abs(delta - 6.02) < 0.3
    env = analyze_buffer(
        buffer_from_samples(dynamic_envelope(), 44100),
        analyzers=(AnalyzerFamily.LEVEL_DYNAMICS.value,),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    ).by_analyzer(AnalyzerFamily.LEVEL_DYNAMICS.value)[0]
    assert env.get("level_change_db").value > 10.0


def test_relational_overlap_and_disjoint(tmp_path: Path) -> None:
    same = sine(44100, 1.0, 70.0, 0.3)
    other = sine(44100, 1.0, 8000.0, 0.3)
    close = analyze_pair(
        buffer_from_samples(same, 44100),
        buffer_from_samples(same, 44100),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    ).observations[0]
    far = analyze_pair(
        buffer_from_samples(same, 44100),
        buffer_from_samples(other, 44100),
        use_cache=False,
        cache_dir=_cache(tmp_path),
    ).observations[0]
    assert close.get("potential_overlap").value is True
    assert close.get("mean_joint_share").value > far.get("mean_joint_share").value
