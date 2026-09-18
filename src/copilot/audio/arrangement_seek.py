from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import time

from copilot.audio.live_capture import AudioCaptureError, beats_to_seconds
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError

QN_UNIT = "quarter_note_position"
SEEK_TOLERANCE_QN = 0.25
# Transport/poll uncertainty (~270 ms at 167 BPM). Not first-readback slack.
PLAYBACK_START_TOLERANCE_QN = 0.75
# Restore of an already-playing cursor may land after a poll delay.
RESTORE_PLAYING_TOLERANCE_QN = 2.0
REGION_STARTS_QN = (32.0, 172.0, 292.0)
REGION_1_START_QN = 32.0
REGION_1_END_QN = 64.0
TRANSPORT_PRIMITIVE_VERSION = "arrangement-start-at-qn-1"
PRE_ROLL_QN = 16.0  # FROZEN. Do not shorten to buy wall time.
STOPPED_SEEK_PLUS_PLAY_SUPPORTED = False
CONTINUE_AFTER_STOPPED_SEEK_SUPPORTED = False


class ArrangementSeekError(AudioCaptureError):
    def __init__(self, message: str) -> None:
        super().__init__("ARRANGEMENT_SEEK_FAILED", message)


class ArrangementPlaybackStartError(AudioCaptureError):
    def __init__(self, message: str) -> None:
        super().__init__("ARRANGEMENT_PLAYBACK_START_MISMATCH", message)


def implied_start_qn(
    first_qn: float,
    *,
    t_first_poll: float,
    t_play_command: float,
    tempo: float,
) -> float:
    """Estimate actual start from first TCP readback minus elapsed transport time."""
    if float(tempo) <= 0:
        raise ArrangementPlaybackStartError("tempo missing for implied start")
    elapsed_s = max(0.0, float(t_first_poll) - float(t_play_command))
    expected_progress_qn = elapsed_s * float(tempo) / 60.0
    return float(first_qn) - expected_progress_qn


def expected_progress_qn(
    *,
    t_first_poll: float,
    t_play_command: float,
    tempo: float,
) -> float:
    if float(tempo) <= 0:
        raise ArrangementPlaybackStartError("tempo missing for expected progress")
    elapsed_s = max(0.0, float(t_first_poll) - float(t_play_command))
    return elapsed_s * float(tempo) / 60.0


def transport_snapshot(daw: AbletonTcpAdapter) -> dict[str, Any]:
    pos = daw.get_playback_position()
    return {
        "is_playing": bool(pos.get("is_playing")),
        "current_song_time": float(pos.get("current_song_time") or 0.0),
        "loop": bool(pos.get("loop", False)),
        "loop_start": float(pos.get("loop_start") or 0.0),
        "loop_length": float(pos.get("loop_length") or 0.0),
        "tempo": float(pos.get("tempo") or 0.0),
        "signature_numerator": int(pos.get("signature_numerator") or 4),
        "signature_denominator": int(pos.get("signature_denominator") or 4),
        "unit": QN_UNIT,
    }


def read_song_time_qn(daw: AbletonTcpAdapter) -> float:
    """Live Song.current_song_time is beats; in 4/4 that is quarter-note position."""
    pos = daw.get_playback_position()
    return float(pos.get("current_song_time") or 0.0)


def verify_song_time_qn(
    observed_qn: float,
    requested_qn: float,
    *,
    tolerance_qn: float = SEEK_TOLERANCE_QN,
) -> None:
    if abs(float(observed_qn) - float(requested_qn)) > float(tolerance_qn):
        raise ArrangementSeekError(
            f"requested {requested_qn} {QN_UNIT}, Live at {observed_qn} "
            f"(tolerance {tolerance_qn})"
        )


def verify_playback_start_qn(
    observed_qn: float,
    requested_qn: float,
    *,
    tolerance_qn: float = PLAYBACK_START_TOLERANCE_QN,
) -> None:
    """Verify implied actual start, not the first TCP readback."""
    if abs(float(observed_qn) - float(requested_qn)) > float(tolerance_qn):
        raise ArrangementPlaybackStartError(
            f"requested {requested_qn} {QN_UNIT}, implied start {observed_qn} "
            f"(tolerance {tolerance_qn})"
        )


