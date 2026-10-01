from __future__ import annotations

from copilot.audio.drum_detector_calibration_v1 import calibrate_rms_flux_detector


def test_rms_flux_calibration_is_repeatable_and_scoped_to_synthetic_prototypes():
    first = calibrate_rms_flux_detector()
    second = calibrate_rms_flux_detector()

    assert first.calibration_id == second.calibration_id
    assert first.model_dump() == second.model_dump()
    assert first.applicability == "SYNTHETIC_PROTOTYPES_ONLY"
    assert first.source_audio_calibrated is False
    assert first.correction_applied is False
    assert len(first.trials) == 6
    assert all(trial.unmatched_expected_count == 0 for trial in first.trials)
    assert all(len(trial.signed_errors_ms) == 4 for trial in first.trials)
    errors = [error for trial in first.trials for error in trial.signed_errors_ms]
    assert all(-20.0 <= error <= -13.0 for error in errors)
    errors_48k = [
        error for trial in first.trials if trial.sample_rate == 48_000
        for error in trial.signed_errors_ms
    ]
    assert set(errors_48k) == {-15.0}
    errors_44k = [
        error for trial in first.trials if trial.sample_rate == 44_100
        for error in trial.signed_errors_ms
    ]
    assert max(errors_44k) - min(errors_44k) > 4.0
    assert "MUSICAL_MICROTIMING_REMAINS_PROVISIONAL" in first.limitations


def test_rms_flux_calibration_rejects_invalid_sample_rate():
    try:
        calibrate_rms_flux_detector((1_000,))
    except ValueError as exc:
        assert str(exc) == "DRUM_CALIBRATION_SAMPLE_RATES_INVALID"
    else:
        raise AssertionError("invalid sample rate must be rejected")
