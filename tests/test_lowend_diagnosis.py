from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.diagnose import (
    diagnoses_equivalent,
    diagnose_lowend,
    primary_finding_types,
    unstable_insufficient,
)
from copilot.audio.live_capture import AudioAsset
from copilot.audio.lowend import band_energy_over_time, detect_transients
from copilot.schemas.diagnosis import FindingType
from copilot.schemas.observation import CaptureView, SignalPoint


def _tone(sr: int, freq: float, dur: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(sr * dur)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _click(sr: int, n: int = 400) -> np.ndarray:
    x = np.zeros(n, dtype=np.float32)
    x[:80] = 0.9
    return x


def _asset(path: Path, start: float, end: float, view: CaptureView, point: SignalPoint) -> AudioAsset:
    data, sr = sf.read(str(path), always_2d=True)
    return AudioAsset(
        capture_id=path.stem,
        raw_file_path=str(path),
        analysis_file_path=str(path),
        file_path=str(path),
        requested_start_beat=start,
        requested_end_beat=end,
        start_beat=start,
        end_beat=end,
        sample_rate=int(sr),
        channels=int(data.shape[1]),
        raw_duration=len(data) / sr,
        analysis_duration=len(data) / sr,
        duration=len(data) / sr,
        session_revision=1,
        capture_view=view,
        signal_point=point,
        analysis_start_beat=start,
        analysis_end_beat=end,
    )


def _write_pair(tmp: Path, kick: np.ndarray, bass: np.ndarray, master: np.ndarray, sr: int):
    paths = {}
    for name, audio in (("kick", kick), ("bass", bass), ("master", master)):
        path = tmp / f"{name}.wav"
        stereo = np.column_stack([audio, audio])
        sf.write(str(path), stereo, sr, subtype="FLOAT")
        paths[name] = path
    return paths


def test_stft_has_configured_bands() -> None:
    sr = 44100
    x = _tone(sr, 70, 1.0)
    spec = band_energy_over_time(x, sr)
    assert "60-80" in spec["bands"]
    assert np.mean(spec["bands"]["60-80"]) > np.mean(spec["bands"]["200-300"])


def test_transients_find_periodic_clicks() -> None:
    sr = 44100
    x = np.zeros(sr * 2, dtype=np.float32)
    for t in (0.0, 0.5, 1.0, 1.5):
        i = int(t * sr)
        x[i : i + 400] = _click(sr)
    hits = detect_transients(x, sr, role="kick")
    assert hits["count"] >= 3


def test_clean_fixture_allows_no_action(tmp_path: Path) -> None:
    sr = 44100
    n = sr * 2
    kick = np.zeros(n, dtype=np.float32)
    bass = np.zeros(n, dtype=np.float32)
    for t in (0.0, 0.5, 1.0, 1.5):
        i = int(t * sr)
        kick[i : i + 300] += _click(sr, 300) * 0.5
        # bass only in the gap, different band
        start = i + int(0.18 * sr)
        end = i + int(0.42 * sr)
        bass[start:end] += _tone(sr, 110, (end - start) / sr, 0.2)
    paths = _write_pair(tmp_path, kick, bass, kick + bass, sr)
    views = {
        name: _asset(paths[name], 0.0, 16.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER)
        if name != "master"
        else _asset(paths[name], 0.0, 16.0, CaptureView.MASTER_CONTEXT, SignalPoint.MAIN_FINAL)
        for name in paths
    }
    diag = diagnose_lowend(views, kick_name="Kick", bass_name="Bass")
    types = {f.type for f in diag.findings}
    assert FindingType.NO_ACTION_REQUIRED in types
    assert diag.no_change_is_valid is True
    assert diag.feature_sources["kick_events"]["view"] == "TRACK_ISOLATED"
    assert diag.feature_sources["bass_envelope"]["view"] == "TRACK_ISOLATED"


def test_temporal_collision_is_detected(tmp_path: Path) -> None:
    sr = 44100
    n = sr * 2
    kick = np.zeros(n, dtype=np.float32)
    bass = _tone(sr, 55, 2.0, 0.25)
    for t in (0.0, 0.5, 1.0, 1.5):
        i = int(t * sr)
        kick[i : i + 250] += _click(sr, 250)
    paths = _write_pair(tmp_path, kick, bass, kick + bass, sr)
    views = {
        "kick": _asset(paths["kick"], 0.0, 16.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER),
        "bass": _asset(paths["bass"], 0.0, 16.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER),
        "master": _asset(paths["master"], 0.0, 16.0, CaptureView.MASTER_CONTEXT, SignalPoint.MAIN_FINAL),
    }
    diag = diagnose_lowend(views, kick_name="Kick", bass_name="Bass")
    types = {f.type for f in diag.findings}
    assert FindingType.TEMPORAL_MASKING in types
    assert "THROUGH_MASTER_CHAIN" not in diag.limitations
    assert "phase cancellation" not in diag.user_facing.lower()
    assert "cancelación de fase" not in diag.user_facing.lower()


def test_spectral_collision_is_detected(tmp_path: Path) -> None:
    sr = 44100
    n = sr * 2
    kick = np.zeros(n, dtype=np.float32)
    bass = np.zeros(n, dtype=np.float32)
    for t in (0.0, 0.5, 1.0, 1.5):
        i = int(t * sr)
        kick[i : i + 300] += _click(sr, 300)
        burst = _tone(sr, 70, 0.12, 0.45)
        kick[i : i + len(burst)] += burst
        bass[i : i + len(burst)] += burst
    paths = _write_pair(tmp_path, kick, bass, kick + bass, sr)
    views = {
        "kick": _asset(paths["kick"], 0.0, 16.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER),
        "bass": _asset(paths["bass"], 0.0, 16.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER),
        "master": _asset(paths["master"], 0.0, 16.0, CaptureView.MASTER_CONTEXT, SignalPoint.MAIN_FINAL),
    }
    diag = diagnose_lowend(views, kick_name="Kick", bass_name="Bass")
    types = {f.type for f in diag.findings}
    assert FindingType.SPECTRAL_MASKING in types
    assert "sidechain" not in diag.user_facing.lower()


def test_clean_negative_assertions(tmp_path: Path) -> None:
    sr = 44100
    n = sr * 2
    kick = np.zeros(n, dtype=np.float32)
    bass = np.zeros(n, dtype=np.float32)
    for t in (0.0, 0.5, 1.0, 1.5):
        i = int(t * sr)
        kick[i : i + 300] += _click(sr, 300) * 0.5
        start = i + int(0.22 * sr)
        end = i + int(0.40 * sr)
        bass[start:end] += _tone(sr, 130, (end - start) / sr, 0.22)
    paths = _write_pair(tmp_path, kick, bass, kick + bass, sr)
    views = {
        "kick": _asset(paths["kick"], 0.0, 16.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER),
        "bass": _asset(paths["bass"], 0.0, 16.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER),
        "master": _asset(paths["master"], 0.0, 16.0, CaptureView.MASTER_CONTEXT, SignalPoint.MAIN_FINAL),
    }
    diag = diagnose_lowend(views, kick_name="Kick", bass_name="Bass")
    types = {f.type for f in diag.findings}
    text = diag.user_facing.lower()
    assert FindingType.NO_ACTION_REQUIRED in types
    assert FindingType.TEMPORAL_MASKING not in types
    assert FindingType.SPECTRAL_MASKING not in types
    assert "severe" not in text
    assert "phase" not in text
    assert diag.primary_hypothesis is not None
    assert diag.no_change_is_valid is True


def test_mismatched_region_is_rejected(tmp_path: Path) -> None:
    sr = 44100
    x = _tone(sr, 60, 1.0)
    path = tmp_path / "a.wav"
    sf.write(str(path), np.column_stack([x, x]), sr)
    kick = _asset(path, 0.0, 16.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER)
    bass = _asset(path, 1.0, 17.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER)
    master = _asset(path, 0.0, 16.0, CaptureView.MASTER_CONTEXT, SignalPoint.MAIN_FINAL)
    try:
        diagnose_lowend(
            {"kick": kick, "bass": bass, "master": master},
            kick_name="K",
            bass_name="B",
        )
        raise AssertionError("region mismatch must fail")
    except ValueError as exc:
        assert "region" in str(exc)


def test_same_wavs_twice_are_deterministic(tmp_path: Path) -> None:
    sr = 44100
    n = sr * 2
    kick = np.zeros(n, dtype=np.float32)
    bass = 0.2 * _tone(sr, 55, 2.0)
    for t in (0.0, 0.5, 1.0, 1.5):
        i = int(t * sr)
        kick[i : i + 400] = _click(sr)
    master = np.clip(kick + bass, -1, 1)
    paths = _write_pair(tmp_path, kick, bass, master, sr)
    views = {
        "kick": _asset(paths["kick"], 0.0, 4.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER),
        "bass": _asset(paths["bass"], 0.0, 4.0, CaptureView.TRACK_ISOLATED, SignalPoint.TRACK_POST_MIXER),
        "master": _asset(paths["master"], 0.0, 4.0, CaptureView.MASTER_CONTEXT, SignalPoint.MAIN_FINAL),
    }
    first = diagnose_lowend(views, kick_name="K", bass_name="B")
    second = diagnose_lowend(views, kick_name="K", bass_name="B")
    assert diagnoses_equivalent(first, second)
    assert primary_finding_types(first) == primary_finding_types(second)


def test_unstable_policy_does_not_pick_a_side() -> None:
    from copilot.schemas.diagnosis import (
        Confidence,
        EvidenceStatus,
        Finding,
        FindingType,
        MusicDiagnosis,
    )

    a = MusicDiagnosis(
        diagnosis_id="a",
        region="0->8 quarter_note",
        targets={"kick": "K", "bass": "B"},
        findings=[
            Finding(
                type=FindingType.NO_ACTION_REQUIRED,
                status=EvidenceStatus.INFERRED,
                summary="none",
            )
        ],
        confidence=Confidence.LOW,
    )
    b = a.model_copy(deep=True)
    b.findings = [
        Finding(
            type=FindingType.TEMPORAL_MASKING,
            status=EvidenceStatus.MEASURED,
            summary="mask",
        )
    ]
    out = unstable_insufficient(
        left=a, right=b, region="0->8 quarter_note", kick_name="K", bass_name="B"
    )
    assert primary_finding_types(out) == ["INSUFFICIENT_EVIDENCE"]
    assert out.structured_evidence["reason"] == "DIAGNOSIS_UNSTABLE"
    assert out.candidate_actions[0].action_type == "NO_CHANGE"