def _apply_set(daw: AbletonTcpAdapter, qn: float) -> dict[str, Any]:
    result = daw.set_current_song_time(float(qn))
    return {
        "command": "set_current_song_time",
        "params": {"time": float(qn)},
        "result": result,
        "readback_qn": read_song_time_qn(daw),
    }


def _apply_jump(daw: AbletonTcpAdapter, qn: float) -> dict[str, Any]:
    result = daw.jump_to_time(float(qn))
    return {
        "command": "jump_to_time",
        "params": {"time": float(qn)},
        "result": result,
        "readback_qn": read_song_time_qn(daw),
    }


def _apply_scrub(daw: AbletonTcpAdapter, qn: float) -> dict[str, Any]:
    current = read_song_time_qn(daw)
    delta = float(qn) - current
    result = daw.scrub_by(delta)
    return {
        "command": "scrub_by",
        "params": {"delta": delta, "from_qn": current, "to_qn": float(qn)},
        "result": result,
        "readback_qn": read_song_time_qn(daw),
    }


def _try_apply(name: str, fn, daw: AbletonTcpAdapter, qn: float) -> dict[str, Any]:
    try:
        row = fn(daw, qn)
        row["error"] = None
        return row
    except (DawError, AudioCaptureError) as exc:
        return {
            "command": name,
            "params": {"time": float(qn)},
            "result": None,
            "readback_qn": read_song_time_qn(daw),
            "error": str(exc),
        }


def seek_arrangement_qn(
    daw: AbletonTcpAdapter,
    qn: float,
    *,
    tolerance_qn: float = SEEK_TOLERANCE_QN,
    allow_fallbacks: bool = True,
) -> dict[str, Any]:
    """Stop, set quarter-note position, read back. Never keep the old cursor."""
    attempts: list[dict[str, Any]] = []
    if bool(daw.get_playback_position().get("is_playing")):
        daw.stop_playback()
    attempts.append(_try_apply("set_current_song_time", _apply_set, daw, qn))
    if allow_fallbacks and abs(attempts[-1]["readback_qn"] - float(qn)) > tolerance_qn:
        attempts.append(_try_apply("jump_to_time", _apply_jump, daw, qn))
    if allow_fallbacks and abs(attempts[-1]["readback_qn"] - float(qn)) > tolerance_qn:
        attempts.append(_try_apply("scrub_by", _apply_scrub, daw, qn))
    observed = float(attempts[-1]["readback_qn"])
    verify_song_time_qn(observed, qn, tolerance_qn=tolerance_qn)
    return {
        "requested_qn": float(qn),
        "observed_qn": observed,
        "error_qn": observed - float(qn),
        "tolerance_qn": tolerance_qn,
        "unit": QN_UNIT,
        "attempts": attempts,
        "command_used": attempts[-1]["command"],
        "ok": True,
    }


def restore_transport(
    daw: AbletonTcpAdapter,
    saved: dict[str, Any],
    *,
    tolerance_qn: float = SEEK_TOLERANCE_QN,
) -> dict[str, Any]:
    daw.stop_playback()
    seek = seek_arrangement_qn(
        daw, float(saved["current_song_time"]), tolerance_qn=tolerance_qn
    )
    loop_length = float(saved.get("loop_length") or 0.0)
    loop_touched = False
    if loop_length > 0:
        daw.set_arrangement_loop(
            float(saved["loop_start"]),
            float(saved["loop_start"]) + loop_length,
            bool(saved["loop"]),
        )
        loop_touched = True
    if saved.get("is_playing"):
        daw.start_playback()
    readback = transport_snapshot(daw)
    playing_ok = bool(readback["is_playing"]) == bool(saved["is_playing"])
    time_ok = abs(readback["current_song_time"] - float(saved["current_song_time"])) <= (
        RESTORE_PLAYING_TOLERANCE_QN if saved.get("is_playing") else tolerance_qn
    )
    return {
        "ok": playing_ok and time_ok,
        "seek": seek,
        "loop_touched": loop_touched,
        "saved": saved,
        "readback": readback,
    }


def static_seek_probe(daw: AbletonTcpAdapter, positions_qn: tuple[float, ...] = REGION_STARTS_QN) -> dict[str, Any]:
    rows = []
    for qn in positions_qn:
        row = seek_arrangement_qn(daw, qn)
        rows.append(row)
    return {"ok": all(row["ok"] for row in rows), "unit": QN_UNIT, "rows": rows}


