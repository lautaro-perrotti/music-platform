"""Execute a persisted, real Lucas ProducerPlan on the already-owned Live set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import time

from copilot.core_v2.direct import AbletonBatch, DirectAbletonExecutor, DirectAbletonSession, TrackBatch
from copilot.daw.detect import detect_ableton
from copilot.integration.lucas_producer_plan_v1 import (
    A_WAV, B_WAV, C_WAV, OUT, RUNTIME, _redact_error, _validate_raw, _write_json,
    novelty_report,
)
from copilot.integration.tech_house_kit_direct_v1 import _capture, metrics as wav_metrics, VOLUMES
from copilot.producer import tech_house_kit_v2 as palette
from copilot.schemas.session import MidiNote


ORDER = ("kick", "clap", "closed_hat", "open_hat", "perc", "bass", "stab")
DISPLAY = ("Kick", "Clap", "Closed Hat", "Open Hat", "Perc", "Bass", "Stab")
PRIOR_NAMES = (["FAST V2 - Kick", "FAST V2 - Hat", "FAST V2 - Bass"]
               + [f"KIT DIRECT V1 - {role}" for role in DISPLAY]
               + [f"KIT DIRECT V2 - {role}" for role in DISPLAY])


def load_real_plan() -> tuple[object, dict, dict]:
    raw = (OUT / "plan" / "producer_plan_raw.json").read_text(encoding="utf-8")
    plan, rendered = _validate_raw(raw)
    persisted = json.loads((OUT / "plan" / "producer_plan_validated.json").read_text(encoding="utf-8"))
    provider = json.loads((OUT / "evidence" / "lucas_provider.json").read_text(encoding="utf-8"))
    if persisted != plan.model_dump(mode="json") or not provider.get("real_call") or not provider.get("calls"):
        raise RuntimeError("PERSISTED_REAL_LUCAS_PLAN_NOT_VERIFIED")
    return plan, rendered, provider


def make_batch(rendered: dict[str, list[MidiNote]]) -> AbletonBatch:
    tracks = tuple(TrackBatch(
        name=f"KIT LUCAS V1 - {label}", source_name=sound.browser_name,
        source_kind="sample" if sound.is_one_shot else "device",
        notes=tuple(rendered[key]), volume=VOLUMES[label], fallback_source_name=None,
    ) for key, label, sound in zip(ORDER, DISPLAY, palette.KIT))
    if len(tracks) != 7 or any(not track.notes or len(track.notes) > 512 for track in tracks):
        raise ValueError("INVALID_LUCAS_BATCH_ROLE_NOTES")
    return AbletonBatch(126.0, 64.0, tracks)


def _preflight(session: DirectAbletonSession) -> tuple[dict[int, bool], dict]:
    detected = detect_ableton()
    if not detected.process_running or not detected.process_pid:
        raise RuntimeError("ABLETON_NOT_RUNNING")
    session.pid = detected.process_pid
    session.daw.connect()
    session.daw.get_session_info()
    path = str(session.daw.get_session_path().get("path") or "")
    session.initial_path = path
    if not path or not Path(path).resolve().is_relative_to(RUNTIME.resolve()):
        raise RuntimeError("UNRECOGNIZED_LIVE_PROJECT")
    snapshot = session.daw.snapshot(include_notes=False)
    arranged = session.daw.get_arrangement_clips().get("clips") or []
    if [t.name for t in snapshot.tracks] != ["1-MIDI", "2-MIDI", "3-Audio", "4-Audio"] + PRIOR_NAMES:
        raise RuntimeError("UNKNOWN_OR_CHANGED_LIVE_TRACKS")
    if len(arranged) != 17 or abs(snapshot.transport.tempo - 126) > .01:
        raise RuntimeError("UNKNOWN_ARRANGEMENT_OR_TEMPO")
    for i, track in enumerate(snapshot.tracks):
        if i < 4:
            if track.clips or track.devices:
                raise RuntimeError("DEFAULT_TRACK_HAS_USER_WORK")
            continue
        if track.role != "midi" or len(track.devices) != 1 or len(track.clips) != 1:
            raise RuntimeError("PRIOR_OWNED_TRACK_CHANGED")
        placed = [a for a in arranged if a.get("track_index") == track.index]
        expected_end = 16 if i < 7 else 64
        if (len(placed) != 1 or abs(float(placed[0]["start_time"])) > 1e-6
                or abs(float(placed[0]["end_time"]) - expected_end) > 1e-6):
            raise RuntimeError("PRIOR_OWNED_ARRANGEMENT_CHANGED")
    hashes = {}
    for label, path in (("A", A_WAV), ("B", B_WAV)):
        report = json.loads((path.parents[1] / "evidence" / "direct_execution_report.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if report["technical"] != "VERIFIED" or digest != report["audio"]["sha256"]:
            raise RuntimeError(f"BASELINE_{label}_CHANGED")
        hashes[label] = digest
    return {t.index: t.mixer.mute for t in snapshot.tracks[4:]}, hashes


def _note_key(note: MidiNote | dict) -> tuple:
    if isinstance(note, dict):
        return (int(note["pitch"]), round(float(note["start_time"]), 6),
                round(float(note["duration"]), 6), int(note["velocity"]))
    return (note.pitch, round(note.start_time, 6), round(note.duration, 6), note.velocity)


def _apply(session: DirectAbletonSession, batch: AbletonBatch, old_mutes: dict[int, bool]) -> dict:
    daw = session.daw
    if str(daw.get_session_path().get("path") or "") != session.initial_path:
        raise RuntimeError("SESSION_CHANGED_BEFORE_C")
    executor = DirectAbletonExecutor(session)
    daw.stop_playback()
    for index in old_mutes:
        daw.set_track_mute(index, True)
    if any(not t.mixer.mute for t in daw.snapshot(include_notes=False).tracks[4:21]):
        raise RuntimeError("PRIOR_MUTE_READBACK_FAILED")
    created: dict[str, int] = {}
    for spec in batch.tracks:
        index = int(daw.create_midi_track(spec.name)["index"])
        created[spec.name] = index
        if executor._load_with_fallback(index, spec) != spec.source_name:
            raise RuntimeError(f"SONIC_PALETTE_CHANGED: {spec.name}")
        daw.create_midi_clip(index, 0, 64)
        daw.replace_clip_notes(index, 0, list(spec.notes))
        daw.duplicate_clip_to_arrangement(index, 0, 0)
        daw.set_mixer_volume(index, spec.volume)
    final = daw.snapshot(include_notes=False)
    arranged = daw.get_arrangement_clips().get("clips") or []
    if len(final.tracks) != 28 or len(arranged) != 24 or abs(final.transport.tempo - 126) > .01:
        raise RuntimeError("C_TRACK_COUNT_ARRANGEMENT_OR_TEMPO_MISMATCH")
    details = {}
    for spec in batch.tracks:
        track = next((t for t in final.tracks if t.name == spec.name), None)
        if track is None or track.index != created[spec.name] or not track.devices:
            raise RuntimeError(f"C_TRACK_OR_DEVICE_READBACK_FAILED: {spec.name}")
        observed = daw.get_clip_notes(track.index, 0).get("notes") or []
        placed = [a for a in arranged if a.get("track_index") == track.index]
        if (sorted(map(_note_key, observed)) != sorted(map(_note_key, spec.notes))
                or len(placed) != 1 or abs(float(placed[0]["start_time"])) > 1e-6
                or abs(float(placed[0]["end_time"]) - 64) > 1e-6
                or abs(track.mixer.volume - spec.volume) > 1e-4):
            raise RuntimeError(f"C_NOTE_ARRANGEMENT_OR_VOLUME_MISMATCH: {spec.name}")
        details[spec.name] = {"source": spec.source_name, "device": [d.name for d in track.devices],
                              "index": track.index, "note_count": len(observed),
                              "arrangement": placed, "volume": track.mixer.volume}
    return {"tempo": final.transport.tempo, "track_count": len(final.tracks),
            "tracks": details, "timeouts": executor.timeouts}


def _restore(session: DirectAbletonSession, old_mutes: dict[int, bool]) -> bool:
    daw = session.daw
    daw.stop_playback()
    current = daw.snapshot(include_notes=False)
    for track in current.tracks:
        if track.name.startswith("KIT LUCAS V1 - "):
            daw.set_track_mute(track.index, True)
    for index, mute in old_mutes.items():
        daw.set_track_mute(index, mute)
    observed = daw.snapshot(include_notes=False)
    return (all(observed.tracks[index].mixer.mute == mute for index, mute in old_mutes.items())
            and all(t.mixer.mute for t in observed.tracks if t.name.startswith("KIT LUCAS V1 - ")))


def run_from_persisted_real_plan() -> dict:
    if C_WAV.exists():
        raise FileExistsError("LUCAS_C_WAV_ALREADY_EXISTS")
    started = time.perf_counter()
    render_started = time.perf_counter()
    plan, rendered, provider = load_real_plan()
    novelty = novelty_report(rendered)
    _write_json(OUT / "evidence" / "novelty_report.json", novelty)
    batch = make_batch(rendered)
    render_seconds = round(time.perf_counter() - render_started, 3)
    report: dict = {
        "milestone": "LUCAS_PRODUCER_PLAN_V1", "technical": "RUNNING", "human_audition": "PENDING",
        "provider": {key: provider[key] for key in ("provider", "model", "real_call", "repair_pass_used")},
        "plan": {"key": plan.tonality.root, "mode": plan.tonality.mode,
                 "swing_beats": plan.swing.amount_beats,
                 "sections": [s.model_dump(mode="json") for s in plan.sections],
                 "bass_concept": plan.roles.bass.concept},
        "novelty": novelty, "wav_a": str(A_WAV), "wav_b": str(B_WAV), "wav_c": str(C_WAV),
        "safe_write_used": False, "fast_direct_used": True,
        "lucas_plan_seconds": round(sum(call["seconds"] for call in provider["calls"]), 3),
        "render_seconds": render_seconds,
    }
    old_mutes: dict[int, bool] = {}
    session = DirectAbletonSession()
    try:
        old_mutes, report["baseline_hashes"] = _preflight(session)
        report["ableton_pid"] = session.pid
        t = time.perf_counter()
        report["ableton_readback"] = _apply(session, batch, old_mutes)
        report["ableton_execution_seconds"] = round(time.perf_counter() - t, 3)
        _write_json(OUT / "evidence" / "ableton_readback.json", report["ableton_readback"])
        session.daw.set_current_song_time(0)
        session.daw.start_playback()
        time.sleep(2)
        session.daw.stop_playback()
        report["play_stop"] = "VERIFIED"
        t = time.perf_counter()
        attempts = []
        for attempt in range(2):
            source = _capture(session)
            measured = wav_metrics(source)
            attempts.append({"attempt": attempt + 1, "metrics": measured})
            if measured["peak"] < 1:
                break
            if attempt == 0:
                for track in report["ableton_readback"]["tracks"].values():
                    session.daw.set_mixer_volume(track["index"], max(.05, track["volume"] - .06))
        report["capture_seconds"] = round(time.perf_counter() - t, 3)
        report["capture_attempts"] = attempts
        if not (measured["channels"] == 2 and 29 < measured["duration_s"] < 32
                and measured["left_rms"] > 1e-5 and measured["right_rms"] > 1e-5
                and measured["peak"] < 1 and not measured["lr_identical"]):
            raise RuntimeError("LUCAS_C_CAPTURE_TECHNICAL_GATE_FAILED")
        shutil.copy2(source, C_WAV)
        report["audio"] = wav_metrics(C_WAV)
        _write_json(OUT / "evidence" / "capture_metrics.json", report["audio"])
        for label, path in (("A", A_WAV), ("B", B_WAV)):
            if hashlib.sha256(path.read_bytes()).hexdigest() != report["baseline_hashes"][label]:
                raise RuntimeError(f"BASELINE_{label}_CHANGED_DURING_C")
        report["technical"] = "VERIFIED"
    except Exception as error:
        report["technical"] = "FAILED"
        report["error"] = _redact_error(error)
        raise
    finally:
        try:
            if old_mutes:
                report["mutes_restored"] = _restore(session, old_mutes)
                if not report["mutes_restored"]:
                    report["technical"] = "FAILED"
                    report["error"] = "MUTE_RESTORE_READBACK_FAILED"
        except Exception as error:
            report["technical"] = "FAILED"
            report["mute_restore_error"] = _redact_error(error)
        report["rpc_count"] = session.daw.tcp_stats().get("total")
        report["total_seconds"] = round(time.perf_counter() - started + report["lucas_plan_seconds"], 3)
        _write_json(OUT / "evidence" / "direct_execution_report.json", report)
        _write_json(OUT / "report" / "lucas_producer_plan_v1.json", report)
        (OUT / "report" / "lucas_producer_plan_v1.md").write_text(
            f"# Lucas Producer Plan V1\n\nTechnical: {report['technical']}\n\n"
            f"Human audition: PENDING\n\nA: {A_WAV}\n\nB: {B_WAV}\n\n"
            f"C: {C_WAV if C_WAV.exists() else 'none'}\n\nNo artistic winner is declared.\n",
            encoding="utf-8")
        session.close()
    if report["technical"] != "VERIFIED":
        raise RuntimeError(report.get("error", "LUCAS_C_TECHNICAL_GATE_FAILED"))
    return report


if __name__ == "__main__":
    try:
        finished = run_from_persisted_real_plan()
        print(json.dumps({key: finished.get(key) for key in
                          ("technical", "provider", "plan", "novelty", "audio", "mutes_restored",
                           "lucas_plan_seconds", "render_seconds", "ableton_execution_seconds",
                           "capture_seconds", "total_seconds", "rpc_count")}, ensure_ascii=False, indent=2))
    except Exception as error:
        print(json.dumps({"technical": "FAILED", "error": _redact_error(error)}))
        raise SystemExit(1)
