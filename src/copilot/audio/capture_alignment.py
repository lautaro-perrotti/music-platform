"""One-time click/silence alignment envelope. Certification only.

Do not run this on a normal production capture. Persist the envelope in the
capability cache and invalidate when Live/Max/TapProtocol/sample rate/mode
change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import soundfile as sf

from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    CAPTURE_HOST,
    capture_parallel_pass,
    ensure_capture_host_ready,
)
from copilot.audio.capture_capability import (
    CERT_ALIGNMENT_ENVELOPE,
    CORE_CAPTURE_VERSION,
    CapabilityCache,
    capability_key,
)
from copilot.audio.live3r import BASS_PREFERENCE, KICK_PREFERENCE
from copilot.audio.live3r_perf2 import _pick
from copilot.audio.live3r_trust import _recorders
from copilot.audio.live_capture import (
    EXPECTED_TAP_PROTOCOL,
    MASTER_INDEX,
    AudioCaptureError,
    _stop_session_clips,
    load_capture_context,
    master_tap_position,
)
from copilot.audio.tap_trust import inventory_taps
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.daw.detect import detect_ableton
from copilot.schemas.observation import SignalPoint

CLICK_BEATS = (1.0, 2.0, 3.0)
CLICK_TRACK = "Copilot Align Click"
ALIGN_REGION_END = 8.0


def click_times_s(tempo: float, beats: tuple[float, ...] = CLICK_BEATS) -> list[float]:
    beat_s = 60.0 / float(tempo)
    return [float(beat) * beat_s for beat in beats]


def write_click_wav(
    path: Path,
    *,
    sr: int = 44100,
    tempo: float = 120.0,
    duration_beats: float = ALIGN_REGION_END,
    click_beats: tuple[float, ...] = CLICK_BEATS,
) -> dict[str, Any]:
    n = int(round(duration_beats * 60.0 / float(tempo) * sr))
    data = np.zeros((n, 2), dtype=np.float32)
    pulse_n = int(0.008 * sr)
    t = np.arange(pulse_n, dtype=np.float32) / float(sr)
    pulse = (0.95 * np.sin(2.0 * np.pi * 2000.0 * t) * np.exp(-t * 500.0)).astype(
        np.float32
    )
    placed: list[float] = []
    for beat in click_beats:
        index = int(round(beat * 60.0 / float(tempo) * sr))
        if index < 0 or index >= n:
            continue
        end = min(n, index + len(pulse))
        data[index:end, 0] += pulse[: end - index]
        data[index:end, 1] += pulse[: end - index]
        placed.append(index / float(sr))
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, sr)
    return {
        "path": str(path),
        "sample_rate": sr,
        "frames": n,
        "click_beats": list(click_beats),
        "click_times_s": placed,
        "initial_silence_s": placed[0] if placed else None,
    }


def _onset_times(mono: np.ndarray, sr: int, *, threshold: float = 0.12) -> list[float]:
    if len(mono) < 16:
        return []
    env = np.abs(mono)
    peak = float(np.max(env)) if len(env) else 0.0
    if peak <= 1.0e-6:
        return []
    norm = env / peak
    times: list[float] = []
    armed = True
    min_gap = int(0.08 * sr)
    last = -min_gap
    for i, value in enumerate(norm):
        if armed and value >= threshold and (i - last) >= min_gap:
            times.append(i / float(sr))
            armed = False
            last = i
        elif value < threshold * 0.35:
            armed = True
    return times


def _peak_near(
    mono: np.ndarray,
    sr: int,
    want_s: float,
    *,
    window_s: float = 0.2,
    threshold: float = 0.12,
) -> float | None:
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if peak <= 1.0e-6:
        return None
    i0 = max(0, int(round((want_s - window_s) * sr)))
    i1 = min(len(mono), int(round((want_s + window_s) * sr)))
    if i1 <= i0:
        return None
    sl = np.abs(np.asarray(mono[i0:i1], dtype=np.float64))
    local = float(np.max(sl)) if len(sl) else 0.0
    if local < threshold * peak:
        return None
    return (i0 + int(np.argmax(sl))) / float(sr)


def measure_alignment_envelope(
    views: dict[str, np.ndarray],
    *,
    sr: int,
    tempo: float,
    expected_beats: tuple[float, ...] = CLICK_BEATS,
) -> dict[str, Any]:
    """Compare click peaks vs known musical times. Not Kick-vs-Master xcorr."""
    expected = click_times_s(tempo, expected_beats)
    per_view: dict[str, Any] = {}
    lags_ms: list[float] = []
    lags_frames: list[float] = []
    view_first: dict[str, float] = {}
    misses = 0
    for name, mono in views.items():
        matched: list[dict[str, float | None]] = []
        for want in expected:
            got = _peak_near(np.asarray(mono, dtype=np.float64), sr, want)
            if got is None:
                misses += 1
                matched.append({"expected_s": want, "observed_s": None, "error_s": None})
                continue
            err_s = float(got - want)
            matched.append({"expected_s": want, "observed_s": got, "error_s": err_s})
            lags_ms.append(abs(err_s) * 1000.0)
            lags_frames.append(abs(err_s) * float(sr))
            if name not in view_first:
                view_first[name] = got
        per_view[name] = {
            "onsets_s": _onset_times(np.asarray(mono, dtype=np.float64), sr)[:12],
            "matched": matched,
        }
    inter_view_ms: list[float] = []
    names = list(view_first)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            inter_view_ms.append(abs(view_first[left] - view_first[right]) * 1000.0)
    max_abs_ms = max(lags_ms) if lags_ms else None
    max_abs_frames = max(lags_frames) if lags_frames else None
    median_ms = float(np.median(lags_ms)) if lags_ms else None
    inter_max = max(inter_view_ms) if inter_view_ms else None
    sample_accurate = bool(
        misses == 0 and max_abs_ms is not None and max_abs_ms <= 1.0
    )
    if max_abs_ms is None or misses:
        claim = "FAILED" if max_abs_ms is None else "LIMITED"
    elif max_abs_ms <= 20.0:
        claim = "FRAME_GROUNDED"
    else:
        claim = "LIMITED"
    return {
        "claim": claim,
        "sample_accurate": sample_accurate,
        "tempo": tempo,
        "sample_rate": sr,
        "expected_beats": list(expected_beats),
        "expected_s": expected,
        "max_abs_error_ms": max_abs_ms,
        "max_abs_error_frames": max_abs_frames,
        "median_abs_error_ms": median_ms,
        "inter_view_max_ms": inter_max,
        "missed_clicks": misses,
        "envelope": (
            None
            if max_abs_ms is None
            else f"±{max_abs_ms:.1f} ms / ±{max_abs_frames:.0f} frames"
        ),
        "views": per_view,
        "note": (
            "Envelope is transport/frame grounded against known clicks. "
            "Kick-vs-Master content xcorr is not used as clock."
        ),
    }


def persist_alignment(
    cache: CapabilityCache,
    envelope: dict[str, Any],
    *,
    evidence: str | None = None,
) -> None:
    status = (
        "CERTIFIED"
        if envelope.get("claim") == "FRAME_GROUNDED"
        else "LIMITED"
        if envelope.get("claim") == "LIMITED"
        else "FAILED"
    )
    cache.put(
        CERT_ALIGNMENT_ENVELOPE,
        status=status,
        evidence=evidence,
        details=envelope,
    )


def run_alignment_certification(
    daw: AbletonTcpAdapter, evidence: Path
) -> dict[str, Any]:
    """CERTIFICATION only. Mutates capture-host Audio From, then restores."""
    report: dict[str, Any] = {
        "phase": "ALIGNMENT CERTIFICATION — CLICK FIXTURE",
        "mode": "CERTIFICATION",
        "NOT_PRODUCTION": True,
        "click_beats": list(CLICK_BEATS),
    }
    context = load_capture_context(daw)
    kick = _pick(context.session, KICK_PREFERENCE)
    bass = _pick(context.session, BASS_PREFERENCE)
    if kick is None or bass is None:
        raise AudioCaptureError("TARGET_AMBIGUOUS", "need Kick and Bass to restore routing")
    tempo = float(context.tempo or 120.0)
    sr = int(context.sample_rate or 44100)
    wav_path = Path(r"D:\MusicCopilot\captures") / "_align_click.wav"
    report["click_wav"] = write_click_wav(wav_path, sr=sr, tempo=tempo)

    existing = context.session.track_by_name(CLICK_TRACK)
    created = False
    if existing is None:
        created_info = daw.create_audio_track(CLICK_TRACK, -1)
        click_index = int(created_info["index"])
        created = True
        daw.snapshot(include_notes=False)
    else:
        click_index = existing.index
    report["click_track"] = {"index": click_index, "created": created}
    try:
        daw.delete_clip(click_index, 0)
    except DawError:
        pass
    try:
        daw.create_audio_clip(click_index, 0, str(wav_path))
    except DawError as exc:
        report["STOP"] = f"create_audio_clip failed: {exc}"
        return report
    try:
        daw.set_clip_warping(click_index, 0, True)
    except DawError:
        pass
    try:
        daw.set_clip_loop(click_index, 0, 0.0, ALIGN_REGION_END, False)
    except DawError:
        pass
    daw.set_clip_name(click_index, 0, "ALIGN CLICKS")
    try:
        daw.set_track_mute(click_index, False)
    except DawError:
        pass
    daw.snapshot(include_notes=False)
    playing = []
    for index, info in daw.last_track_infos.items():
        slots = info.get("clip_slots") or []
        clip = (slots[0].get("clip") if slots else None) or {}
        if clip.get("is_playing") or clip.get("is_triggered"):
            playing.append(int(index))
    if playing:
        _stop_session_clips(daw, playing)
        daw.stop_playback()

    inventory = inventory_taps(daw)
    try:
        kick_host = ensure_capture_host_ready(
            daw,
            name=CAPTURE_HOST,
            target_name=CLICK_TRACK,
            slot=1,
            inventory=inventory,
        )
        bass_host = ensure_capture_host_ready(
            daw,
            name=CAPTURE_BASS,
            target_name=CLICK_TRACK,
            slot=2,
            inventory=inventory,
        )
        report["host_routing_for_fixture"] = {
            "kick": {
                "input_type": kick_host.get("input_type"),
                "mutations": kick_host.get("mutations"),
            },
            "bass": {
                "input_type": bass_host.get("input_type"),
                "mutations": bass_host.get("mutations"),
            },
        }
        main_final = bool(master_tap_position(daw).get("is_last"))
        recs = _recorders(
            kick_index=int(kick_host["index"]),
            bass_index=int(bass_host["index"]),
            kick_id="ALIGN_A",
            bass_id="ALIGN_B",
            kick_channel=str(kick_host.get("input_channel")),
            bass_channel=str(bass_host.get("input_channel")),
            main_point=SignalPoint.MAIN_FINAL if main_final else SignalPoint.MAIN_NOT_FINAL,
        )
        inventory = inventory_taps(daw, use_cached_taps=False)
        for rec in recs:
            for row in inventory:
                if int(row["track_index"]) == int(rec["tap_track_index"]):
                    rec["device_index"] = row["device_index"]
                    rec["rec_param_index"] = row.get("rec_param_index")
                    break
        pass_id = "align_" + uuid4().hex[:8]
        one = capture_parallel_pass(
            daw,
            start_beat=0.0,
            end_beat=ALIGN_REGION_END,
            fire_tracks=[click_index],
            tempo=tempo,
            session_revision=int(context.revision or 0),
            pass_id=pass_id,
            recorders=recs,
        )
        assets = one["assets"]
        views = {}
        for key in ("master", "kick", "bass"):
            data, file_sr = sf.read(assets[key].file_path, always_2d=True)
            views[key] = np.mean(np.asarray(data, dtype=np.float64), axis=1)
            sr = int(file_sr)
        envelope = measure_alignment_envelope(views, sr=sr, tempo=tempo)
        report["capture"] = {
            "pass_id": pass_id,
            "timings": {
                "prepare": (one.get("timings") or {}).get("recorder_start_s"),
                "pre_roll": (one.get("timings") or {}).get("pre_roll_s"),
                "record": (one.get("timings") or {}).get("record_s"),
                "stop": (one.get("timings") or {}).get("recorder_stop_s"),
                "prepare_breakdown": (one.get("timings") or {}).get("prepare_breakdown"),
                "stop_breakdown": (one.get("timings") or {}).get("stop_breakdown"),
            },
            "wavs": {k: v.file_path for k, v in assets.items()},
            "frames": {k: v.actual_frames for k, v in assets.items()},
            "restore": one.get("restore"),
        }
        report["ALIGNMENT_ENVELOPE"] = envelope
        detection = detect_ableton()
        cache = CapabilityCache(evidence / "capture_capability.json")
        cache.bind(
            capability_key(
                live_version=detection.version,
                max_version=None,
                remote_script_protocol=str(
                    (daw.handshake_info or {}).get("protocol_version") or "1"
                ),
                tap_protocol=EXPECTED_TAP_PROTOCOL,
                core_capture_version=CORE_CAPTURE_VERSION,
                sample_rate=sr,
            )
        )
        persist_alignment(cache, envelope, evidence=str(evidence / "live3r_align.json"))
        report["capability_cache"] = {
            "certified": cache.certified(CERT_ALIGNMENT_ENVELOPE),
            "status": ((cache.data.get("certs") or {}).get(CERT_ALIGNMENT_ENVELOPE) or {}).get(
                "status"
            ),
        }
    except Exception as exc:
        report["STOP"] = str(exc)
        report["ALIGNMENT_ENVELOPE"] = report.get("ALIGNMENT_ENVELOPE") or {
            "claim": "FAILED"
        }
    finally:
        inventory = inventory_taps(daw, use_cached_taps=False)
        restored_kick = ensure_capture_host_ready(
            daw,
            name=CAPTURE_HOST,
            target_name=kick.name,
            slot=1,
            inventory=inventory,
        )
        restored_bass = ensure_capture_host_ready(
            daw,
            name=CAPTURE_BASS,
            target_name=bass.name,
            slot=2,
            inventory=inventory,
        )
        report["routing_restored"] = {
            "kick": restored_kick.get("input_type"),
            "bass": restored_bass.get("input_type"),
            "kick_mutations": restored_kick.get("mutations"),
            "bass_mutations": restored_bass.get("mutations"),
        }
        if created:
            try:
                daw.delete_track(click_index)
                report["click_track"]["deleted"] = True
            except DawError as exc:
                report["click_track"]["deleted"] = False
                report["click_track"]["delete_error"] = str(exc)
    return report