def transport_target_qn(requested_start_qn: float, *, pre_roll_qn: float = PRE_ROLL_QN) -> float:
    return max(0.0, float(requested_start_qn) - float(pre_roll_qn))


def start_arrangement_at_qn(
    daw: AbletonTcpAdapter,
    target_qn: float,
    *,
    tolerance_qn: float = PLAYBACK_START_TOLERANCE_QN,
) -> dict[str, Any]:
    """Canonical Arrangement start. One Live-side start_playing + seek. No TCP split."""
    t_client_command = time.monotonic()
    t_client_wall = time.time()
    try:
        result = daw.start_playback_at_qn(float(target_qn))
    except DawError as exc:
        message = str(exc).lower()
        if "unknown command" in message or "unsupported" in message:
            raise ArrangementPlaybackStartError(
                "start_playback_at_qn missing on Control Surface; "
                "reload AbletonMCP after install-script"
            ) from exc
        raise ArrangementPlaybackStartError(str(exc)) from exc
    t_client_return = time.monotonic()
    playing = bool(result.get("is_playing"))
    observed = float(result.get("observed_qn") or 0.0)
    tempo = float(result.get("tempo") or 0.0)
    elapsed_s = float(result.get("elapsed_s") or 0.0)
    if tempo <= 0:
        try:
            daw.stop_playback()
        except DawError:
            pass
        raise ArrangementPlaybackStartError("tempo missing for implied start")
    progress_qn = elapsed_s * tempo / 60.0
    implied = observed - progress_qn
    version = str(result.get("transport_primitive_version") or "")
    payload = {
        "ok": True,
        "TRANSPORT_START_PROVENANCE": "VERIFIED",
        "playback_start_qn": implied,
        "implied_start_qn": implied,
        "first_qn": observed,
        "observed_qn": observed,
        "target_qn": float(target_qn),
        "is_playing": playing,
        "t_play_command": float(result.get("t_command_mono") or t_client_command),
        "t_play_command_wall": float(result.get("t_command_wall") or t_client_wall),
        "t_play_return": t_client_return,
        "t_first_poll": float(result.get("t_tick_mono") or t_client_return),
        "t_tick_wall": result.get("t_tick_wall"),
        "expected_progress_qn": progress_qn,
        "poll_delay_s": elapsed_s,
        "command_rtt_s": max(0.0, t_client_return - t_client_command),
        "tempo": tempo,
        "tolerance_qn": float(tolerance_qn),
        "requested_qn": float(target_qn),
        "methods": ["start_playback_at_qn"],
        "same_callback_qn": result.get("same_callback_qn"),
        "verification": result.get("verification") or "next_tick",
        "transport_primitive_version": version or TRANSPORT_PRIMITIVE_VERSION,
        "unit": QN_UNIT,
        "live_result": result,
    }
    if not playing:
        try:
            daw.stop_playback()
        except DawError:
            pass
        raise ArrangementPlaybackStartError(
            f"requested {target_qn} {QN_UNIT}, is_playing=false after atomic start"
        )
    if version and version != TRANSPORT_PRIMITIVE_VERSION:
        try:
            daw.stop_playback()
        except DawError:
            pass
        raise ArrangementPlaybackStartError(
            f"transport_primitive_version={version!r} expected {TRANSPORT_PRIMITIVE_VERSION!r}"
        )
    try:
        verify_playback_start_qn(implied, target_qn, tolerance_qn=tolerance_qn)
    except ArrangementPlaybackStartError:
        try:
            daw.stop_playback()
        except DawError:
            pass
        payload["ok"] = False
        payload["TRANSPORT_START_PROVENANCE"] = "ARRANGEMENT_PLAYBACK_START_MISMATCH"
        raise ArrangementPlaybackStartError(
            f"requested {target_qn} {QN_UNIT}, implied_start_qn={implied} "
            f"observed_qn={observed} expected_progress_qn={progress_qn} "
            f"elapsed_s={elapsed_s} (tolerance {tolerance_qn}); "
            f"methods=['start_playback_at_qn']"
        ) from None
    return payload


def start_arrangement_playback(
    daw: AbletonTcpAdapter,
    qn: float,
    *,
    tolerance_qn: float = PLAYBACK_START_TOLERANCE_QN,
) -> dict[str, Any]:
    """Deprecated name. Canonical primitive is start_arrangement_at_qn."""
    return start_arrangement_at_qn(daw, qn, tolerance_qn=tolerance_qn)


