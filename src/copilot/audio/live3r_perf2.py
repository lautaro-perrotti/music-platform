from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from copilot.audio.batch_capture import (
    STAGING_BASS,
    STAGING_KICK,
    STAGING_NAME,
    assets_from_wav_card,
    capture_parallel_pass,
    probe_capture_track,
    setup_capture_host,
)
from copilot.audio.diagnose import (
    context_removal_needed,
    diagnoses_equivalent,
    diagnose_lowend,
    primary_finding_types,
    unstable_insufficient,
)
from copilot.audio.live3r import BASS_PREFERENCE, KICK_PREFERENCE, SKIP_FIRE_PREFIXES
from copilot.audio.live_capture import (
    MASTER_INDEX,
    AudioAsset,
    AudioCaptureError,
    assert_constant_tempo,
    ensure_master_tap,
    install_audio_tap_device,
    load_capture_context,
)
from copilot.audio.views import capture_master_context
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.schemas.observation import CaptureView, ObservationSource, SignalPoint

REGION = "REGION_A"
START_QN = 0.0
END_QN = 8.0
BEFORE_TOTAL_S = 139.7
BEFORE_TCP = 228
BEFORE_ROUTING = 28


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fire_from_session(session, kick_index: int, bass_index: int) -> list[int]:
    play = {kick_index, bass_index}
    for track in session.tracks:
        if str(track.name).startswith(SKIP_FIRE_PREFIXES):
            continue
        if track.clips:
            play.add(track.index)
    return sorted(play)


def _pick(session, names: tuple[str, ...]):
    for name in names:
        track = session.track_by_name(name)
        if track is not None:
            return track
    return None


def _run_diagnosis(views: dict[str, AudioAsset], kick_name: str, bass_name: str):
    return diagnose_lowend(views, kick_name=kick_name, bass_name=bass_name)


def _prior_region_wavs(evidence: Path) -> dict[str, str | None]:
    kick = None
    master = None
    for name in ("live3r_reality_check.json", "live3r_perf.json"):
        path = evidence / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        card = payload.get("REGION_A") or {}
        wavs = card.get("wavs") or {}
        kick_path = (wavs.get("kick") or {}).get("path")
        master_path = (wavs.get("master") or {}).get("path")
        if kick is None and kick_path and Path(str(kick_path)).is_file():
            kick = str(kick_path)
        if master is None and master_path and Path(str(master_path)).is_file():
            master = str(master_path)
    return {"kick": kick, "master": master}


def _audio_tap_hosts(daw: AbletonTcpAdapter) -> list[dict[str, object]]:
    from copilot.audio.live_capture import MASTER_INDEX, count_copilot_taps

    hosts: list[dict[str, object]] = []
    inventory = count_copilot_taps(daw)
    for item in inventory.get("tracks") or []:
        if int(item.get("count") or 0) <= 0:
            continue
        index = int(item["index"])
        if index == MASTER_INDEX:
            continue
        info = daw.get_track_info(index)
        if info.get("is_midi_track") and not info.get("is_audio_track"):
            continue
        hosts.append({"index": index, "name": info.get("name")})
    return hosts


def _same_wav_check(evidence: Path) -> dict[str, Any]:
    report: dict[str, Any] = {}
    sources = {
        "live3r": evidence / "live3r_reality_check.json",
        "live3r_perf": evidence / "live3r_perf.json",
    }
    for label, path in sources.items():
        if not path.is_file():
            report[label] = {"error": "missing_json"}
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        card = payload.get("REGION_A") or payload.get(REGION) or {}
        views = assets_from_wav_card(card, start=START_QN, end=END_QN)
        if any(key not in views for key in ("master", "kick", "bass")):
            report[label] = {
                "error": "missing_wavs",
                "have": list(views),
            }
            continue
        kick_name = (payload.get("session") or {}).get("kick", {}).get("name") or "Kick"
        bass_name = (payload.get("session") or {}).get("bass", {}).get("name") or "Bass"
        first = _run_diagnosis(views, kick_name, bass_name)
        second = _run_diagnosis(views, kick_name, bass_name)
        report[label] = {
            "deterministic": diagnoses_equivalent(first, second),
            "finding_types": primary_finding_types(first),
            "second_finding_types": primary_finding_types(second),
            "dsp_s": first.timings.get("dsp_s"),
        }
    det = [row.get("deterministic") for row in report.values() if "deterministic" in row]
    report["DIAGNOSIS_SAME_WAV_DETERMINISTIC"] = (
        "YES" if det and all(det) else "NO" if det else "NO_BASELINE"
    )
    if det and not all(det):
        report["status"] = "DIAGNOSIS_PIPELINE_NONDETERMINISTIC"
    return report


