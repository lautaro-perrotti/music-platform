from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import soundfile as sf

from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    CAPTURE_HOST,
    STAGING_BASS,
    STAGING_KICK,
    STAGING_NAME,
    capture_parallel_pass,
    mixer_fingerprint,
    restore_host_routing,
    setup_capture_host,
)
from copilot.audio.live3r import BASS_PREFERENCE, KICK_PREFERENCE, SKIP_FIRE_PREFIXES
from copilot.audio.live3r_perf2 import _fire_from_session, _pick
from copilot.audio.live_capture import (
    EXPECTED_TAP_PROTOCOL,
    MASTER_INDEX,
    AudioAsset,
    AudioCaptureError,
    load_capture_context,
    master_chain_signal_point,
    rebuild_audio_tap_device,
    tap_has_slot,
    tap_parameter_names,
    tap_protocol_version,
    verify_rec_close_releases_handles,
    verify_slot_roundtrip,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.schemas.observation import CaptureView, ObservationSource, SignalPoint

REGION = "REGION_A"
START_QN = 0.0
END_QN = 8.0


def _log(msg: str) -> None:
    print(msg, flush=True)


def _mono(path: str) -> tuple[np.ndarray, int]:
    data, sr = sf.read(path, always_2d=True)
    return np.mean(np.asarray(data, dtype=np.float64), axis=1), int(sr)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    if n < 16:
        return 0.0
    left = a[:n] - float(np.mean(a[:n]))
    right = b[:n] - float(np.mean(b[:n]))
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denom <= 0:
        return 0.0
    return float(np.dot(left, right) / denom)


def _profile(path: str, role: str) -> dict[str, Any]:
    from copilot.audio.lowend import (
        band_energy_over_time,
        detect_transients,
        low_frequency_envelope,
        prune_kick_attacks,
    )

    mono, sr = _mono(path)
    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(mono**2)))
    hits = detect_transients(mono, sr, role=role)
    pruned = (
        prune_kick_attacks(hits["attacks"], duration_s=len(mono) / float(sr))
        if role == "kick"
        else hits["attacks"]
    )
    env = low_frequency_envelope(mono, sr)
    bands = band_energy_over_time(mono, sr)
    low = 0.0
    mid = 0.0
    for name, series in bands["bands"].items():
        lo, hi = (int(part) for part in name.split("-"))
        energy = float(np.mean(series)) if series else 0.0
        if hi <= 120:
            low += energy
        elif lo >= 200:
            mid += energy
    return {
        "path": path,
        "peak": peak,
        "rms": rms,
        "events": len(pruned),
        "raw_onsets": hits.get("count"),
        "low_end_mean": float(np.mean(env["energy"])) if env["energy"] else 0.0,
        "low_vs_mid": None if (low + mid) <= 0 else low / (low + mid),
        "attack_times_s": [float(item["time_s"]) for item in pruned[:8]],
    }


def _asset_card(asset: AudioAsset) -> dict[str, Any]:
    return {
        "path": asset.file_path,
        "raw_path": asset.raw_file_path,
        "capture_id": asset.capture_id,
        "pass_id": asset.pass_id,
        "slot": asset.slot,
        "source": asset.source,
        "source_stable_id": asset.source_stable_id,
        "view": asset.capture_view.value if asset.capture_view else None,
        "signal_point": asset.signal_point.value if asset.signal_point else None,
        "signal_point_label": asset.signal_point_label,
        "routing": asset.routing,
        "returns_included": asset.returns_included,
        "requested_region": f"{asset.requested_start_beat:g}->{asset.requested_end_beat:g} quarter_note",
        "requested_start_qn": asset.requested_start_qn,
        "requested_end_qn": asset.requested_end_qn,
        "transport_start_estimate_qn": asset.transport_start_estimate_qn,
        "capture_arm_time": asset.capture_arm_time,
        "analysis_start_offset": asset.analysis_start_offset,
        "analysis_duration": asset.analysis_duration,
        "tempo": asset.tempo,
        "first_poll_qn": asset.first_poll_qn,
        "session_revision": asset.session_revision,
        "sample_rate": asset.sample_rate,
        "channels": asset.channels,
        "expected_frames": asset.expected_frames,
        "actual_frames": asset.actual_frames,
        "finite_samples": asset.finite_samples,
        "duration": asset.duration,
        "rms": asset.rms,
        "peak": asset.peak,
    }


