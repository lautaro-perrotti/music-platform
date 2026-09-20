import pytest

from copilot.audio.reference_analysis_v1 import (
    REFERENCE_WINDOW_BEATS,
    build_reference_analysis_pack,
    reference_window_spans,
    pack_from_fullmix_observation,
)


def test_reference_windows_are_consecutive_32_bar_windows():
    spans = reference_window_spans(300)
    assert spans == [
        (0.0, REFERENCE_WINDOW_BEATS),
        (REFERENCE_WINDOW_BEATS, REFERENCE_WINDOW_BEATS * 2),
        (REFERENCE_WINDOW_BEATS * 2, 300),
    ]


def test_reference_pack_attaches_measurements_to_windows():
    pack = build_reference_analysis_pack(
        reference_state_token="reference:r1",
        target_state_token="target:t1",
        total_beats=256,
        measurements=[{"energy_db": -12.0}, {"low_band_energy": 0.72}],
    )
    assert len(pack.windows) == 2
    assert pack.windows[0].energy_db == -12.0
    assert pack.windows[1].low_band_energy == 0.72
    assert pack.no_write is True


def test_reference_pack_rejects_extra_measurements():
    with pytest.raises(ValueError):
        build_reference_analysis_pack(
            reference_state_token="r",
            target_state_token="t",
            total_beats=128,
            measurements=[{}, {}],
        )


def test_invalid_window_inputs_fail_closed():
    with pytest.raises(ValueError):
        reference_window_spans(-1)


def test_fullmix_projection_aggregates_factual_frames():
    class Frame:
        t_s = 1.0
        relative_db = -10.0

    class Dynamic:
        window_start_s = 1.0
        crest_factor = 3.0

    class Observation:
        duration_s = 1.5
        energy_frames = [Frame()]
        dynamics = [Dynamic()]
        spectral_trajectory = []

    pack = pack_from_fullmix_observation(
        Observation(), reference_state_token="r", target_state_token="t", tempo_bpm=120
    )
    assert pack.windows[0].energy_db == -10.0
    assert pack.windows[0].crest_factor_db == 3.0
