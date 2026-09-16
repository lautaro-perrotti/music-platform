from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    CAPTURE_HOST,
    STAGING_BASS,
    STAGING_KICK,
    STAGING_NAME,
    capture_parallel_pass,
    setup_capture_host,
)
from copilot.audio.live3r import BASS_PREFERENCE, KICK_PREFERENCE
from copilot.audio.live3r_perf2 import _fire_from_session, _pick
from copilot.audio.live3r_perf3 import (
    END_QN,
    REGION,
    START_QN,
    _profile,
    _sanity_three,
)
from copilot.audio.live_capture import (
    MASTER_INDEX,
    AudioAsset,
    AudioCaptureError,
    load_capture_context,
    master_chain_signal_point,
    master_tap_position,
    rebuild_audio_tap_device,
    set_tap_recording,
    verify_rec_close_releases_handles,
)
from copilot.audio.tap_trust import (
    FAILED,
    FINALIZING,
    PREPARED,
    RECORDING,
    UDP_REMOVED_PROTOCOL,
    VERIFIED,
    CaptureJournal,
    assert_unique_slots,
    inventory_taps,
    lom_crosstalk_test,
    reconcile_stale_taps,
    routing_claim,
    sha256_file,
    tap_instance_id,
    udp_control_audit,
    udp_crosstalk_test,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.schemas.observation import CaptureView, ObservationSource, SignalPoint


def _log(msg: str) -> None:
    print(msg, flush=True)


def _status(ok: bool, *, limited: str | None = None) -> str:
    if ok:
        return "FIXTURE_VERIFIED"
    if limited:
        return limited
    return "FAILED"


def _lag_s(left: np.ndarray, right: np.ndarray, sr: int) -> float:
    n = min(len(left), len(right))
    if n < 32:
        return 0.0
    a = left[:n] - float(np.mean(left[:n]))
    b = right[:n] - float(np.mean(right[:n]))
    corr = np.correlate(a, b, mode="full")
    lag = int(np.argmax(corr) - (n - 1))
    return float(lag) / float(sr)


def _recorders(
    *,
    kick_index: int,
    bass_index: int,
    kick_id: str,
    bass_id: str,
    kick_channel: str,
    bass_channel: str,
    main_point: SignalPoint,
) -> list[dict[str, Any]]:
    main_label = "Main " + (
        "final" if main_point is SignalPoint.MAIN_FINAL else "not final"
    )
    return [
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
            "signal_point_label": main_label,
            "routing": "MAIN",
            "returns_included": True,
            "require_signal": True,
        },
        {
            "key": "kick",
            "tap_track_index": kick_index,
            "staging": STAGING_KICK,
            "slot": 1,
            "source": kick_id,
            "source_type": "TRACK",
            "capture_view": CaptureView.TRACK_ISOLATED,
            "observation_source": ObservationSource.TRACK_ISOLATED,
            "signal_point": SignalPoint.TRACK_POST_MIXER,
            "signal_point_label": kick_channel,
            "routing": "OFF_MAIN",
            "returns_included": False,
            "require_signal": True,
        },
        {
            "key": "bass",
            "tap_track_index": bass_index,
            "staging": STAGING_BASS,
            "slot": 2,
            "source": bass_id,
            "source_type": "TRACK",
            "capture_view": CaptureView.TRACK_ISOLATED,
            "observation_source": ObservationSource.TRACK_ISOLATED,
            "signal_point": SignalPoint.TRACK_POST_MIXER,
            "signal_point_label": bass_channel,
            "routing": "OFF_MAIN",
            "returns_included": False,
            "require_signal": True,
        },
    ]


def _restore_readback(
    daw: AbletonTcpAdapter, inventory: list[dict[str, Any]]
) -> dict[str, Any]:
    pos = daw.get_playback_position()
    recs = []
    armed = False
    for row in inventory:
        rec = float(row.get("rec") or 0.0)
        try:
            from copilot.audio.tap_trust import _read_rec

            rec = _read_rec(daw, int(row["track_index"]), int(row["device_index"])) or 0.0
        except DawError:
            pass
        recs.append({"tap_instance_id": row["tap_instance_id"], "rec": rec})
        if rec >= 0.5:
            armed = True
    return {
        "playing": bool(pos.get("is_playing")),
        "song_time": pos.get("current_song_time"),
        "rec": recs,
        "any_armed": armed,
        "ok": (not armed) and (not pos.get("is_playing")),
    }


