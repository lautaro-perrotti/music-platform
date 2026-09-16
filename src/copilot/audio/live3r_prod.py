"""Production capture: one PASS 1. Not a certification suite."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    CAPTURE_HOST,
    capture_parallel_pass,
    ensure_capture_host_ready,
)
from copilot.audio.capture_capability import (
    CERT_ALIGNMENT_ENVELOPE,
    CERT_MAIN_FINAL,
    CERT_MULTI_TAP_ISOLATION,
    CERT_REC_CLOSE,
    CERT_ROUTING_API,
    CERT_SLOT_OWNERSHIP,
    CERT_TAP_PROTOCOL_3,
    CaptureMode,
    CapabilityCache,
    CORE_CAPTURE_VERSION,
    capability_key,
)
from copilot.audio.live3r import BASS_PREFERENCE, KICK_PREFERENCE
from copilot.audio.live3r_perf2 import _fire_from_session, _pick
from copilot.audio.live3r_perf3 import _asset_card, _sanity_three
from copilot.audio.live3r_trust import _recorders
from copilot.audio.live_capture import (
    EXPECTED_TAP_PROTOCOL,
    MASTER_INDEX,
    AudioCaptureError,
    load_capture_context,
    master_chain_signal_point,
    master_tap_position,
)
from copilot.audio.tap_trust import (
    FAILED,
    FINALIZING,
    PREPARED,
    RECORDING,
    VERIFIED,
    CaptureJournal,
    assert_unique_slots,
    inventory_taps,
    reconcile_stale_taps,
    routing_claim,
    sha256_file,
    tap_instance_id,
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.daw.detect import detect_ableton
from copilot.schemas.observation import SignalPoint

REGION = "REGION_A"
START_QN = 0.0
END_QN = 8.0
BEFORE_PASS1_S = 39.7


def _log(msg: str) -> None:
    print(msg, flush=True)


def _phase_tcp(daw: AbletonTcpAdapter, before: dict[str, Any]) -> dict[str, Any]:
    after = daw.tcp_stats()
    return {
        "tcp_delta": int(after["total"]) - int(before["total"]),
        "get_track_info_delta": int(after["get_track_info"])
        - int(before["get_track_info"]),
    }


def run_live3r_prod(daw: AbletonTcpAdapter, evidence: Path) -> dict[str, Any]:
    t_all = time.perf_counter()
    daw.reset_tcp_stats()
    daw.profile_track_info = True
    report: dict[str, Any] = {
        "phase": "PRODUCTION CAPTURE — PASS 1",
        "mode": CaptureMode.PRODUCTION.value,
        "NOT_CERTIFICATION_SUITE": True,
        "region": REGION,
        "requested": "4 s / Master + Kick + Bass / one playback",
        "BEFORE_PASS1_S": BEFORE_PASS1_S,
        "NO LIVE-4": True,
        "NO DIAGNOSIS": True,
        "core_capture_version": CORE_CAPTURE_VERSION,
    }

    def persist() -> None:
        (evidence / "live3r_prod.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

    def mark(name: str, t0: float, tcp0: dict[str, Any], **extra: Any) -> dict[str, Any]:
        row = {
            "s": time.perf_counter() - t0,
            **_phase_tcp(daw, tcp0),
            **extra,
        }
        report.setdefault("BENCHMARK", {})[name] = row
        persist()
        return row

    try:
        detection = detect_ableton()
        cache = CapabilityCache(evidence / "capture_capability.json")
        t0 = time.perf_counter()
        tcp0 = daw.tcp_stats()
        context = load_capture_context(daw)
        mark("snapshot", t0, tcp0, snapshot_source=daw.snapshot_source)

        kick = _pick(context.session, KICK_PREFERENCE)
        bass = _pick(context.session, BASS_PREFERENCE)
        if kick is None or bass is None:
            raise AudioCaptureError("TARGET_AMBIGUOUS", "need Kick and Bass")
        fire = _fire_from_session(context.session, kick.index, bass.index)

        t0 = time.perf_counter()
        tcp0 = daw.tcp_stats()
        inventory = inventory_taps(daw)
        proto = {
            "MASTER": None,
            "Copilot Capture": None,
            "Copilot Capture Bass": None,
        }
        for row in inventory:
            name = str(row.get("track_name") or "")
            if name in proto:
                proto[name] = row.get("tap_protocol")
            if int(row["track_index"]) == MASTER_INDEX:
                proto["MASTER"] = row.get("tap_protocol")
        runtime_proto = {k: v for k, v in proto.items() if v is not None}
        proto_ok = all(
            value == EXPECTED_TAP_PROTOCOL for value in runtime_proto.values()
        ) and len(runtime_proto) >= 3
        main_pos = master_tap_position(daw)
        main_final = bool(main_pos.get("is_last"))
        report["RUNTIME"] = {
            "tap_protocol": proto,
            "expected_tap_protocol": EXPECTED_TAP_PROTOCOL,
            "protocol_ok": proto_ok,
            "main_devices": main_pos.get("devices"),
            "main_claim": "MAIN_FINAL" if main_final else "MAIN_NOT_FINAL",
            "slots_before_claim": [
                {
                    "track": row.get("track_name"),
                    "slot": row.get("slot"),
                }
                for row in inventory
            ],
        }
        key = capability_key(
            live_version=detection.version,
            max_version=None,
            remote_script_protocol=str(
                (daw.handshake_info or {}).get("protocol_version") or "1"
            ),
            tap_protocol=EXPECTED_TAP_PROTOCOL if proto_ok else proto.get("MASTER"),
            sample_rate=context.sample_rate,
        )
        cache.bind(key)
        if proto_ok:
            cache.put(CERT_TAP_PROTOCOL_3, status="YES", evidence="runtime TapProtocol")
        if cache.certified(CERT_MULTI_TAP_ISOLATION):
            report["SKIPPED_CERTIFICATION"] = report.get("SKIPPED_CERTIFICATION") or []
            report["SKIPPED_CERTIFICATION"].append(
                cache.skip_reason(CERT_MULTI_TAP_ISOLATION)
            )
        if cache.certified(CERT_REC_CLOSE):
            report.setdefault("SKIPPED_CERTIFICATION", []).append(
                cache.skip_reason(CERT_REC_CLOSE)
            )
        if cache.certified(CERT_ALIGNMENT_ENVELOPE):
            report.setdefault("SKIPPED_CERTIFICATION", []).append(
                cache.skip_reason(CERT_ALIGNMENT_ENVELOPE)
            )
        if not proto_ok:
            report["STOP"] = (
                "TapProtocol runtime is not 3 on the three loaded instances. "
                "Re-drop devices/Copilot Audio Tap.amxd onto Main, Copilot Capture, "
                "and Copilot Capture Bass. Source-only is not enough."
            )
            persist()
            return report
        if not main_final:
            report["STOP"] = (
                "MAIN_FINAL required: Utility then Copilot Audio Tap last on Main. "
                "Typed move is not assumed. One manual reorder, then re-run."
            )
            persist()
            return report
        cache.put(CERT_MAIN_FINAL, status="FIXTURE_VERIFIED", evidence="device order")

        kick_host = ensure_capture_host_ready(
            daw,
            name=CAPTURE_HOST,
            target_name=kick.name,
            slot=1,
            inventory=inventory,
        )
        bass_host = ensure_capture_host_ready(
            daw,
            name=CAPTURE_BASS,
            target_name=bass.name,
            slot=2,
            inventory=inventory,
        )
        from copilot.audio.live_capture import set_tap_slot

        master_row = next(
            (row for row in inventory if int(row["track_index"]) == MASTER_INDEX),
            None,
        )
        if master_row is None or master_row.get("slot") != 0:
            set_tap_slot(
                daw,
                MASTER_INDEX,
                0,
                device_index=None if master_row is None else master_row.get("device_index"),
                parameter_index=(
                    None if master_row is None else master_row.get("slot_param_index")
                ),
            )
        inventory = inventory_taps(daw, use_cached_taps=False)
        assert_unique_slots(inventory)
        cache.put(
            CERT_SLOT_OWNERSHIP,
            status="CERTIFIED",
            evidence="unique slots after production claim 0/1/2",
        )
        routing_mutations = int(kick_host.get("routing_mutations") or 0) + int(
            bass_host.get("routing_mutations") or 0
        )
        kick_route = routing_claim(
            input_type=str(kick_host.get("input_type")),
            input_channel=str(kick_host.get("input_channel")),
            output=str(kick_host.get("output")),
            monitoring=str(kick_host.get("monitoring")),
            through_main=bool(kick_host.get("through_main")),
            sends=list(kick_host.get("sends") or []),
            target_name=kick.name,
        )
        bass_route = routing_claim(
            input_type=str(bass_host.get("input_type")),
            input_channel=str(bass_host.get("input_channel")),
            output=str(bass_host.get("output")),
            monitoring=str(bass_host.get("monitoring")),
            through_main=bool(bass_host.get("through_main")),
            sends=list(bass_host.get("sends") or []),
            target_name=bass.name,
        )
        cache.put(
            CERT_ROUTING_API,
            status="CERTIFIED" if kick_route.get("claim") == "OFF_MIX_GRAPH" else "LIMITED",
            details={"kick": kick_route, "bass": bass_route},
        )
        recorder_ids = set()
        for row in inventory:
            if int(row["track_index"]) in {
                MASTER_INDEX,
                int(kick_host["index"]),
                int(bass_host["index"]),
            }:
                recorder_ids.add(row["tap_instance_id"])
        stale = reconcile_stale_taps(daw, inventory, recorder_ids)
        mark(
            "tap_routing_validation",
            t0,
            tcp0,
            routing_mutations=routing_mutations,
            kick_claim=kick_route.get("claim"),
            bass_claim=bass_route.get("claim"),
            stale=stale,
        )
        report["HOSTS"] = {"kick": kick_host, "bass": bass_host}
        report["ROUTING"] = {"kick": kick_route, "bass": bass_route}
        persist()

        main_point = (
            SignalPoint.MAIN_FINAL
            if main_final
            else master_chain_signal_point(daw)
        )
        recs = _recorders(
            kick_index=int(kick_host["index"]),
            bass_index=int(bass_host["index"]),
            kick_id=kick.stable_id,
            bass_id=bass.stable_id,
            kick_channel=str(kick_host.get("input_channel")),
            bass_channel=str(bass_host.get("input_channel")),
            main_point=main_point,
        )
        for rec in recs:
            for row in inventory:
                if int(row["track_index"]) == int(rec["tap_track_index"]):
                    rec["device_index"] = row["device_index"]
                    rec["rec_param_index"] = row.get("rec_param_index")
                    break

        pass_id = uuid4().hex[:12]
        journal = CaptureJournal(pass_id)
        journal.record(
            PREPARED,
            inventory=inventory,
            region=REGION,
            start_beat=START_QN,
            end_beat=END_QN,
            revision=context.revision,
            mode=CaptureMode.PRODUCTION.value,
        )
        journal.record(RECORDING)
        _log("PRODUCTION PASS 1")
        t0 = time.perf_counter()
        tcp0 = daw.tcp_stats()
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
        timings = one.get("timings") or {}
        mark(
            "prepare_transport_record_finalize",
            t0,
            tcp0,
            timings={
                "prepare": timings.get("recorder_start_s"),
                "transport_pre_roll": timings.get("pre_roll_s"),
                "physical_record": timings.get("record_s"),
                "stop_close": timings.get("recorder_stop_s"),
                "finalize": timings.get("wav_finalize_s"),
                "restore": timings.get("restore_s"),
                "total_pass": timings.get("total_s"),
                "prepare_breakdown": timings.get("prepare_breakdown"),
                "pre_roll_breakdown": timings.get("pre_roll_breakdown"),
                "stop_breakdown": timings.get("stop_breakdown"),
                "fixed_sleeps_remaining": timings.get("fixed_sleeps_remaining"),
            },
        )
        journal.record(FINALIZING, timings=timings)
        assets = one["assets"]
        three = _sanity_three(assets["master"], assets["kick"], assets["bass"])
        hashes = {
            key_name: sha256_file(Path(asset.file_path))
            for key_name, asset in assets.items()
        }
        restore = one.get("restore") or {}
        t0 = time.perf_counter()
        tcp0 = daw.tcp_stats()
        if three["ok"] and restore.get("ok"):
            journal.record(
                VERIFIED,
                hashes=hashes,
                staging_release=timings.get("staging_release"),
            )
            claim = VERIFIED
        else:
            journal.record(FAILED, sanity=three["ok"], restore=restore)
            claim = FAILED
        mark("validation_journal_restore", t0, tcp0, journal=claim)

        total_s = time.perf_counter() - t_all
        if total_s < 15:
            grade = "EXCELLENT"
        elif total_s < 20:
            grade = "GOOD"
        elif total_s < 30:
            grade = "ACCEPTABLE"
        else:
            grade = "ABOVE_TARGET"
        report["PASS 1"] = {
            "ok": three["ok"] and bool(restore.get("ok")),
            "pass_id": pass_id,
            "elapsed_s": timings.get("total_s"),
            "wavs": {k: v.file_path for k, v in assets.items()},
            "cards": {k: _asset_card(v) for k, v in assets.items()},
            "hashes": hashes,
            "content": three.get("gates"),
            "timings": timings,
            "restore": restore,
        }
        report["CAPTURE JOURNAL STATUS"] = {
            "path": str(journal.path),
            "last": journal.last_status(),
            "claim": journal.claim_verified(),
        }
        report["PRODUCTION_PASS_TIME"] = total_s
        report["CERTIFICATION_SUITE_TIME"] = None
        report["GRADE"] = grade
        report["TCP"] = daw.tcp_stats()
        report["ROUTING_MUTATIONS"] = routing_mutations
        report["TRANSPORT_MUTATIONS"] = (restore or {}).get("transport_mutations")
        report["FIXED_SLEEPS_REMAINING"] = timings.get("fixed_sleeps_remaining")
        report["BATCH"] = {
            "set_device_parameters": int(daw.tcp_counts.get("set_device_parameters", 0)),
            "fire_clips": int(daw.tcp_counts.get("fire_clips", 0)),
            "stop_clips": int(daw.tcp_counts.get("stop_clips", 0)),
            "set_device_parameter": int(daw.tcp_counts.get("set_device_parameter", 0)),
            "fire_clip": int(daw.tcp_counts.get("fire_clip", 0)),
            "stop_clip": int(daw.tcp_counts.get("stop_clip", 0)),
        }
        report["REGRESSION"] = {
            "BEFORE_S": BEFORE_PASS1_S,
            "AFTER_S": total_s,
            "delta_s": total_s - BEFORE_PASS1_S,
            "same_region": True,
            "same_three_views": True,
        }
        persist()
        return report
    except (AudioCaptureError, DawError) as exc:
        report["error"] = str(exc)
        report["PRODUCTION_PASS_TIME"] = time.perf_counter() - t_all
        report["TCP"] = daw.tcp_stats()
        persist()
        return report
    finally:
        report["elapsed_s"] = time.perf_counter() - t_all
        report["TCP"] = daw.tcp_stats()
        persist()