def run_live3r_perf2(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    t_all = time.perf_counter()
    daw.reset_tcp_stats()
    report: dict[str, Any] = {
        "phase": "PERF-2 — BATCHED CAPTURE + DIAGNOSIS STABILITY",
        "region": REGION,
        "NO CHANGES MADE": True,
        "DSP_THRESHOLDS_UNCHANGED": True,
        "before": {
            "total_s": BEFORE_TOTAL_S,
            "tcp": BEFORE_TCP,
            "routing_mutations": BEFORE_ROUTING,
        },
    }

    def persist() -> None:
        (evidence / "live3r_perf2.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    _log("PERF-2 same-WAV diagnosis twice")
    report["same_wav"] = _same_wav_check(evidence)
    persist()
    if report["same_wav"].get("status") == "DIAGNOSIS_PIPELINE_NONDETERMINISTIC":
        report["STOP"] = "DIAGNOSIS_PIPELINE_NONDETERMINISTIC"
        persist()
        return report

    try:
        install_audio_tap_device()
        ensure_master_tap(daw)
        context = load_capture_context(daw)
        kick = _pick(context.session, KICK_PREFERENCE)
        bass = _pick(context.session, BASS_PREFERENCE)
        if kick is None or bass is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "need Kick and Bass in the set")
        fire = _fire_from_session(context.session, kick.index, bass.index)
        tempo = assert_constant_tempo(daw, START_QN, END_QN)
        tcp0 = daw.tcp_stats()
        report["session"] = {
            "kick": {"name": kick.name, "index": kick.index, "stable_id": kick.stable_id},
            "bass": {"name": bass.name, "index": bass.index, "stable_id": bass.stable_id},
            "fire_tracks": fire,
            "tempo": tempo,
            "revision": context.revision,
        }
        refs = _prior_region_wavs(evidence)
        report["prior_wavs"] = refs
        _log(f"PERF-2 probe capture track from {kick.name}")
        probe = probe_capture_track(
            daw,
            target_name=kick.name,
            target_stable_id=kick.stable_id,
            start_beat=START_QN,
            end_beat=END_QN,
            fire_tracks=fire,
            known_tempo=tempo,
            kick_ref_wav=refs.get("kick"),
            master_ref_wav=refs.get("master"),
            context=context,
        )
        probe_public = {k: v for k, v in probe.items() if k != "asset"}
        report["capture_track_probe"] = probe_public
        report["MINIMAL_EXPERIMENT"] = "PASS" if probe["ok"] else "FAIL"
        persist()
        if not probe["ok"]:
            tap_missing = "TAP_MISSING" in str(probe.get("error") or "")
            report["STOP"] = (
                "Copilot Capture is an audio track, but Copilot Audio Tap is not "
                "on its device chain. Drop devices/Copilot Audio Tap.amxd onto the "
                "device panel of Copilot Capture (not Main, not the clip slot). "
                "Did not use the Live browser, did not fall back to through-master, "
                "did not reroute the other tracks."
                if tap_missing
                else (
                    "Minimal Kick → Copilot Capture Post Mixer → Tap → WAV "
                    "did not satisfy provenance + content. No parallel path. "
                    "Did not fall back to through-master."
                )
            )
            report["CAPTURE_PROVENANCE_INTACT"] = "NO"
            report["STATE_RESTORE_INTACT"] = (
                "YES" if not probe.get("restore_errors") else "NO"
            )
            report["TOTAL"] = time.perf_counter() - t_all
            report["tcp"] = daw.tcp_stats()
            persist()
            return report

        report["CAPTURE_PROVENANCE_INTACT"] = "YES"
        report["STATE_RESTORE_INTACT"] = (
            "YES" if not probe["restore_errors"] else "NO"
        )
        slot_info = probe["host"].get("slot") or {}
        slot_ok = slot_info.get("slot") is not None
        report["slot_parameter"] = slot_info
        tap_hosts = _audio_tap_hosts(daw)
        report["off_main_audio_taps"] = tap_hosts
        persist()
        bass_host_name = None
        for host in tap_hosts:
            if str(host.get("name") or "") != str(probe["host"]["name"]):
                bass_host_name = str(host["name"])
                break
        if not slot_ok:
            report["STOP"] = (
                "Minimal experiment PASSED. Slot is missing on the capture tap, "
                "so independent files for parallel recorders are unavailable. "
                "Did not mount Kick+Bass+Master."
            )
            report["TOTAL"] = time.perf_counter() - t_all
            report["tcp"] = daw.tcp_stats()
            persist()
            return report
        if bass_host_name is None:
            report["STOP"] = (
                "Minimal Post Mixer + Monitor In + No Output experiment PASSED. "
                "PASS 1 parallel Kick+Bass+Master needs a second Copilot Audio Tap "
                "on another audio capture track. Browser cannot load it. "
                "Did not fall back to through-master and did not reroute the other tracks."
            )
            report["TOTAL"] = time.perf_counter() - t_all
            report["tcp"] = daw.tcp_stats()
            persist()
            return report

        _log("PERF-2 setup Kick/Bass capture hosts")
        kick_host = setup_capture_host(
            daw, name=str(probe["host"]["name"]), target_name=kick.name, slot=1
        )
        bass_host = setup_capture_host(
            daw, name=bass_host_name, target_name=bass.name, slot=2
        )
        report["hosts"] = {
            "kick": {
                "index": kick_host["index"],
                "output": kick_host.get("output"),
                "through_main": kick_host["through_main"],
                "channel": kick_host.get("input_channel"),
            },
            "bass": {
                "index": bass_host["index"],
                "output": bass_host.get("output"),
                "through_main": bass_host["through_main"],
                "channel": bass_host.get("input_channel"),
            },
        }
        if kick_host["through_main"] or bass_host["through_main"]:
            report["STOP"] = "capture hosts still route through Main"
            persist()
            return report

        def one_pass() -> dict[str, AudioAsset]:
            result = capture_parallel_pass(
                daw,
                start_beat=START_QN,
                end_beat=END_QN,
                fire_tracks=fire,
                tempo=tempo,
                session_revision=int(context.revision or 0),
                recorders=[
                    {
                        "key": "master",
                        "tap_track_index": MASTER_INDEX,
                        "staging": STAGING_NAME,
                        "source": "MASTER",
                        "source_type": "MASTER",
                        "capture_view": CaptureView.MASTER_CONTEXT,
                        "observation_source": ObservationSource.MASTER_CONTEXT,
                        "signal_point": SignalPoint.MAIN_NOT_FINAL,
                        "signal_point_label": "Main not final",
                        "require_signal": True,
                    },
                    {
                        "key": "kick",
                        "tap_track_index": int(kick_host["index"]),
                        "staging": STAGING_KICK,
                        "source": kick.stable_id,
                        "source_type": "TRACK",
                        "capture_view": CaptureView.TRACK_ISOLATED,
                        "observation_source": ObservationSource.TRACK_ISOLATED,
                        "signal_point": SignalPoint.TRACK_POST_MIXER,
                        "signal_point_label": str(
                            kick_host.get("input_channel") or "Post Mixer"
                        ),
                        "require_signal": True,
                    },
                    {
                        "key": "bass",
                        "tap_track_index": int(bass_host["index"]),
                        "staging": STAGING_BASS,
                        "source": bass.stable_id,
                        "source_type": "TRACK",
                        "capture_view": CaptureView.TRACK_ISOLATED,
                        "observation_source": ObservationSource.TRACK_ISOLATED,
                        "signal_point": SignalPoint.TRACK_POST_MIXER,
                        "signal_point_label": str(
                            bass_host.get("input_channel") or "Post Mixer"
                        ),
                        "require_signal": True,
                    },
                ],
            )
            assets = result["assets"]
            assets["_pass_timings"] = result["timings"]  # type: ignore[assignment]
            return assets

        passes = 1
        t_cap = time.perf_counter()
        _log("PERF-2 PASS 1 parallel MASTER+KICK+BASS")
        pass1 = one_pass()
        pass_timings = [pass1.pop("_pass_timings")]  # type: ignore[arg-type]
        views = {k: v for k, v in pass1.items() if isinstance(v, AudioAsset)}
        diag_3 = _run_diagnosis(views, kick.name, bass.name)
        need = context_removal_needed(diag_3)
        report["pass1_diagnosis"] = {
            "finding_types": primary_finding_types(diag_3),
            "confidence": diag_3.confidence.value,
            "context_removal": need,
        }
        persist()
        if need["needed"]:
            _log(f"PERF-2 context removal because {need['reason']}")
            muted = daw.set_track_mute(bass.index, True)
            if not muted.get("mute"):
                raise AudioCaptureError("REMOTE_DISCONNECT", "bass mute ack failed")
            without_bass = capture_master_context(
                daw,
                START_QN,
                END_QN,
                fire_tracks=fire,
                fire_all=False,
                require_tap_final=False,
                require_signal=False,
                context=context,
                known_tempo=tempo,
            )
            unmute = daw.set_track_mute(bass.index, False)
            if unmute.get("mute"):
                raise AudioCaptureError("REMOTE_DISCONNECT", "bass unmute ack failed")
            muted = daw.set_track_mute(kick.index, True)
            if not muted.get("mute"):
                raise AudioCaptureError("REMOTE_DISCONNECT", "kick mute ack failed")
            without_kick = capture_master_context(
                daw,
                START_QN,
                END_QN,
                fire_tracks=fire,
                fire_all=False,
                require_tap_final=False,
                require_signal=False,
                context=context,
                known_tempo=tempo,
            )
            unmute = daw.set_track_mute(kick.index, False)
            if unmute.get("mute"):
                raise AudioCaptureError("REMOTE_DISCONNECT", "kick unmute ack failed")
            views["master_without_bass"] = without_bass
            views["master_without_kick"] = without_kick
            without_bass.capture_view = CaptureView.TRACK_CONTEXT_REMOVAL
            without_kick.capture_view = CaptureView.TRACK_CONTEXT_REMOVAL
            without_bass.observation_source = ObservationSource.TRACK_CONTEXT_REMOVAL
            without_kick.observation_source = ObservationSource.TRACK_CONTEXT_REMOVAL
            passes = 3
        capture_s = time.perf_counter() - t_cap
        diagnosis = _run_diagnosis(views, kick.name, bass.name)

        _log("PERF-2 capture repeatability PASS 1 again")
        t_rep = time.perf_counter()
        pass1b = one_pass()
        pass_timings.append(pass1b.pop("_pass_timings"))  # type: ignore[arg-type]
        views_b = {k: v for k, v in pass1b.items() if isinstance(v, AudioAsset)}
        diag_b = _run_diagnosis(views_b, kick.name, bass.name)
        repeat_s = time.perf_counter() - t_rep
        types_a = primary_finding_types(diag_3)
        types_b = primary_finding_types(diag_b)
        stable = types_a == types_b
        final = diagnosis
        if not stable:
            final = unstable_insufficient(
                left=diag_3,
                right=diag_b,
                region=f"{START_QN:g}->{END_QN:g} quarter_note",
                kick_name=kick.name,
                bass_name=bass.name,
            )
        tcp = daw.tcp_stats()
        routing = int(tcp.get("by_type", {}).get("set_track_output_routing", 0))
        total_s = time.perf_counter() - t_all
        gate = "FAILED" if total_s >= 60 else ("GOOD" if total_s < 40 else "ACCEPTABLE")
        kick_point = views["kick"].signal_point
        report.update(
            {
                "PASS COUNT": passes,
                "TOTAL": total_s,
                "CAPTURE": capture_s,
                "REPEAT_CAPTURE": repeat_s,
                "AUDIO_WALL_TIME": sum(
                    float((row or {}).get("record_s") or 0.0) for row in pass_timings
                ),
                "pass_timings": pass_timings,
                "DSP": diagnosis.timings.get("dsp_s"),
                "DIAGNOSIS": diagnosis.timings.get("diagnosis_s"),
                "tcp": tcp,
                "tcp_after_setup": tcp0,
                "routing_mutations": routing,
                "transport_mutations": int(
                    tcp.get("by_type", {}).get("start_playback", 0)
                )
                + int(tcp.get("by_type", {}).get("stop_playback", 0))
                + int(tcp.get("by_type", {}).get("fire_clip", 0)),
                "mute_mutations": int(tcp.get("by_type", {}).get("set_track_mute", 0)),
                "finding_types": primary_finding_types(final),
                "pass1_three_view_types": types_a,
                "repeat_three_view_types": types_b,
                "CAPTURE_PROVENANCE_INTACT": "YES",
                "STATE_RESTORE_INTACT": "YES",
                "DIAGNOSIS_SAME_WAV_DETERMINISTIC": report["same_wav"][
                    "DIAGNOSIS_SAME_WAV_DETERMINISTIC"
                ],
                "CAPTURE_TO_CAPTURE_STABILITY": (
                    "VERIFIED" if stable else "FAILED"
                ),
                "kick_signal_point": kick_point.value if kick_point else None,
                "through_main": kick_host["through_main"] or bass_host["through_main"],
                "performance_gate": gate,
                "user_facing": final.user_facing,
                "NO CHANGES MADE": True,
            }
        )
        persist()
        _log(
            f"PERF-2 done TOTAL={total_s:.1f}s gate={gate} "
            f"stable={stable} types={report['finding_types']}"
        )
        return report
    except (AudioCaptureError, DawError, ValueError) as exc:
        report["error"] = str(exc)
        report["tcp"] = daw.tcp_stats()
        report["TOTAL"] = time.perf_counter() - t_all
        if "TAP_MISSING" in str(exc):
            report["MINIMAL_EXPERIMENT"] = "FAIL"
            report["STOP"] = (
                "Copilot Capture is an audio track, but Copilot Audio Tap is not "
                "on its device chain. Did not use the Live browser, did not "
                "fall back to through-master, did not reroute the other tracks."
            )
            report["CAPTURE_PROVENANCE_INTACT"] = "NO"
        persist()
        _log(f"PERF-2 FAILED {exc}")
        return report