def _sanity_two(master: AudioAsset, kick: AudioAsset) -> dict[str, Any]:
    master_p = _profile(master.file_path, "kick")
    kick_p = _profile(kick.file_path, "kick")
    m_mono, _ = _mono(master.file_path)
    k_mono, _ = _mono(kick.file_path)
    corr = _corr(k_mono, m_mono)
    distinct = corr < 0.80 and kick_p["rms"] < 0.90 * max(master_p["rms"], 1.0e-12)
    kick_ok = kick_p["peak"] > 1.0e-3 and kick_p["events"] >= 2
    master_ok = master_p["peak"] > 1.0e-3 and master_p["rms"] > kick_p["rms"]
    return {
        "ok": distinct and kick_ok and master_ok,
        "corr_kick_vs_master": corr,
        "master": master_p,
        "kick": kick_p,
        "gates": {
            "two_distinct_files": master.file_path != kick.file_path,
            "same_pass_id": master.pass_id == kick.pass_id,
            "master_has_mix": master_ok,
            "kick_has_attacks": kick_ok,
            "kick_not_master": distinct,
        },
    }


def _sanity_three(master: AudioAsset, kick: AudioAsset, bass: AudioAsset) -> dict[str, Any]:
    two = _sanity_two(master, kick)
    bass_p = _profile(bass.file_path, "bass")
    b_mono, _ = _mono(bass.file_path)
    k_mono, _ = _mono(kick.file_path)
    m_mono, _ = _mono(master.file_path)
    vs_kick = _corr(b_mono, k_mono)
    vs_master = _corr(b_mono, m_mono)
    bass_ok = bass_p["peak"] > 1.0e-3 and bass_p["rms"] > 1.0e-4
    distinct = vs_kick < 0.85 and vs_master < 0.85
    return {
        "ok": bool(two["ok"] and bass_ok and distinct),
        "two_tap": two,
        "bass": bass_p,
        "corr_bass_vs_kick": vs_kick,
        "corr_bass_vs_master": vs_master,
        "gates": {
            **two["gates"],
            "bass_has_signal": bass_ok,
            "bass_not_kick": vs_kick < 0.85,
            "bass_not_master": vs_master < 0.85,
            "three_distinct_files": len({master.file_path, kick.file_path, bass.file_path}) == 3,
        },
    }


def _public_asset_map(assets: dict[str, AudioAsset]) -> dict[str, Any]:
    return {key: _asset_card(asset) for key, asset in assets.items()}


def _require_slot(daw: AbletonTcpAdapter, track_index: int, label: str) -> dict[str, Any]:
    names = tap_parameter_names(daw, track_index)
    has = tap_has_slot(daw, track_index)
    return {"label": label, "index": track_index, "parameters": names, "has_slot": has}


