from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copilot.audio.live_capture import (
    AudioAsset,
    AudioCaptureError,
    capture_dir,
    capture_master_segment,
    ensure_internal_source,
    ensure_master_tap,
    find_master_tap,
    four_bar_region,
    install_audio_tap_device,
    observe_asset,
    validate_wav,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError

SILENCE_RATIO = 10.0


def run_live2(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "phase": "LIVE-2",
        "MAX AUDIO TAP": "NOT_STARTED",
        "LIVE MASTER CAPTURE": "NOT_STARTED",
        "WAV VALIDATION": "NOT_STARTED",
        "PROVENANCE A/B": "NOT_STARTED",
        "COPILOT CONTROL": "NOT_STARTED",
        "MUSIC OBSERVATION": "NOT_STARTED",
        "AUDIO CAPTURE": "NOT_STARTED",
    }
    if daw.handshake_info.get("bridge_version") != "abletonmcp-vendored-live2":
        daw.strict_capabilities = False
        report["script_reload_required"] = True
        report["handshake"] = daw.handshake_info
    try:
        health = daw.health()
    except DawError as exc:
        report["LIVE MASTER CAPTURE"] = "BLOCKED"
        report["error"] = f"REMOTE_DISCONNECT: {exc}"
        _write(evidence, report)
        return report
    report["health"] = health
    report["capture_dir"] = str(capture_dir())

    try:
        installed = install_audio_tap_device()
        tap = ensure_master_tap(daw)
        report["tap_install"] = installed
        report["tap"] = tap
        report["MAX AUDIO TAP"] = "VERIFIED"
    except AudioCaptureError as exc:
        report["MAX AUDIO TAP"] = "BLOCKED" if exc.code == "TAP_MISSING" else "FAILED"
        report["error"] = str(exc)
        _write(evidence, report)
        return report

    try:
        source = ensure_internal_source(daw)
        start_beat, end_beat, tempo = four_bar_region(daw)
        source_track = int(source["track_index"])
        report["source"] = source
        report["region"] = {
            "start_beat": start_beat,
            "end_beat": end_beat,
            "tempo": tempo,
        }
    except (AudioCaptureError, DawError) as exc:
        report["LIVE MASTER CAPTURE"] = "BLOCKED"
        report["error"] = str(exc)
        _write(evidence, report)
        return report

    mute_before = bool(daw.get_track_info(source_track).get("mute", False))
    if mute_before:
        daw.set_track_mute(source_track, False)

    try:
        capture_a = capture_master_segment(
            daw,
            start_beat,
            end_beat,
            source_track=source_track,
            require_signal=True,
        )
        report["capture_a"] = capture_a.model_dump()
        report["LIVE MASTER CAPTURE"] = "VERIFIED"
        report["COPILOT CONTROL"] = "VERIFIED"
    except AudioCaptureError as exc:
        report["LIVE MASTER CAPTURE"] = "FAILED"
        report["COPILOT CONTROL"] = "FAILED"
        report["error"] = str(exc)
        _restore_mute(daw, source_track, mute_before)
        _write(evidence, report)
        return report

    check_a = validate_wav(
        Path(capture_a.file_path),
        expected_sr=_live_sr(daw, capture_a),
        expected_duration=capture_a.duration,
        require_signal=True,
    )
    report["wav_a"] = check_a.model_dump()
    if not check_a.ok:
        report["WAV VALIDATION"] = "FAILED"
        report["error"] = check_a.errors
        _restore_mute(daw, source_track, mute_before)
        _write(evidence, report)
        return report
    report["WAV VALIDATION"] = "VERIFIED"

    daw.set_track_mute(source_track, True)
    try:
        capture_b = capture_master_segment(
            daw,
            start_beat,
            end_beat,
            source_track=source_track,
            require_signal=False,
        )
        report["capture_b"] = capture_b.model_dump()
    except AudioCaptureError as exc:
        report["PROVENANCE A/B"] = "FAILED"
        report["error"] = str(exc)
        _restore_mute(daw, source_track, mute_before)
        _write(evidence, report)
        return report
    finally:
        _restore_mute(daw, source_track, mute_before)

    provenance = _compare_ab(capture_a, capture_b)
    report["provenance"] = provenance
    if not provenance["passed"]:
        report["PROVENANCE A/B"] = "FAILED"
        report["AUDIO CAPTURE"] = "NOT_VERIFIED"
        _write(evidence, report)
        return report
    report["PROVENANCE A/B"] = "VERIFIED"

    try:
        observation = observe_asset(
            capture_a, region=f"{start_beat}:{end_beat}", tempo=tempo
        )
        report["observation"] = observation.model_dump()
        report["MUSIC OBSERVATION"] = "VERIFIED"
    except Exception as exc:  # noqa: BLE001
        report["MUSIC OBSERVATION"] = "FAILED"
        report["error"] = str(exc)
        _write(evidence, report)
        return report

    try:
        capture_c = capture_master_segment(
            daw,
            start_beat,
            end_beat,
            source_track=source_track,
            require_signal=True,
        )
        report["capture_repeat"] = capture_c.model_dump()
        report["repeatability"] = {
            "duration_delta": abs(capture_c.duration - capture_a.duration),
            "rms_a": capture_a.rms,
            "rms_c": capture_c.rms,
        }
    except AudioCaptureError as exc:
        report["repeatability_error"] = str(exc)

    tap_after = find_master_tap(daw)
    if tap_after is None:
        report["MAX AUDIO TAP"] = "FAILED"
        report["AUDIO CAPTURE"] = "NOT_VERIFIED"
        report["error"] = "tap missing after capture"
        _write(evidence, report)
        return report

    report["AUDIO CAPTURE"] = "VERIFIED"
    report["file"] = capture_a.file_path
    report["sample_rate"] = capture_a.sample_rate
    report["channels"] = capture_a.channels
    report["duration"] = capture_a.duration
    report["rms"] = capture_a.rms
    report["peak"] = capture_a.peak
    report["lufs"] = observation.signal.lufs
    report["limitations"] = [
        "MASTER only",
        "one Max Audio Effect: Copilot Audio Tap",
        "staging file D:/MusicCopilot/captures/_next.wav renamed after stop",
        "UDP 19877 used as Rec fallback if Live parameter set fails",
        "no multi-track, stems, bounce, or permanent stream",
    ]
    _write(evidence, report)
    return report


def _compare_ab(audible: AudioAsset, muted: AudioAsset) -> dict[str, Any]:
    a_rms = float(audible.rms or 0.0)
    b_rms = float(muted.rms or 0.0)
    a_peak = float(audible.peak or 0.0)
    b_peak = float(muted.peak or 0.0)
    ratio = a_rms / max(b_rms, 1e-12)
    muted_silent = b_rms < 1.0e-4 and b_peak < 1.0e-3
    passed = a_rms >= 1.0e-4 and (muted_silent or ratio >= SILENCE_RATIO)
    return {
        "a_file": audible.file_path,
        "b_file": muted.file_path,
        "a_rms": a_rms,
        "b_rms": b_rms,
        "a_peak": a_peak,
        "b_peak": b_peak,
        "rms_ratio": ratio,
        "passed": passed,
    }


def _live_sr(daw: AbletonTcpAdapter, asset: AudioAsset) -> int | None:
    pos = daw.get_playback_position()
    raw = pos.get("sample_rate")
    if raw:
        return int(raw)
    return asset.sample_rate


def _restore_mute(daw: AbletonTcpAdapter, track_index: int, mute: bool) -> None:
    try:
        daw.set_track_mute(track_index, mute)
    except DawError:
        pass


def _write(evidence: Path, report: dict[str, Any]) -> None:
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "live2_audio_capture.json").write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