def run_live3r_trust(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    t_all = time.perf_counter()
    daw.reset_tcp_stats()
    report: dict[str, Any] = {
        "phase": "CAPTURE FOUNDATION CLOSURE — MULTI-TAP TRUST",
        "region": REGION,
        "DSP_THRESHOLDS_UNCHANGED": True,
        "THROUGH_MASTER_PATH": "NOT USED",
        "NO LIVE-4": True,
        "NO DIAGNOSIS": True,
    }

    def persist() -> None:
        (evidence / "live3r_trust.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    try:
        _log("TRUST rebuild tap source (disk); Live instances are whatever was dropped")
        report["tap_rebuild"] = rebuild_audio_tap_device()
        report["UDP AUDIT"] = udp_control_audit()
        persist()

        context = load_capture_context(daw)
        kick = _pick(context.session, KICK_PREFERENCE)
        bass = _pick(context.session, BASS_PREFERENCE)
        if kick is None or bass is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "need Kick and Bass")
        fire = _fire_from_session(context.session, kick.index, bass.index)
        kick_host = setup_capture_host(
            daw, name=CAPTURE_HOST, target_name=kick.name, slot=1
        )
        bass_host = setup_capture_host(
            daw, name=CAPTURE_BASS, target_name=bass.name, slot=2
        )
        daw.snapshot(include_notes=False)
        inventory = inventory_taps(daw)
        report["TAP INVENTORY"] = {
            "count": len(inventory),
            "instances": inventory,
        }
        persist()

        try:
            assert_unique_slots(inventory)
            collision = {"ok": True, "duplicates": []}
        except AudioCaptureError as exc:
            collision = {"ok": False, "error": str(exc)}
            report["SLOT COLLISION TEST"] = collision
            report["STOP"] = str(exc)
            persist()
            return report
        report["SLOT COLLISION TEST"] = collision

        recorder_ids = {
            tap_instance_id(MASTER_INDEX, 0),
            tap_instance_id(int(kick_host["index"]), 0),
            tap_instance_id(int(bass_host["index"]), 0),
        }
        for row in inventory:
            if int(row["track_index"]) in {
                MASTER_INDEX,
                int(kick_host["index"]),
                int(bass_host["index"]),
            }:
                recorder_ids.add(row["tap_instance_id"])
        try:
            stale = reconcile_stale_taps(daw, inventory, recorder_ids)
            report["STALE TAP RECONCILIATION"] = stale
        except AudioCaptureError as exc:
            report["STALE TAP RECONCILIATION"] = {"ok": False, "error": str(exc)}
            report["STOP"] = str(exc)
            persist()
            return report

        lom = lom_crosstalk_test(daw, inventory)
        udp = udp_crosstalk_test(daw, inventory)
        for row in inventory:
            set_tap_recording(daw, False, int(row["track_index"]), broadcast_udp=False)
        time.sleep(0.5)
        report["UDP CROSS-TALK TEST"] = {
            "lom": lom,
            "udp": udp,
            "instance_control": (
                "LIMITED"
                if lom["ok"]
                else "FAILED"
            ),
        }
        persist()

        kick_sends = daw.get_track_sends(int(kick_host["index"]))
        bass_sends = daw.get_track_sends(int(bass_host["index"]))
        kick_route = routing_claim(
            input_type=str(kick_host.get("input_type")),
            input_channel=str(kick_host.get("input_channel")),
            output=str(kick_host.get("output")),
            monitoring=str(kick_host.get("monitoring")),
            through_main=bool(kick_host.get("through_main")),
            sends=kick_sends,
            target_name=kick.name,
        )
        bass_route = routing_claim(
            input_type=str(bass_host.get("input_type")),
            input_channel=str(bass_host.get("input_channel")),
            output=str(bass_host.get("output")),
            monitoring=str(bass_host.get("monitoring")),
            through_main=bool(bass_host.get("through_main")),
            sends=bass_sends,
            target_name=bass.name,
        )
        report["KICK/BASS ROUTING SEMANTICS"] = {
            "kick": kick_route,
            "bass": bass_route,
            "send_api_rows": {"kick": kick_sends, "bass": bass_sends},
        }

        pos = master_tap_position(daw)
        report["MAIN SIGNAL POINT"] = {
            "before": pos,
            "devices": pos.get("devices"),
        }
        if pos["is_last"]:
            report["MAIN SIGNAL POINT"]["claim"] = "MAIN_FINAL"
        else:
            tap_index = pos.get("tap_index")
            moved = None
            if tap_index is not None:
                try:
                    moved = daw.move_device_right(MASTER_INDEX, int(tap_index))
                except DawError as exc:
                    moved = {"error": str(exc)}
            after = master_tap_position(daw)
            report["MAIN SIGNAL POINT"]["move"] = moved
            report["MAIN SIGNAL POINT"]["after"] = after
            report["MAIN SIGNAL POINT"]["claim"] = (
                "MAIN_FINAL" if after.get("is_last") else "MAIN_NOT_FINAL"
            )
            if after.get("is_last") is not True:
                report["MAIN SIGNAL POINT"]["STOP"] = (
                    "Could not move Copilot Audio Tap after Utility on Main. "
                    "Put the tap last on the Master chain, then re-run. "
                    "Did not declare MAIN_FINAL."
                )
        persist()

        close = verify_rec_close_releases_handles(
            daw,
            [
                {
                    "key": "master",
                    "tap_track_index": MASTER_INDEX,
                    "slot": 0,
                    "staging": STAGING_NAME,
                },
                {
                    "key": "kick",
                    "tap_track_index": int(kick_host["index"]),
                    "slot": 1,
                    "staging": STAGING_KICK,
                },
                {
                    "key": "bass",
                    "tap_track_index": int(bass_host["index"]),
                    "slot": 2,
                    "staging": STAGING_BASS,
                },
            ],
        )
        report["REC CLOSE"] = close
        if not close.get("ok"):
            report["STOP"] = "Rec=0 did not release handles"
            persist()
            return report

        main_point = master_chain_signal_point(daw)
        recs = _recorders(
            kick_index=int(kick_host["index"]),
            bass_index=int(bass_host["index"]),
            kick_id=kick.stable_id,
            bass_id=bass.stable_id,
            kick_channel=str(kick_host.get("input_channel")),
            bass_channel=str(bass_host.get("input_channel")),
            main_point=main_point,
        )
        from uuid import uuid4

        pass_id = uuid4().hex[:12]
        journal = CaptureJournal(pass_id)
        journal.record(
            PREPARED,
            inventory=inventory,
            region=REGION,
            start_beat=START_QN,
            end_beat=END_QN,
            revision=context.revision,
        )
        journal.record(RECORDING)
        _log("TRUST PASS 1 with journal")
        t_pass = time.perf_counter()
        try:
            one = capture_parallel_pass(
                daw,
                start_beat=START_QN,
                end_beat=END_QN,
                fire_tracks=fire,
                tempo=float(context.tempo or 120.0),
                session_revision=int(context.revision or 0),
                pass_id=pass_id,
                recorders=recs,
            )
        except AudioCaptureError as exc:
            journal.record(FAILED, error=str(exc))
            raise
        journal.record(FINALIZING, timings=one.get("timings"))
        assets = one["assets"]
        three = _sanity_three(assets["master"], assets["kick"], assets["bass"])
        hashes = {
            key: sha256_file(Path(asset.file_path)) for key, asset in assets.items()
        }
        restore = _restore_readback(daw, inventory_taps(daw))
        if three["ok"] and restore["ok"]:
            journal.record(
                VERIFIED,
                hashes=hashes,
                staging_release=(one.get("timings") or {}).get("staging_release"),
            )
        else:
            journal.record(FAILED, sanity=three["ok"], restore=restore)
        report["PASS 1"] = {
            "ok": three["ok"],
            "pass_id": pass_id,
            "elapsed_s": time.perf_counter() - t_pass,
            "wavs": {k: v.file_path for k, v in assets.items()},
            "hashes": hashes,
            "content": {
                "corr_bass_vs_kick": three["corr_bass_vs_kick"],
                "corr_bass_vs_master": three["corr_bass_vs_master"],
                "gates": three["gates"],
            },
            "timings": one.get("timings"),
            "file_ownership": (one.get("timings") or {}).get("staging_release"),
        }
        report["FILE OWNERSHIP TEST"] = {
            "ok": (one.get("timings") or {}).get("staging_release")
            == {"master": "moved", "kick": "moved", "bass": "moved"},
            "staging_release": (one.get("timings") or {}).get("staging_release"),
            "unique_dests": len({v.file_path for v in assets.values()}) == 3,
        }
        report["CAPTURE JOURNAL STATUS"] = {
            "path": str(journal.path),
            "last": journal.last_status(),
            "claim": journal.claim_verified(),
        }
        report["STATE RESTORE"] = restore

        from copilot.audio.live3r_perf3 import _mono

        m_mono, sr = _mono(assets["master"].file_path)
        k_mono, _ = _mono(assets["kick"].file_path)
        b_mono, _ = _mono(assets["bass"].file_path)
        alignment = {
            "frames": {
                "master": assets["master"].actual_frames,
                "kick": assets["kick"].actual_frames,
                "bass": assets["bass"].actual_frames,
            },
            "duration_s": {
                "master": assets["master"].duration,
                "kick": assets["kick"].duration,
                "bass": assets["bass"].duration,
            },
            "xcorr_lag_s": {
                "kick_vs_master": _lag_s(k_mono, m_mono, sr),
                "bass_vs_master": _lag_s(b_mono, m_mono, sr),
                "bass_vs_kick": _lag_s(b_mono, k_mono, sr),
            },
            "same_frame_count": len(
                {
                    assets["master"].actual_frames,
                    assets["kick"].actual_frames,
                    assets["bass"].actual_frames,
                }
            )
            == 1,
            "onset_not_used_as_truth": True,
        }
        max_lag = max(abs(v) for v in alignment["xcorr_lag_s"].values())
        alignment["measured_tolerance_s"] = max_lag
        alignment["claim"] = (
            "FIXTURE_VERIFIED" if alignment["same_frame_count"] and max_lag < 0.08 else "LIMITED"
        )
        report["ALIGNMENT RESULT"] = alignment
        persist()

        _log("TRUST 5x repeatability")
        repeats: list[dict[str, Any]] = []
        unstable = False
        for index in range(5):
            rid = uuid4().hex[:12]
            j = CaptureJournal(rid)
            j.record(PREPARED, run=index)
            j.record(RECORDING)
            captured = capture_parallel_pass(
                daw,
                start_beat=START_QN,
                end_beat=END_QN,
                fire_tracks=fire,
                tempo=float(context.tempo or 120.0),
                session_revision=int(context.revision or 0),
                pass_id=rid,
                recorders=_recorders(
                    kick_index=int(kick_host["index"]),
                    bass_index=int(bass_host["index"]),
                    kick_id=kick.stable_id,
                    bass_id=bass.stable_id,
                    kick_channel=str(kick_host.get("input_channel")),
                    bass_channel=str(bass_host.get("input_channel")),
                    main_point=master_chain_signal_point(daw),
                ),
            )
            a = captured["assets"]
            profiles = {
                "master": _profile(a["master"].file_path, "kick"),
                "kick": _profile(a["kick"].file_path, "kick"),
                "bass": _profile(a["bass"].file_path, "bass"),
            }
            three_r = _sanity_three(a["master"], a["kick"], a["bass"])
            if not three_r["ok"]:
                unstable = True
                j.record(FAILED, run=index)
            else:
                j.record(VERIFIED, run=index)
            repeats.append(
                {
                    "pass_id": rid,
                    "ok": three_r["ok"],
                    "rms": {k: p["rms"] for k, p in profiles.items()},
                    "peak": {k: p["peak"] for k, p in profiles.items()},
                    "events": {k: p["events"] for k, p in profiles.items()},
                    "low_vs_mid": {k: p["low_vs_mid"] for k, p in profiles.items()},
                    "frames": {k: x.actual_frames for k, x in a.items()},
                }
            )
        def _spread(key: str, view: str) -> float:
            vals = [float(item[key][view]) for item in repeats]
            return max(vals) - min(vals)

        for view in ("master", "kick", "bass"):
            event_spread = max(int(item["events"][view]) for item in repeats) - min(
                int(item["events"][view]) for item in repeats
            )
            if event_spread > 2 or _spread("rms", view) > 0.03:
                unstable = True
        report["5x REPEATABILITY"] = {
            "runs": repeats,
            "claim": "CAPTURE_UNSTABLE" if unstable else "FIXTURE_VERIFIED",
            "rms_spread": {v: _spread("rms", v) for v in ("master", "kick", "bass")},
        }
        persist()

        tcp = daw.tcp_stats()
        report["PERFORMANCE"] = {
            "total_s": time.perf_counter() - t_all,
            "pass1_s": report["PASS 1"]["elapsed_s"],
            "tcp": tcp,
            "get_track_info": (tcp.get("by_type") or {}).get("get_track_info"),
            "note": "Correctness first. Snapshot reused for inventory when last_track_infos is warm.",
        }
        report["disk_tap_protocol_source"] = UDP_REMOVED_PROTOCOL
        report["STOP"] = (
            "Multi-tap trust slice complete. No diagnosis, no MusicPlan, no LIVE-4."
        )
        persist()
        return report
    except (AudioCaptureError, DawError, ValueError) as exc:
        report["error"] = str(exc)
        report["tcp"] = daw.tcp_stats()
        report["TOTAL"] = time.perf_counter() - t_all
        persist()
        _log(f"TRUST FAILED {exc}")
        return report