def run_live3r_perf3(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    t_all = time.perf_counter()
    daw.reset_tcp_stats()
    report: dict[str, Any] = {
        "phase": "PERF-3 — SLOT-ENABLED MULTI-TAP + PARALLEL PASS 1",
        "region": REGION,
        "NO CHANGES MADE": True,
        "DSP_THRESHOLDS_UNCHANGED": True,
        "THROUGH_MASTER_PATH": "NOT USED",
    }

    def persist() -> None:
        (evidence / "live3r_perf3.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    restored: list[tuple[int, dict]] = []
    try:
        _log("PERF-3 rebuild Slot-enabled tap")
        t_setup = time.perf_counter()
        rebuilt = rebuild_audio_tap_device()
        report["tap_rebuild"] = rebuilt
        persist()

        report["tap_parameters_before_reload"] = {
            "master": tap_parameter_names(daw, MASTER_INDEX),
        }

        context = load_capture_context(daw)
        kick = _pick(context.session, KICK_PREFERENCE)
        bass = _pick(context.session, BASS_PREFERENCE)
        if kick is None or bass is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "need Kick and Bass in the set")
        capture_track = context.session.track_by_name(CAPTURE_HOST)
        if capture_track is not None:
            report["tap_parameters_before_reload"]["capture"] = tap_parameter_names(
                daw, capture_track.index
            )
        fire = _fire_from_session(context.session, kick.index, bass.index)
        report["session"] = {
            "kick": {"name": kick.name, "index": kick.index, "stable_id": kick.stable_id},
            "bass": {"name": bass.name, "index": bass.index, "stable_id": bass.stable_id},
            "fire_tracks": fire,
            "tempo": context.tempo,
            "revision": context.revision,
        }

        _log("PERF-3 reload taps if Live still hides Slot")
        kick_host = setup_capture_host(
            daw, name=CAPTURE_HOST, target_name=kick.name, slot=1
        )
        restored.append((int(kick_host["index"]), kick_host["before"]))
        master_slot = _require_slot(daw, MASTER_INDEX, "master")
        capture_slot = _require_slot(daw, int(kick_host["index"]), "copilot_capture")
        if not master_slot["has_slot"]:
            from copilot.audio.batch_capture import load_tap_on_track

            load_tap_on_track(daw, MASTER_INDEX)
            master_slot = _require_slot(daw, MASTER_INDEX, "master")
        capture_index = int(kick_host["index"])
        bass_track = context.session.track_by_name(CAPTURE_BASS)
        bass_index = None if bass_track is None else int(bass_track.index)
        master_proto = tap_protocol_version(daw, MASTER_INDEX)
        capture_proto = tap_protocol_version(daw, capture_index)
        bass_proto = (
            None if bass_index is None else tap_protocol_version(daw, bass_index)
        )
        report["UPDATED TAP PARAMETERS"] = {
            "master": {**master_slot, "tap_protocol": master_proto},
            "copilot_capture": {
                **_require_slot(daw, capture_index, "copilot_capture"),
                "tap_protocol": capture_proto,
            },
            "copilot_capture_bass": (
                None
                if bass_index is None
                else {
                    **_require_slot(daw, bass_index, "copilot_capture_bass"),
                    "tap_protocol": bass_proto,
                }
            ),
        }
        slot_ok = master_slot["has_slot"] and tap_has_slot(daw, capture_index)
        three_protocol = (
            master_proto == EXPECTED_TAP_PROTOCOL
            and capture_proto == EXPECTED_TAP_PROTOCOL
            and bass_proto == EXPECTED_TAP_PROTOCOL
        )
        report["SLOT-ENABLED TAP"] = "FIXTURE_VERIFIED" if slot_ok else "FAILED"
        report["RUNTIME PROTOCOL"] = (
            "FIXTURE_VERIFIED" if three_protocol else "FAILED"
        )
        persist()
        if not slot_ok:
            report["STOP"] = (
                "Live still does not expose Slot on a loaded Copilot Audio Tap. "
                "Delete the old tap on Main and on Copilot Capture, then drag "
                "devices/Copilot Audio Tap.amxd onto both. Do not drop it on a clip slot."
            )
            report["TOTAL"] = time.perf_counter() - t_all
            report["tcp"] = daw.tcp_stats()
            persist()
            return report
        if bass_index is None or not tap_has_slot(daw, bass_index):
            report["STOP"] = (
                "Copilot Capture Bass is missing a Slot-enabled tap. "
                "Drag devices/Copilot Audio Tap.amxd onto that audio track only."
            )
            report["TOTAL"] = time.perf_counter() - t_all
            report["tcp"] = daw.tcp_stats()
            persist()
            return report
        report["slot_roundtrip"] = {
            "master": verify_slot_roundtrip(daw, MASTER_INDEX, 0),
            "capture": verify_slot_roundtrip(daw, capture_index, 1),
            "bass": verify_slot_roundtrip(daw, bass_index, 2),
        }
        report["capture_sends"] = [
            {"send_index": row.get("send_index"), "level": row.get("level"), "name": row.get("name")}
            for row in daw.get_track_sends(capture_index)
        ]
        report["capture_sends_all_silent"] = all(
            float(row.get("level") or 0.0) <= 1e-5 for row in report["capture_sends"]
        )
        if report["RUNTIME PROTOCOL"] != "FIXTURE_VERIFIED":
            report["STOP"] = (
                "Slot is live, but TapProtocol is not 2 on Main, Copilot Capture, "
                "and Copilot Capture Bass. Replace those three taps with "
                "devices/Copilot Audio Tap.amxd. Did not record."
            )
            report["TOTAL"] = time.perf_counter() - t_all
            report["tcp"] = daw.tcp_stats()
            persist()
            return report
        _log("PERF-3 Rec=0 close must release staging handles")
        report["REC CLOSE"] = verify_rec_close_releases_handles(
            daw,
            [
                {"key": "master", "tap_track_index": MASTER_INDEX, "slot": 0, "staging": STAGING_NAME},
                {"key": "kick", "tap_track_index": capture_index, "slot": 1, "staging": STAGING_KICK},
                {"key": "bass", "tap_track_index": bass_index, "slot": 2, "staging": STAGING_BASS},
            ],
        )
        persist()
        if not report["REC CLOSE"]["ok"]:
            report["STOP"] = (
                "Rec=0 did not release a capture file handle. "
                f"{report['REC CLOSE']}. Did not run PASS 1."
            )
            report["TOTAL"] = time.perf_counter() - t_all
            report["tcp"] = daw.tcp_stats()
            persist()
            return report
        kick_read = {
            "input_type": kick_host.get("input_type"),
            "input_channel": kick_host.get("input_channel"),
            "output": kick_host.get("output"),
            "monitoring": kick_host.get("monitoring"),
            "through_main": kick_host["through_main"],
        }
        kick_off_main = str(kick_read["output"] or "").lower() in {
            "no output",
            "sends only",
        }
        kick_post = "post mixer" in str(kick_read["input_channel"] or "").lower()
        kick_from = kick.name.lower() in str(kick_read["input_type"] or "").lower()
        kick_monitor = str(kick_read["monitoring"] or "").lower() in {"in", "in/in"}
        if not (kick_from and kick_post and kick_monitor and kick_off_main):
            report["STOP"] = f"Kick host routing not off-main Post Mixer: {kick_read}"
            report["TOTAL"] = time.perf_counter() - t_all
            persist()
            return report

        main_point = master_chain_signal_point(daw)
        report["MAIN SIGNAL POINT"] = {
            "value": main_point.value,
            "devices": [
                item.get("name")
                for item in (daw.get_master_info().get("devices") or [])
            ],
        }
        mixer_before = mixer_fingerprint(daw)
        setup_s = time.perf_counter() - t_setup
        report["setup_s"] = setup_s

        _log("PERF-3 TWO-TAP PROOF Master + Kick")
        t_two = time.perf_counter()
        two_id = uuid4().hex[:12]
        two = capture_parallel_pass(
            daw,
            start_beat=START_QN,
            end_beat=END_QN,
            fire_tracks=fire,
            tempo=float(context.tempo or 120.0),
            session_revision=int(context.revision or 0),
            pass_id=two_id,
            recorders=[
                {
                    "key": "master",
                    "tap_track_index": MASTER_INDEX,
                    "staging": STAGING_NAME,
                    "slot": 0,
                    "source": "MASTER",
                    "source_type": "MASTER",
                    "capture_view": CaptureView.MASTER_CONTEXT,
                    "observation_source": ObservationSource.MASTER_CONTEXT,
                    "signal_point": main_point,
                    "signal_point_label": "Main "
                    + ("final" if main_point is SignalPoint.MAIN_FINAL else "not final"),
                    "routing": "MAIN",
                    "returns_included": True,
                    "require_signal": True,
                },
                {
                    "key": "kick",
                    "tap_track_index": int(kick_host["index"]),
                    "staging": STAGING_KICK,
                    "slot": 1,
                    "source": kick.stable_id,
                    "source_type": "TRACK",
                    "capture_view": CaptureView.TRACK_ISOLATED,
                    "observation_source": ObservationSource.TRACK_ISOLATED,
                    "signal_point": SignalPoint.TRACK_POST_MIXER,
                    "signal_point_label": str(kick_read["input_channel"]),
                    "routing": "OFF_MAIN",
                    "returns_included": False,
                    "require_signal": True,
                },
            ],
        )
        two_assets = two["assets"]
        two_sanity = _sanity_two(two_assets["master"], two_assets["kick"])
        mixer_after = mixer_fingerprint(daw)
        host_indexes = {int(kick_host["index"])}
        others_same = [row for row in mixer_before if int(row["index"]) not in host_indexes] == [
            row for row in mixer_after if int(row["index"]) not in host_indexes
        ]
        report["TWO-TAP RESULT"] = {
            "ok": two_sanity["ok"] and others_same,
            "elapsed_s": time.perf_counter() - t_two,
            "pass_id": two.get("pass_id"),
            "timings": two["timings"],
            "wavs": _public_asset_map(two_assets),
            "content": two_sanity,
            "other_tracks_not_rerouted": others_same,
        }
        report["MULTI-TAP FILE ISOLATION"] = (
            "VERIFIED" if two_sanity["ok"] and others_same else "FAILED"
        )
        persist()
        if report["MULTI-TAP FILE ISOLATION"] != "VERIFIED":
            report["STOP"] = "Two-tap proof failed. Did not create a Bass capture track."
            report["TOTAL"] = time.perf_counter() - t_all
            report["tcp"] = daw.tcp_stats()
            persist()
            return report

        _log("PERF-3 setup Copilot Capture Bass")
        try:
            bass_host = setup_capture_host(
                daw, name=CAPTURE_BASS, target_name=bass.name, slot=2
            )
        except AudioCaptureError as exc:
            report["STOP"] = (
                "TWO-TAP PASSED. Bass needs a second Slot-enabled Copilot Audio Tap "
                f"on audio track {CAPTURE_BASS}. {exc} "
                f"Drag devices/Copilot Audio Tap.amxd onto {CAPTURE_BASS} only."
            )
            report["TOTAL"] = time.perf_counter() - t_all
            report["tcp"] = daw.tcp_stats()
            persist()
            return report
        restored.append((int(bass_host["index"]), bass_host["before"]))
        bass_proto_live = tap_protocol_version(daw, int(bass_host["index"]))
        report["bass_host"] = {
            "index": bass_host["index"],
            "input_type": bass_host.get("input_type"),
            "input_channel": bass_host.get("input_channel"),
            "output": bass_host.get("output"),
            "monitoring": bass_host.get("monitoring"),
            "through_main": bass_host["through_main"],
            "tap_protocol": bass_proto_live,
        }
        report["bass_sends"] = [
            {"send_index": row.get("send_index"), "level": row.get("level"), "name": row.get("name")}
            for row in daw.get_track_sends(int(bass_host["index"]))
        ]
        report["bass_sends_all_silent"] = all(
            float(row.get("level") or 0.0) <= 1e-5 for row in report["bass_sends"]
        )
        if (
            not tap_has_slot(daw, int(bass_host["index"]))
            or bass_host["through_main"]
            or bass_proto_live != EXPECTED_TAP_PROTOCOL
        ):
            report["STOP"] = (
                "Bass capture host has no Slot or still routes through Main. "
                f"host={bass_host.get('output')} slot={tap_has_slot(daw, int(bass_host['index']))}"
            )
            report["TOTAL"] = time.perf_counter() - t_all
            persist()
            return report

        _log("PERF-3 PASS 1 Master + Kick + Bass")
        t_pass = time.perf_counter()
        pass_id = uuid4().hex[:12]
        one = capture_parallel_pass(
            daw,
            start_beat=START_QN,
            end_beat=END_QN,
            fire_tracks=fire,
            tempo=float(context.tempo or 120.0),
            session_revision=int(context.revision or 0),
            pass_id=pass_id,
            recorders=[
                {
                    "key": "master",
                    "tap_track_index": MASTER_INDEX,
                    "staging": STAGING_NAME,
                    "slot": 0,
                    "source": "MASTER",
                    "source_type": "MASTER",
                    "capture_view": CaptureView.MASTER_CONTEXT,
                    "observation_source": ObservationSource.MASTER_CONTEXT,
                    "signal_point": main_point,
                    "signal_point_label": "Main "
                    + ("final" if main_point is SignalPoint.MAIN_FINAL else "not final"),
                    "routing": "MAIN",
                    "returns_included": True,
                    "require_signal": True,
                },
                {
                    "key": "kick",
                    "tap_track_index": int(kick_host["index"]),
                    "staging": STAGING_KICK,
                    "slot": 1,
                    "source": kick.stable_id,
                    "source_type": "TRACK",
                    "capture_view": CaptureView.TRACK_ISOLATED,
                    "observation_source": ObservationSource.TRACK_ISOLATED,
                    "signal_point": SignalPoint.TRACK_POST_MIXER,
                    "signal_point_label": str(kick_read["input_channel"]),
                    "routing": "OFF_MAIN",
                    "returns_included": False,
                    "require_signal": True,
                },
                {
                    "key": "bass",
                    "tap_track_index": int(bass_host["index"]),
                    "staging": STAGING_BASS,
                    "slot": 2,
                    "source": bass.stable_id,
                    "source_type": "TRACK",
                    "capture_view": CaptureView.TRACK_ISOLATED,
                    "observation_source": ObservationSource.TRACK_ISOLATED,
                    "signal_point": SignalPoint.TRACK_POST_MIXER,
                    "signal_point_label": str(bass_host.get("input_channel")),
                    "routing": "OFF_MAIN",
                    "returns_included": False,
                    "require_signal": True,
                },
            ],
        )
        assets = one["assets"]
        three = _sanity_three(assets["master"], assets["kick"], assets["bass"])
        mixer_after_pass = mixer_fingerprint(daw)
        host_indexes = {int(kick_host["index"]), int(bass_host["index"])}
        others_same_pass = [
            row for row in mixer_before if int(row["index"]) not in host_indexes
        ] == [
            row for row in mixer_after_pass if int(row["index"]) not in host_indexes
        ]
        pass_ok = bool(three["ok"] and others_same_pass)
        tcp = daw.tcp_stats()
        total_s = time.perf_counter() - t_all
        pass_s = time.perf_counter() - t_pass
        timings = one["timings"]
        report.update(
            {
                "PASS 1 RESULT": {
                    "ok": pass_ok,
                    "elapsed_s": pass_s,
                    "pass_id": one.get("pass_id"),
                    "wavs": _public_asset_map(assets),
                    "content": {
                        "corr_bass_vs_kick": three["corr_bass_vs_kick"],
                        "corr_bass_vs_master": three["corr_bass_vs_master"],
                        "gates": three["gates"],
                        "master": three["two_tap"]["master"],
                        "kick": three["two_tap"]["kick"],
                        "bass": three["bass"],
                    },
                    "timings": timings,
                    "other_tracks_not_rerouted": others_same_pass,
                },
                "PARALLEL PASS 1": "VERIFIED" if pass_ok else "FAILED",
                "OFF-MAIN TRACK CAPTURE": (
                    "VERIFIED"
                    if kick_off_main
                    and not bass_host["through_main"]
                    and report.get("capture_sends_all_silent")
                    and report.get("bass_sends_all_silent")
                    and pass_ok
                    else "FAILED"
                ),
                "SEMANTICS": {
                    "master": {
                        "view": "MASTER_CONTEXT",
                        "signal_point": main_point.value,
                    },
                    "kick": {
                        "view": "TRACK_ISOLATED",
                        "signal_point": "TRACK_POST_MIXER",
                        "routing": "OFF_MAIN",
                        "returns_included": False,
                    },
                    "bass": {
                        "view": "TRACK_ISOLATED",
                        "signal_point": "TRACK_POST_MIXER",
                        "routing": "OFF_MAIN",
                        "returns_included": False,
                    },
                },
                "TOTAL TIME": total_s,
                "CAPTURE": timings.get("total_s"),
                "setup_s": setup_s,
                "two_tap_s": report["TWO-TAP RESULT"]["elapsed_s"],
                "pass1_s": pass_s,
                "pre_roll_s": timings.get("pre_roll_s"),
                "record_s": timings.get("record_s"),
                "recorder_stop_s": timings.get("recorder_stop_s"),
                "file_finalize_s": timings.get("wav_finalize_s"),
                "restore_s": timings.get("restore_s"),
                "DSP": "NOT RUN",
                "tcp": tcp,
                "routing_mutations": int(
                    tcp.get("by_type", {}).get("set_track_output_routing", 0)
                ),
                "transport_mutations": int(tcp.get("by_type", {}).get("start_playback", 0))
                + int(tcp.get("by_type", {}).get("stop_playback", 0))
                + int(tcp.get("by_type", {}).get("fire_clip", 0)),
                "STOP": "PASS 1 complete. No diagnosis, no context removal, no LIVE-4.",
            }
        )
        spent = {
            "setup_s": setup_s,
            "two_tap_s": report["TWO-TAP RESULT"]["elapsed_s"],
            "pass1_s": pass_s,
            "pre_roll_s": timings.get("pre_roll_s"),
            "record_s": timings.get("record_s"),
            "recorder_stop_s": timings.get("recorder_stop_s"),
            "file_finalize_s": timings.get("wav_finalize_s"),
            "restore_s": timings.get("restore_s"),
        }
        report["TOP REMAINING BOTTLENECK"] = max(
            ((name, float(value or 0.0)) for name, value in spent.items()),
            key=lambda item: item[1],
        )
        persist()
        _log(
            f"PERF-3 done TOTAL={total_s:.1f}s two-tap={report['MULTI-TAP FILE ISOLATION']} "
            f"pass1={report['PARALLEL PASS 1']}"
        )
        return report
    except (AudioCaptureError, DawError, ValueError) as exc:
        report["error"] = str(exc)
        report["tcp"] = daw.tcp_stats()
        report["TOTAL"] = time.perf_counter() - t_all
        if "TAP_MISSING" in str(exc):
            report["STOP"] = (
                "Need one manual drag of devices/Copilot Audio Tap.amxd onto the "
                "audio capture track that is missing the Slot-enabled tap. "
                "Did not fall back to through-master."
            )
        persist()
        _log(f"PERF-3 FAILED {exc}")
        return report
    finally:
        for index, before in restored:
            try:
                restore_host_routing(daw, index, before)
            except Exception:
                pass
