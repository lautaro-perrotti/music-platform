from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from copilot.audio.live_capture import (
    DURATION_TOLERANCE_S,
    AudioAsset,
    AudioCaptureError,
    capture_audio_segment,
    capture_dir,
    ensure_internal_source,
    ensure_master_tap,
    ensure_silent_track,
    find_master_tap,
    find_onset_samples,
    four_bar_region,
    observe_asset,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError


def run_live21(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "phase": "LIVE-2.1",
        "PRECISION REGION": "NOT_STARTED",
        "TRACK CAPTURE": "NOT_STARTED",
        "MUSIC OBSERVATION": "NOT_STARTED",
        "MASTER PRECISE CAPTURE": "NOT_STARTED",
        "TRACK PRECISE CAPTURE": "NOT_STARTED",
    }
    if daw.handshake_info.get("bridge_version") != "abletonmcp-vendored-live2":
        daw.strict_capabilities = False

    try:
        ensure_master_tap(daw)
        source = ensure_internal_source(daw)
        silent = ensure_silent_track(daw)
        session = daw.snapshot(include_notes=False)
        drift = session.track_by_name(source["track_name"])
        silent_track = session.track_by_name(silent["track_name"])
        if drift is None or silent_track is None:
            raise AudioCaptureError("TAP_MISSING", "source tracks missing after setup")
        start_beat, end_beat, tempo = four_bar_region(daw)
        report["tempo"] = tempo
        report["signature"] = [
            session.transport.signature_numerator,
            session.transport.signature_denominator,
        ]
        report["requested_full"] = {"start_beat": start_beat, "end_beat": end_beat}
        report["source"] = {**source, "stable_id": drift.stable_id}
        report["silent"] = {**silent, "stable_id": silent_track.stable_id}
        mixer_before = _mixer_names(daw)
        tap_before = daw.get_master_info()
    except (AudioCaptureError, DawError) as exc:
        report["PRECISION REGION"] = "BLOCKED"
        report["error"] = str(exc)
        _write(evidence, report)
        return report

    try:
        full = capture_audio_segment(
            daw, "MASTER", start_beat, end_beat, fire_track=drift.index
        )
        late = capture_audio_segment(
            daw, "MASTER", 8.0, end_beat, fire_track=drift.index
        )
        early = capture_audio_segment(
            daw, "MASTER", start_beat, 8.0, fire_track=drift.index
        )
    except AudioCaptureError as exc:
        report["PRECISION REGION"] = "FAILED"
        report["MASTER PRECISE CAPTURE"] = "FAILED"
        report["error"] = str(exc)
        _write(evidence, report)
        return report

    expected_full = (end_beat - start_beat) * 60.0 / tempo
    expected_half = 8.0 * 60.0 / tempo
    report["capture_0_16"] = full.model_dump()
    report["capture_8_16"] = late.model_dump()
    report["capture_0_8"] = early.model_dump()
    region_ok, region_errors = _validate_regions(
        full, late, early, expected_full, expected_half
    )
    report["region_check"] = {"ok": region_ok, "errors": region_errors}
    if not region_ok:
        report["PRECISION REGION"] = "FAILED"
        report["MASTER PRECISE CAPTURE"] = "FAILED"
        report["error"] = region_errors
        _write(evidence, report)
        return report
    report["PRECISION REGION"] = "VERIFIED"
    report["MASTER PRECISE CAPTURE"] = "VERIFIED"

    try:
        master = capture_audio_segment(
            daw, "MASTER", start_beat, end_beat, fire_track=drift.index
        )
        drift_cap = capture_audio_segment(
            daw, drift.stable_id, start_beat, end_beat
        )
        silent_cap = capture_audio_segment(
            daw,
            silent_track.stable_id,
            start_beat,
            end_beat,
            require_signal=False,
        )
    except AudioCaptureError as exc:
        report["TRACK CAPTURE"] = "FAILED"
        report["TRACK PRECISE CAPTURE"] = "FAILED"
        report["error"] = str(exc)
        _write(evidence, report)
        return report

    report["capture_master"] = master.model_dump()
    report["capture_drift"] = drift_cap.model_dump()
    report["capture_silent"] = silent_cap.model_dump()
    track_ok = (
        (master.rms or 0) > 1e-4
        and (drift_cap.rms or 0) > 1e-4
        and (silent_cap.rms or 0) < 1e-4
        and abs(master.analysis_duration - expected_full) <= DURATION_TOLERANCE_S
        and abs(drift_cap.analysis_duration - expected_full) <= DURATION_TOLERANCE_S
        and abs(silent_cap.analysis_duration - expected_full) <= DURATION_TOLERANCE_S
        and drift_cap.source_type == "TRACK"
        and drift_cap.source_stable_id == drift.stable_id
        and silent_cap.source_stable_id == silent_track.stable_id
    )
    report["track_provenance"] = {
        "master_rms": master.rms,
        "drift_rms": drift_cap.rms,
        "silent_rms": silent_cap.rms,
        "passed": track_ok,
    }
    if not track_ok:
        report["TRACK CAPTURE"] = "FAILED"
        report["TRACK PRECISE CAPTURE"] = "FAILED"
        _write(evidence, report)
        return report
    report["TRACK CAPTURE"] = "VERIFIED"
    report["TRACK PRECISE CAPTURE"] = "VERIFIED"

    try:
        observation = observe_asset(
            late, region=f"{late.requested_start_beat}:{late.requested_end_beat}", tempo=tempo
        )
        if observation.source.startswith("offline") or "LIVE_" not in observation.source:
            raise AudioCaptureError("SILENT_UNEXPECTED", "observation not from Live analysis wav")
        if abs(observation.signal.duration_seconds - expected_half) > DURATION_TOLERANCE_S:
            raise AudioCaptureError(
                "SHORT_CAPTURE",
                f"observation duration {observation.signal.duration_seconds}",
            )
        report["observation"] = observation.model_dump()
        report["MUSIC OBSERVATION"] = "VERIFIED"
    except Exception as exc:  # noqa: BLE001
        report["MUSIC OBSERVATION"] = "FAILED"
        report["error"] = str(exc)
        _write(evidence, report)
        return report

    repeat = capture_audio_segment(
        daw, drift.stable_id, start_beat, end_beat
    )
    report["repeat"] = {
        "duration_a": drift_cap.analysis_duration,
        "duration_b": repeat.analysis_duration,
        "rms_a": drift_cap.rms,
        "rms_b": repeat.rms,
        "peak_a": drift_cap.peak,
        "peak_b": repeat.peak,
        "duration_delta": abs(repeat.analysis_duration - drift_cap.analysis_duration),
    }

    mixer_after = _mixer_names(daw)
    tap_after = find_master_tap(daw)
    restored = mixer_after == mixer_before and tap_after is not None
    report["project_restored"] = restored
    report["tap_after"] = tap_after
    if not restored:
        report["TRACK PRECISE CAPTURE"] = "FAILED"
        report["error"] = "project state not restored"
        _write(evidence, report)
        return report

    report["file"] = late.analysis_file_path
    report["raw_file"] = late.raw_file_path
    report["sample_rate"] = late.sample_rate
    report["channels"] = late.channels
    report["analysis_duration"] = late.analysis_duration
    report["raw_duration"] = late.raw_duration
    report["rms"] = late.rms
    report["peak"] = late.peak
    report["lufs"] = observation.signal.lufs
    report["limitations"] = [
        "constant tempo only; TEMPO_AUTOMATION_UNSUPPORTED otherwise",
        "track capture via exclusive solo into the verified Master tap",
        "session clips fired from clip start; region isolated by onset+beat trim",
        "returns may still leak into a soloed track capture",
    ]
    _write(evidence, report)
    return report


def _validate_regions(
    full: AudioAsset,
    late: AudioAsset,
    early: AudioAsset,
    expected_full: float,
    expected_half: float,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for name, asset, expected in (
        ("0-16", full, expected_full),
        ("8-16", late, expected_half),
        ("0-8", early, expected_half),
    ):
        if abs(asset.analysis_duration - expected) > DURATION_TOLERANCE_S:
            errors.append(
                f"{name} analysis {asset.analysis_duration:.4f}s != {expected:.4f}s"
            )
    early_c = _centroid(early.analysis_file_path)
    late_c = _centroid(late.analysis_file_path)
    raw_first_half = _centroid_raw_prefix(full.raw_file_path, expected_half)
    if late_c <= early_c * 1.3:
        errors.append(
            f"8-16 centroid {late_c:.1f} not above 0-8 centroid {early_c:.1f}"
        )
    if abs(late_c - raw_first_half) < abs(late_c - early_c):
        # late region should look like high notes, not the first N seconds of RAW
        if late_c < raw_first_half * 1.2:
            errors.append(
                f"8-16 looks like RAW prefix (late={late_c:.1f} prefix={raw_first_half:.1f})"
            )
    return not errors, errors


def _centroid(path: str) -> float:
    data, sr = sf.read(path, always_2d=True)
    mono = np.mean(data, axis=1)
    windowed = mono * np.hanning(len(mono))
    spectrum = np.abs(np.fft.rfft(windowed)) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sr)
    denom = float(np.sum(spectrum))
    if denom <= 0:
        return 0.0
    return float(np.sum(freqs * spectrum) / denom)


def _centroid_raw_prefix(path: str, seconds: float) -> float:
    data, sr = sf.read(path, always_2d=True)
    onset = find_onset_samples(data) or 0
    end = min(len(data), onset + int(seconds * sr))
    tmp = Path(path).with_name(Path(path).stem + "_prefix.wav")
    sf.write(str(tmp), data[onset:end], sr)
    try:
        return _centroid(str(tmp))
    finally:
        tmp.unlink(missing_ok=True)


def _mixer_names(daw: AbletonTcpAdapter) -> list[dict[str, object]]:
    count = int(daw.health().get("track_count") or 0)
    rows = []
    for index in range(count):
        info = daw.get_track_info(index)
        rows.append(
            {
                "index": index,
                "name": info.get("name"),
                "mute": bool(info.get("mute", False)),
                "solo": bool(info.get("solo", False)),
            }
        )
    return rows


def _write(evidence: Path, report: dict[str, Any]) -> None:
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "live21_precise_capture.json").write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