def playback_smoke(daw: AbletonTcpAdapter, start_qn: float = REGION_1_START_QN) -> dict[str, Any]:
    daw.stop_playback()
    wall0 = time.perf_counter()
    started = start_arrangement_at_qn(daw, start_qn)
    started_qn = float(started["implied_start_qn"])
    time.sleep(1.0)
    elapsed = time.perf_counter() - wall0
    later = transport_snapshot(daw)
    later_qn = float(later["current_song_time"])
    tempo = float(later["tempo"] or 0.0)
    expected_delta = elapsed * tempo / 60.0 if tempo > 0 else None
    daw.stop_playback()
    near_zero = started_qn < 8.0
    progressed = later_qn > started_qn
    return {
        "ok": (not near_zero) and progressed and started["ok"],
        "start": started,
        "playback_start_qn": started_qn,
        "implied_start_qn": started_qn,
        "first_qn": started.get("first_qn"),
        "after_1s_qn": later_qn,
        "elapsed_wall_s": elapsed,
        "tempo": tempo,
        "expected_delta_qn": expected_delta,
        "observed_delta_qn": later_qn - started_qn,
        "restarted_near_zero": near_zero,
        "unit": QN_UNIT,
        "taps_armed": False,
        "audio_files": [],
    }


def run_seek_trust(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    evidence.mkdir(parents=True, exist_ok=True)
    saved = transport_snapshot(daw)
    report: dict[str, Any] = {
        "phase": "ARRANGEMENT SEEK TRUST",
        "unit_contract": {
            "canonical": QN_UNIT,
            "live_song_current_song_time": "beats; 4/4 beat == quarter-note position",
            "remote_script_param": "params.time assigned to Song.current_song_time with no conversion",
            "runner": "passes region start_qn unchanged",
            "no_silent_bars_beats_seconds_convert": True,
            "seek_tolerance_qn": SEEK_TOLERANCE_QN,
            "playback_start_tolerance_qn": PLAYBACK_START_TOLERANCE_QN,
            "restore_playing_tolerance_qn": RESTORE_PLAYING_TOLERANCE_QN,
            "start_truth": "implied_start_qn = first_qn - (t_first_poll - t_play_command) * tempo / 60",
            "tempo_167_region_32_64": {
                "start_qn": 32.0,
                "end_qn": 64.0,
                "duration_qn": 32.0,
                "duration_s": beats_to_seconds(32.0, 167.0),
            },
        },
        "snapshot_before": saved,
        "MUSICAL WRITES": 0,
        "ASTRA CALLS": 0,
        "NO CAPTURE": True,
        "NO DSP": True,
    }
    try:
        if saved["is_playing"]:
            daw.stop_playback()
        stopped = transport_snapshot(daw)
        report["stopped"] = stopped
        if stopped["is_playing"]:
            raise ArrangementSeekError("transport still playing after stop")

        first = seek_arrangement_qn(daw, REGION_1_START_QN)
        report["STATIC SEEK 32"] = first
        static = static_seek_probe(daw, REGION_STARTS_QN)
        report["STATIC SEEK 172"] = static["rows"][1]
        report["STATIC SEEK 292"] = static["rows"][2]
        report["static_all_ok"] = static["ok"]
        smoke = playback_smoke(daw, REGION_1_START_QN)
        report["PLAYBACK SMOKE"] = smoke
        restore = restore_transport(daw, saved)
        report["RESTORE"] = restore
        report["ok"] = bool(static["ok"] and smoke["ok"] and restore["ok"])
        report["status"] = "SEEK VERIFIED" if report["ok"] else "ARRANGEMENT_SEEK_FAILED"
    except (ArrangementSeekError, ArrangementPlaybackStartError) as exc:
        report["ok"] = False
        report["status"] = str(getattr(exc, "code", None) or type(exc).__name__)
        if isinstance(exc, ArrangementPlaybackStartError):
            report["status"] = "ARRANGEMENT_PLAYBACK_START_MISMATCH"
        else:
            report["status"] = "ARRANGEMENT_SEEK_FAILED"
        report["error"] = str(exc)
        try:
            report["RESTORE"] = restore_transport(daw, saved)
        except Exception as restore_exc:
            report["RESTORE"] = {"ok": False, "error": str(restore_exc)}
    path = evidence / "arrangement_seek_trust.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["artifact"] = str(path)
    return report


def probe_transport_start_provenance(
    daw: AbletonTcpAdapter,
    requested_qn: float = REGION_1_START_QN,
    *,
    evidence: Path | None = None,
) -> dict[str, Any]:
    """Single-target atomic start. Prefer run_atomic_transport_trust for the gate."""
    return run_atomic_transport_trust(
        daw, positions_qn=(float(requested_qn),), evidence=evidence
    )


def run_atomic_transport_trust(
    daw: AbletonTcpAdapter,
    positions_qn: tuple[float, ...] = REGION_STARTS_QN,
    *,
    evidence: Path | None = None,
) -> dict[str, Any]:
    """Transport-only: stop → atomic start+seek → next-tick verify → ~1s → stop."""
    saved = transport_snapshot(daw)
    report: dict[str, Any] = {
        "phase": "ARRANGEMENT PLAYBACK AT QN",
        "transport_primitive_version": TRANSPORT_PRIMITIVE_VERSION,
        "tolerance_qn": PLAYBACK_START_TOLERANCE_QN,
        "pre_roll_qn": PRE_ROLL_QN,
        "unit": QN_UNIT,
        "STOPPED_SEEK_PLUS_PLAY_SUPPORTED": STOPPED_SEEK_PLUS_PLAY_SUPPORTED,
        "CONTINUE_AFTER_STOPPED_SEEK_SUPPORTED": CONTINUE_AFTER_STOPPED_SEEK_SUPPORTED,
        "MUSICAL WRITES": 0,
        "ASTRA CALLS": 0,
        "NO CAPTURE": True,
        "NO DSP": True,
        "NO TAPS": True,
        "rows": [],
    }
    try:
        if saved["is_playing"]:
            daw.stop_playback()
        rows: list[dict[str, Any]] = []
        for target in positions_qn:
            daw.stop_playback()
            started = start_arrangement_at_qn(daw, float(target))
            time.sleep(1.0)
            later = transport_snapshot(daw)
            later_qn = float(later["current_song_time"])
            daw.stop_playback()
            progressed = later_qn > float(started["implied_start_qn"])
            error_qn = float(started["implied_start_qn"]) - float(target)
            row = {
                "target_qn": float(target),
                "ok": bool(started["ok"] and progressed),
                "implied_start_qn": started["implied_start_qn"],
                "observed_qn": started["observed_qn"],
                "expected_progress_qn": started["expected_progress_qn"],
                "implied_start_error_qn": error_qn,
                "after_1s_qn": later_qn,
                "progressed": progressed,
                "is_playing_at_verify": started["is_playing"],
                "elapsed_s": started["poll_delay_s"],
                "same_callback_qn": started.get("same_callback_qn"),
                "verification": started.get("verification"),
                "transport_primitive_version": started.get("transport_primitive_version"),
                "start": started,
            }
            rows.append(row)
            if not row["ok"]:
                raise ArrangementPlaybackStartError(
                    f"target {target}: implied={started['implied_start_qn']} "
                    f"progressed={progressed}"
                )
        restore = restore_transport(daw, saved)
        report["rows"] = rows
        report["ok"] = all(row["ok"] for row in rows) and bool(restore.get("ok"))
        report["TRANSPORT_START_PROVENANCE"] = (
            "VERIFIED" if report["ok"] else "ARRANGEMENT_PLAYBACK_START_MISMATCH"
        )
        report["restore_ok"] = restore.get("ok")
        report["implied_start_errors_qn"] = {
            str(row["target_qn"]): row["implied_start_error_qn"] for row in rows
        }
    except (ArrangementSeekError, ArrangementPlaybackStartError, DawError) as exc:
        report["ok"] = False
        report["TRANSPORT_START_PROVENANCE"] = "ARRANGEMENT_PLAYBACK_START_MISMATCH"
        report["error"] = str(exc)
        try:
            report["restore"] = restore_transport(daw, saved)
        except Exception as restore_exc:
            report["restore"] = {"ok": False, "error": str(restore_exc)}
    if evidence is not None:
        evidence.mkdir(parents=True, exist_ok=True)
        path = evidence / "arrangement_start_at_qn.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        report["artifact"] = str(path)
    return report
