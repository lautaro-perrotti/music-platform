from __future__ import annotations

import argparse
import json
from pathlib import Path

from copilot.agent.journal import DurableJournal
from copilot.agent.slices import (
    connect_live,
    connect_mock,
    run_create_c3_clip,
    run_undo_last,
)
from copilot.agent.tools import AgentTools
from copilot.agent.transactions import TransactionManager
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.adapter import DawError
from copilot.daw.detect import detect_ableton, live_block_status, write_detection
from copilot.daw.session_ready_v1 import (
    SESSION_READY,
    probe_session_ready,
    revalidate_project,
)
from copilot.audio.live2 import run_live2
from copilot.audio.live21 import run_live21
from copilot.audio.live22 import run_live22
from copilot.audio.live22e import run_live22e
from copilot.audio.live3 import run_live3
from copilot.audio.live3r import run_live3r
from copilot.audio.live3r_perf import run_live3r_perf
from copilot.audio.live3r_perf2 import run_live3r_perf2
from copilot.audio.live3r_perf3 import run_live3r_perf3
from copilot.audio.live3r_trust import run_live3r_trust
from copilot.audio.live3r_prod import run_live3r_prod
from copilot.audio.capture_alignment import run_alignment_certification
from copilot.audio.live_state import run_live_state
from copilot.daw.install_remote_script import install_remote_script
from copilot.logging_setup import configure_logging

# Production routing contracts (MusicPlan behavior not implemented here).
CANONICAL_PRE_WRITE_PATH = (
    "session-diagnose → session-run1 → session-run1-fullmix → "
    "session-run1-astra-r2 → session-run1-causal-trace → "
    "session-run1-source-audio → session-run1-strum-device"
)
CANONICAL_WRITE_PATH = "production-write"
LAB_COMMANDS = frozenset(
    {
        "mock-slice1",
        "live2",
        "live21",
        "live22",
        "live22e",
        "live3",
        "live3r",
        "live3r-perf",
        "live3r-perf2",
        "live3r-perf3",
        "live3r-trust",
        "live3r-prod",
        "live3r-align",
    }
)
CANONICAL_COMMANDS = (
    "install",
    "doctor",
    "onboard-project",
    "project-ready",
    "project-bootstrap",
    "producer-analyze",
    "producer-run",
    "cross-project-validate",
    "import-project",
    "sample-library",
    "regression-v1",
    "capabilities",
)
HELP_EPILOG = """
Canonical supported envelope:
  install                  (Windows or macOS local runtime; no musical writes)
  import-project "<folder>"
  doctor
  onboard-project          (alias of project-ready)
  project-bootstrap
  producer-analyze
  producer-run --mode analyze|autonomous
  cross-project-validate
  regression-v1
  capabilities

Lab runners require --lab and are not the supported envelope.
Live is ready only after SESSION_READY (not a listening port).
""".strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI Music Production Copilot — supported envelope CLI",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        choices=[
            "detect",
            "install-script",
            "probe",
            "slice1",
            "undo",
            "mock-slice1",
            "live2",
            "live21",
            "live22",
            "live22e",
            "live3",
            "live3r",
            "live3r-perf",
            "live3r-perf2",
            "live3r-perf3",
            "live3r-trust",
            "live3r-prod",
            "live3r-align",
            "live-state",
            "reason-eval",
            "reason-real",
            "reason-revalidate",
            "session-diagnose",
            "session-run1",
            "session-run1-astra",
            "session-run1-fullmix",
            "session-run1-astra-r2",
            "session-run1-grounding-causal",
            "session-run1-causal-trace",
            "session-run1-source-audio",
            "session-run1-strum-device",
            "session-seek-trust",
            "session-arrangement-start",
            "human-eval",
            "capture-journal-recover",
            "production-write",
            "project-bootstrap",
            "project-ready",
            "onboard-project",
            "producer-analyze",
            "producer-run",
            "cross-project-validate",
            "downstream-causal-state",
            "sidechain-automation-state",
            "import-project",
            "sample-library",
            "track-build",
            "vibe",
            "install",
            "uninstall-copilot",
            "doctor",
            "regression-v1",
            "capabilities",
        ],
    )
    parser.add_argument("eval_argv", nargs="*", default=[])
    parser.add_argument("--track", default="AI Test")
    parser.add_argument("--log", default="logs/copilot.log")
    parser.add_argument(
        "--gate",
        choices=["smoke", "mini", "full"],
        default="smoke",
        help="reason-real only: smoke=1 call, mini=A-F x1, full=A-F x5",
    )
    parser.add_argument("--run", dest="eval_run", default=None)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument(
        "--lab",
        action="store_true",
        help="Required for legacy/lab capture runners (live3r*, mock-slice1, live2*).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="track-build/vibe: run against the real Ableton TCP bridge instead of the mock.",
    )
    parser.add_argument(
        "--leave",
        action="store_true",
        help="track-build/vibe: skip the rollback and LEAVE the built track in the set.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="production-write: validate/compile only; ZERO musical mutations.",
    )
    parser.add_argument(
        "--plan",
        default=None,
        help="production-write: path to MusicPlan JSON.",
    )
    parser.add_argument(
        "--prepare-controlled-plan",
        action="store_true",
        help="production-write: build CONTROLLED_ENGINEERING_VALIDATION volume plan + dry-run.",
    )
    parser.add_argument(
        "--delta",
        type=float,
        default=-0.02,
        help="controlled plan volume delta in ableton_volume units (default -0.02).",
    )
    parser.add_argument(
        "--target-track",
        default=None,
        help="controlled plan target track name (default: first non-capture audio/midi track).",
    )
    parser.add_argument(
        "--mode",
        choices=["analyze", "autonomous"],
        default=None,
        help="producer-run: analyze (read-only) or autonomous.",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="producer-analyze / producer-run: optional region id.",
    )
    parser.add_argument(
        "--start-qn",
        type=float,
        default=None,
        help="producer-analyze / producer-run: explicit region start.",
    )
    parser.add_argument(
        "--end-qn",
        type=float,
        default=None,
        help="producer-analyze / producer-run: explicit region end.",
    )
    parser.add_argument(
        "--remove-venv",
        action="store_true",
        help="uninstall-copilot: also delete repo .venv",
    )
    parser.add_argument(
        "--remove-config",
        action="store_true",
        help="uninstall-copilot: also delete repo .env",
    )
    parser.add_argument(
        "--execute-controlled-write",
        action="store_true",
        help=(
            "production-write: run CONTROLLED_ENGINEERING_VALIDATION write+rollback loop. "
            "Not autonomous musical improvement."
        ),
    )
    parser.add_argument(
        "--source-plan",
        default="plan_82608749d172",
        help="source dry-run plan_id to link (immutable; not executed).",
    )
    parser.add_argument(
        "--source-envelope",
        default="env_5886f4fb0b",
        help="source dry-run envelope_id to link (immutable; not executed).",
    )
    parser.add_argument(
        "--expected-before",
        type=float,
        default=0.75,
        help="hard precondition: live target volume must match before write.",
    )
    parser.add_argument(
        "--audible-effect-verification",
        action="store_true",
        help=(
            "production-write: AUDIBLE_EFFECT_VERIFICATION_V1 "
            "(capture BEFORE/AFTER around controlled volume write + rollback)."
        ),
    )
    parser.add_argument(
        "--audible-effect-verification-v2",
        action="store_true",
        help=(
            "production-write: AUDIBLE_EFFECT_VERIFICATION_V2 "
            "(baseline-characterize → freeze high-SNR SET_TRACK_VOLUME → one write)."
        ),
    )
    parser.add_argument(
        "--first-autonomous-musical-improvement",
        action="store_true",
        help=(
            "production-write: FIRST_AUTONOMOUS_MUSICAL_IMPROVEMENT_V1 "
            "(one blind holdout observe→diagnose→gate→optional SET_TRACK_VOLUME)."
        ),
    )
    parser.add_argument(
        "--autonomous-musical-evaluation-v2",
        action="store_true",
        help=(
            "production-write: AUTONOMOUS_MUSICAL_EVALUATION_V2 "
            "(preselected HOLDOUT_320_352 blind evaluation)."
        ),
    )
    parser.add_argument(
        "--autonomous-musical-evaluation-v3",
        action="store_true",
        help=(
            "production-write: AUTONOMOUS_MUSICAL_EVALUATION_V3 "
            "(new independent holdout + arrangement-active source isolation)."
        ),
    )
    parser.add_argument(
        "--arrangement-active-source-isolation",
        action="store_true",
        help=(
            "production-write: ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1 "
            "(Post Mixer views for arrangement-active sources; observation only)."
        ),
    )
    args = parser.parse_args(argv)
    logger = configure_logging(Path(args.log))
    evidence = Path("logs")
    evidence.mkdir(parents=True, exist_ok=True)

    if args.command in LAB_COMMANDS and not args.lab:
        print(
            json.dumps(
                {
                    "status": "LAB_FLAG_REQUIRED",
                    "command": args.command,
                    "detail": (
                        "Legacy/lab runners require --lab. "
                        f"CANONICAL_PRE_WRITE_PATH={CANONICAL_PRE_WRITE_PATH}; "
                        f"CANONICAL_WRITE_PATH={CANONICAL_WRITE_PATH}"
                    ),
                    "MUSICAL WRITES": 0,
                },
                indent=2,
            )
        )
        return 2

    if args.command == "production-write":
        return _production_write(
            evidence,
            logger,
            dry_run=bool(args.dry_run),
            plan_path=args.plan,
            prepare_controlled=bool(args.prepare_controlled_plan),
            execute_controlled=bool(args.execute_controlled_write),
            audible_effect=bool(args.audible_effect_verification),
            audible_effect_v2=bool(args.audible_effect_verification_v2),
            autonomous_improvement=bool(args.first_autonomous_musical_improvement),
            autonomous_evaluation_v2=bool(args.autonomous_musical_evaluation_v2),
            autonomous_evaluation_v3=bool(args.autonomous_musical_evaluation_v3),
            arrangement_source_isolation=bool(args.arrangement_active_source_isolation),
            delta=float(args.delta),
            target_track=args.target_track,
            source_plan_id=str(args.source_plan),
            source_envelope_id=str(args.source_envelope),
            expected_before=float(args.expected_before),
        )

    if args.command == "project-bootstrap":
        return _project_bootstrap(evidence, logger)
    if args.command in {"project-ready", "onboard-project"}:
        return _project_ready(evidence, logger)
    if args.command == "producer-analyze":
        return _producer_analyze(
            evidence,
            logger,
            region_id=args.region,
            start_qn=args.start_qn,
            end_qn=args.end_qn,
        )
    if args.command == "producer-run":
        return _producer_run(
            evidence,
            logger,
            mode=args.mode,
            region_id=args.region,
            start_qn=args.start_qn,
            end_qn=args.end_qn,
        )
    if args.command == "cross-project-validate":
        return _cross_project_validate(evidence, logger)
    if args.command == "downstream-causal-state":
        return _downstream_causal_state(
            evidence, logger,
            region_id=args.region or "DOWNSTREAM",
            start_qn=args.start_qn,
            end_qn=args.end_qn,
            source_names=args.eval_argv or None,
        )

    if args.command == "sidechain-automation-state":
        return _sidechain_automation_state(
            evidence, logger,
            region_id=args.region or "SIDECHAIN",
            start_qn=args.start_qn,
            end_qn=args.end_qn,
            source_names=args.eval_argv or None,
        )
    if args.command == "import-project":
        return _import_project(evidence, logger, args.eval_argv)
    if args.command == "sample-library":
        return _sample_library(evidence, logger, args.eval_argv)
    if args.command == "track-build":
        return _track_build(evidence, logger, args.eval_argv, live=bool(args.live), leave=bool(args.leave))
    if args.command == "vibe":
        return _vibe(evidence, logger, args.eval_argv, live=bool(args.live), leave=bool(args.leave))
    if args.command == "install":
        from copilot.installing.second_machine_installer_v1 import run_installer

        report = run_installer(evidence=evidence)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        logger.info("install status=%s remote=%s m4l=%s", report.get("status"), report.get("REMOTE_SCRIPT"), report.get("M4L RUNTIME"))
        return 0 if report.get("status") == "VERIFIED" else 2
    if args.command == "uninstall-copilot":
        from copilot.installing.second_machine_installer_v1 import uninstall_copilot_owned

        report = uninstall_copilot_owned(
            remove_venv=bool(args.remove_venv),
            remove_config=bool(args.remove_config),
        )
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0 if report.get("status") == "VERIFIED" else 2
    if args.command == "doctor":
        return _doctor(evidence, logger)
    if args.command == "regression-v1":
        return _regression_v1(evidence, logger)
    if args.command == "capabilities":
        return _capabilities()

    if args.command == "capture-journal-recover":
        return _capture_journal_recover(evidence, logger)

    if args.command == "detect":
        detection = detect_ableton()
        write_detection(evidence / "ableton_detect.json", detection)
        print(json.dumps(detection.to_dict(), indent=2))
        return 0 if detection.found else 2

    if args.command == "install-script":
        result = install_remote_script()
        (evidence / "remote_script_install.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in {"INSTALLED", "ALREADY_CURRENT", "UPDATED"} else 2

    if args.command == "probe":
        return _probe(evidence, logger)

    if args.command == "live2":
        return _live2(evidence, logger)

    if args.command == "live21":
        return _live21(evidence, logger)

    if args.command == "live22":
        return _live22(evidence, logger)

    if args.command == "live22e":
        return _live22e(evidence, logger)

    if args.command == "live3":
        return _live3(evidence, logger)

    if args.command == "live3r":
        return _live3r(evidence, logger)

    if args.command == "live3r-perf":
        return _live3r_perf(evidence, logger)

    if args.command == "live3r-perf2":
        return _live3r_perf2(evidence, logger)

    if args.command == "live3r-perf3":
        return _live3r_perf3(evidence, logger)

    if args.command == "live3r-trust":
        return _live3r_trust(evidence, logger)

    if args.command == "live3r-prod":
        return _live3r_prod(evidence, logger)

    if args.command == "live3r-align":
        return _live3r_align(evidence, logger)

    if args.command == "live-state":
        return _live_state(evidence, logger)

    if args.command == "reason-eval":
        return _reason_eval(evidence, logger)

    if args.command == "reason-real":
        return _reason_real(evidence, logger, gate=args.gate)

    if args.command == "reason-revalidate":
        return _reason_revalidate(evidence, logger)

    if args.command == "session-diagnose":
        return _session_diagnose(evidence, logger)

    if args.command == "session-run1":
        return _session_run1(evidence, logger)

    if args.command == "session-run1-astra":
        return _session_run1_astra(evidence, logger)

    if args.command == "session-run1-fullmix":
        return _session_run1_fullmix(evidence, logger)

    if args.command == "session-run1-astra-r2":
        return _session_run1_astra_r2(evidence, logger)
    if args.command == "session-run1-grounding-causal":
        return _session_run1_grounding_causal(evidence, logger)
    if args.command == "session-run1-causal-trace":
        return _session_run1_causal_trace(evidence, logger)
    if args.command == "session-run1-source-audio":
        return _session_run1_source_audio(evidence, logger)
    if args.command == "session-run1-strum-device":
        return _session_run1_strum_device(evidence, logger)

    if args.command == "session-seek-trust":
        return _session_seek_trust(evidence, logger)

    if args.command == "session-arrangement-start":
        return _session_arrangement_start(evidence, logger)

    if args.command == "human-eval":
        from copilot.human_eval.cli import handle_human_eval

        return handle_human_eval(args, evidence)

    if args.command == "mock-slice1":
        daw = connect_mock()
        tools = AgentTools(daw, TransactionManager(daw))
        result = run_create_c3_clip(tools, args.track)
        result["verification_class"] = "MOCK_VERIFIED"
        result["backend"] = "mock"
        print(json.dumps(result, indent=2, default=str))
        return 0

    try:
        daw = connect_live()
    except DawError as exc:
        detection = detect_ableton()
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "status": status,
            "error": str(exc),
            "required_action": _manual_control_surface_action()
            if status == "MANUAL_CONFIGURATION_REQUIRED"
            else None,
        }
        print(json.dumps(payload, indent=2))
        return 2
    store = evidence / "agent_transactions.json"
    journal = DurableJournal(evidence / "agent_journal.jsonl")
    txns = TransactionManager(daw, journal=journal)
    txns.load(store)
    recovery = txns.recovery_scan()
    if recovery:
        logger.info("journal recovery scan: %s", recovery)
    tools = AgentTools(daw, txns)
    logger.info("backend=ableton-tcp verification_class=LIVE")
    if args.command == "slice1":
        result = run_create_c3_clip(tools, args.track)
        result["verification_class"] = "LIVE_VERIFIED"
    else:
        result = run_undo_last(tools, args.track)
        result["verification_class"] = "LIVE_VERIFIED"
    txns.save(store)
    result["backend"] = "ableton-tcp"
    (evidence / f"live_{args.command}.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def _live3r(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live3r(adapter, evidence)
        adapter.disconnect()
        print(json.dumps(
            {
                "phase": "LIVE-3R",
                "REAL_SESSION_LOW_END_DIAGNOSIS": result.get(
                    "REAL_SESSION_LOW_END_DIAGNOSIS"
                ),
                "LOW_END_DIAGNOSIS_FIXTURE_BASELINE": result.get(
                    "LOW_END_DIAGNOSIS_FIXTURE_BASELINE"
                ),
                "PRODUCTION_MUSIC_DIAGNOSIS": result.get("PRODUCTION_MUSIC_DIAGNOSIS"),
                "regions": {
                    key: {
                        "finding_types": (result.get(key) or {}).get("finding_types"),
                        "confidence": (result.get(key) or {}).get("confidence"),
                    }
                    for key in ("REGION_A", "REGION_B", "REGION_C")
                },
            },
            indent=2,
            default=str,
        ))
        return 0
    except Exception as exc:
        status = live_block_status(detection)
        payload = {"verification_class": status, "phase": "LIVE-3R", "error": str(exc)}
        (evidence / "live3r_reality_check.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("LIVE-3R failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live3r_perf(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live3r_perf(adapter, evidence)
        adapter.disconnect()
        print(
            json.dumps(
                {
                    "phase": result.get("phase"),
                    "region": result.get("region"),
                    "TOTAL": result.get("TOTAL"),
                    "CAPTURE": result.get("CAPTURE"),
                    "OVERHEAD": result.get("OVERHEAD"),
                    "DSP": result.get("DSP"),
                    "performance_gate": result.get("performance_gate"),
                    "DIAGNOSIS_RESULT_UNCHANGED": result.get(
                        "DIAGNOSIS_RESULT_UNCHANGED"
                    ),
                    "CAPTURE_PROVENANCE_INTACT": result.get(
                        "CAPTURE_PROVENANCE_INTACT"
                    ),
                    "STATE_RESTORE_INTACT": result.get("STATE_RESTORE_INTACT"),
                    "tcp_total": (result.get("tcp") or {}).get("total"),
                    "get_track_info": (result.get("tcp") or {}).get("get_track_info"),
                    "full_session_snapshots": result.get("full_session_snapshots"),
                    "top_3_remaining_bottlenecks": result.get(
                        "top_3_remaining_bottlenecks"
                    ),
                    "error": result.get("error"),
                },
                indent=2,
                default=str,
            )
        )
        if result.get("error"):
            return 2
        return 0
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "phase": "PERFORMANCE CHECK",
            "error": str(exc),
        }
        (evidence / "live3r_perf.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("LIVE-3R perf failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live3r_perf2(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live3r_perf2(adapter, evidence)
        adapter.disconnect()
        print(
            json.dumps(
                {
                    "phase": result.get("phase"),
                    "TOTAL": result.get("TOTAL"),
                    "PASS COUNT": result.get("PASS COUNT"),
                    "performance_gate": result.get("performance_gate"),
                    "DIAGNOSIS_SAME_WAV_DETERMINISTIC": result.get(
                        "DIAGNOSIS_SAME_WAV_DETERMINISTIC"
                    ),
                    "CAPTURE_TO_CAPTURE_STABILITY": result.get(
                        "CAPTURE_TO_CAPTURE_STABILITY"
                    ),
                    "MINIMAL_EXPERIMENT": result.get("MINIMAL_EXPERIMENT"),
                    "probe_gates": (result.get("capture_track_probe") or {}).get(
                        "gates"
                    ),
                    "probe_host": (result.get("capture_track_probe") or {}).get("host"),
                    "probe_content": (result.get("capture_track_probe") or {}).get(
                        "content"
                    ),
                    "CAPTURE_PROVENANCE_INTACT": result.get(
                        "CAPTURE_PROVENANCE_INTACT"
                    ),
                    "STATE_RESTORE_INTACT": result.get("STATE_RESTORE_INTACT"),
                    "kick_signal_point": result.get("kick_signal_point"),
                    "finding_types": result.get("finding_types"),
                    "tcp_total": (result.get("tcp") or {}).get("total"),
                    "routing_mutations": result.get("routing_mutations"),
                    "STOP": result.get("STOP"),
                    "error": result.get("error"),
                },
                indent=2,
                default=str,
            )
        )
        if result.get("error") or result.get("STOP"):
            return 2 if result.get("error") else 0
        return 0
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "phase": "PERF-2",
            "error": str(exc),
        }
        (evidence / "live3r_perf2.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("PERF-2 failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live3r_perf3(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live3r_perf3(adapter, evidence)
        adapter.disconnect()
        print(
            json.dumps(
                {
                    "phase": result.get("phase"),
                    "SLOT-ENABLED TAP": result.get("SLOT-ENABLED TAP"),
                    "RUNTIME PROTOCOL": result.get("RUNTIME PROTOCOL"),
                    "slot_roundtrip": result.get("slot_roundtrip"),
                    "capture_sends_all_silent": result.get(
                        "capture_sends_all_silent"
                    ),
                    "MULTI-TAP FILE ISOLATION": result.get("MULTI-TAP FILE ISOLATION"),
                    "PARALLEL PASS 1": result.get("PARALLEL PASS 1"),
                    "OFF-MAIN TRACK CAPTURE": result.get("OFF-MAIN TRACK CAPTURE"),
                    "UPDATED TAP PARAMETERS": result.get("UPDATED TAP PARAMETERS"),
                    "TWO-TAP RESULT": {
                        "ok": (result.get("TWO-TAP RESULT") or {}).get("ok"),
                        "pass_id": (result.get("TWO-TAP RESULT") or {}).get("pass_id"),
                    },
                    "PASS 1 RESULT": {
                        "ok": (result.get("PASS 1 RESULT") or {}).get("ok"),
                        "pass_id": (result.get("PASS 1 RESULT") or {}).get("pass_id"),
                    },
                    "SEMANTICS": result.get("SEMANTICS"),
                    "TOTAL TIME": result.get("TOTAL TIME") or result.get("TOTAL"),
                    "TOP REMAINING BOTTLENECK": result.get("TOP REMAINING BOTTLENECK"),
                    "routing_mutations": result.get("routing_mutations"),
                    "tcp_total": (result.get("tcp") or {}).get("total"),
                    "STOP": result.get("STOP"),
                    "error": result.get("error"),
                },
                indent=2,
                default=str,
            )
        )
        if result.get("error"):
            return 2
        return 0
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "phase": "PERF-3",
            "error": str(exc),
        }
        (evidence / "live3r_perf3.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("PERF-3 failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live3r_trust(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live3r_trust(adapter, evidence)
        adapter.disconnect()
        print(
            json.dumps(
                {
                    "phase": result.get("phase"),
                    "TAP INVENTORY": {
                        "count": (result.get("TAP INVENTORY") or {}).get("count")
                    },
                    "SLOT COLLISION TEST": result.get("SLOT COLLISION TEST"),
                    "UDP CROSS-TALK TEST": {
                        "instance_control": (
                            result.get("UDP CROSS-TALK TEST") or {}
                        ).get("instance_control"),
                        "lom_ok": ((result.get("UDP CROSS-TALK TEST") or {}).get("lom") or {}).get(
                            "ok"
                        ),
                        "udp_ok": ((result.get("UDP CROSS-TALK TEST") or {}).get("udp") or {}).get(
                            "ok"
                        ),
                    },
                    "FILE OWNERSHIP TEST": result.get("FILE OWNERSHIP TEST"),
                    "MAIN SIGNAL POINT": (result.get("MAIN SIGNAL POINT") or {}).get(
                        "claim"
                    ),
                    "KICK/BASS ROUTING SEMANTICS": {
                        "kick": ((result.get("KICK/BASS ROUTING SEMANTICS") or {}).get("kick") or {}).get(
                            "claim"
                        ),
                        "bass": ((result.get("KICK/BASS ROUTING SEMANTICS") or {}).get("bass") or {}).get(
                            "claim"
                        ),
                    },
                    "PASS 1": {
                        "ok": (result.get("PASS 1") or {}).get("ok"),
                        "pass_id": (result.get("PASS 1") or {}).get("pass_id"),
                    },
                    "5x REPEATABILITY": (result.get("5x REPEATABILITY") or {}).get(
                        "claim"
                    ),
                    "ALIGNMENT RESULT": (result.get("ALIGNMENT RESULT") or {}).get(
                        "claim"
                    ),
                    "CAPTURE JOURNAL STATUS": result.get("CAPTURE JOURNAL STATUS"),
                    "STATE RESTORE": (result.get("STATE RESTORE") or {}).get("ok"),
                    "PERFORMANCE": result.get("PERFORMANCE"),
                    "STOP": result.get("STOP"),
                    "error": result.get("error"),
                },
                indent=2,
                default=str,
            )
        )
        if result.get("error"):
            return 2
        return 0
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "phase": "MULTI-TAP TRUST",
            "error": str(exc),
        }
        (evidence / "live3r_trust.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("TRUST failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live3r_prod(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live3r_prod(adapter, evidence)
        adapter.disconnect()
        print(
            json.dumps(
                {
                    "phase": result.get("phase"),
                    "mode": result.get("mode"),
                    "RUNTIME": result.get("RUNTIME"),
                    "STOP": result.get("STOP"),
                    "PRODUCTION_PASS_TIME": result.get("PRODUCTION_PASS_TIME"),
                    "CERTIFICATION_SUITE_TIME": result.get("CERTIFICATION_SUITE_TIME"),
                    "GRADE": result.get("GRADE"),
                    "BENCHMARK": result.get("BENCHMARK"),
                    "TCP": result.get("TCP"),
                    "ROUTING_MUTATIONS": result.get("ROUTING_MUTATIONS"),
                    "TRANSPORT_MUTATIONS": result.get("TRANSPORT_MUTATIONS"),
                    "FIXED_SLEEPS_REMAINING": result.get("FIXED_SLEEPS_REMAINING"),
                    "BATCH": result.get("BATCH"),
                    "REGRESSION": result.get("REGRESSION"),
                    "CAPTURE JOURNAL STATUS": result.get("CAPTURE JOURNAL STATUS"),
                    "PASS 1": {
                        "ok": (result.get("PASS 1") or {}).get("ok"),
                        "pass_id": (result.get("PASS 1") or {}).get("pass_id"),
                    },
                    "error": result.get("error"),
                },
                indent=2,
                default=str,
            )
        )
        if result.get("error") or result.get("STOP"):
            return 2
        return 0
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "phase": "PRODUCTION CAPTURE",
            "error": str(exc),
        }
        (evidence / "live3r_prod.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("PRODUCTION capture failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live3r_align(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_alignment_certification(adapter, evidence)
        adapter.disconnect()
        (evidence / "live3r_align.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "phase": result.get("phase"),
                    "STOP": result.get("STOP"),
                    "ALIGNMENT_ENVELOPE": {
                        "claim": (result.get("ALIGNMENT_ENVELOPE") or {}).get("claim"),
                        "envelope": (result.get("ALIGNMENT_ENVELOPE") or {}).get(
                            "envelope"
                        ),
                        "sample_accurate": (result.get("ALIGNMENT_ENVELOPE") or {}).get(
                            "sample_accurate"
                        ),
                        "max_abs_error_ms": (result.get("ALIGNMENT_ENVELOPE") or {}).get(
                            "max_abs_error_ms"
                        ),
                    },
                    "capability_cache": result.get("capability_cache"),
                    "routing_restored": result.get("routing_restored"),
                },
                indent=2,
                default=str,
            )
        )
        envelope = result.get("ALIGNMENT_ENVELOPE") or {}
        if result.get("STOP") or envelope.get("claim") == "FAILED":
            return 2
        return 0
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "phase": "ALIGNMENT CERTIFICATION",
            "error": str(exc),
        }
        (evidence / "live3r_align.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        logger.error("ALIGN failed: %s", exc)
        print(json.dumps(payload, indent=2, default=str))
        return 2


def _live_state(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live_state(adapter, evidence)
        adapter.disconnect()
        (evidence / "live_state.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "phase": result.get("phase"),
                    "ok": result.get("ok"),
                    "STOP": result.get("STOP"),
                    "ACCEPT": result.get("ACCEPT"),
                    "PROJECT": {
                        "identity_kind": (result.get("PROJECT") or {}).get(
                            "identity_kind"
                        ),
                        "path": (result.get("PROJECT") or {}).get("path"),
                    },
                    "PERF": result.get("PERF"),
                    "RECONNECT": result.get("RECONNECT"),
                },
                indent=2,
                default=str,
            )
        )
        if result.get("STOP") or not result.get("ok"):
            return 2
        return 0
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "phase": "SESSION STATE TRUST",
            "error": str(exc),
        }
        (evidence / "live_state.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        logger.error("STATE TRUST failed: %s", exc)
        print(json.dumps(payload, indent=2, default=str))
        return 2


def _live3(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live3(adapter, evidence)
        adapter.disconnect()
        print(json.dumps(result, indent=2, default=str))
        ok = result.get("REAL LOW-END AUDIO") == "VERIFIED" and result.get(
            "STRUCTURED EVIDENCE"
        ) == "VERIFIED"
        return 0 if ok else 2
    except Exception as exc:
        status = live_block_status(detection)
        payload = {"verification_class": status, "phase": "LIVE-3", "error": str(exc)}
        (evidence / "live3_lowend_diagnosis.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("LIVE-3 failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live22e(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live22e(adapter, evidence)
        adapter.disconnect()
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("SILENT BOUNDARY") != "FAILED" else 2
    except Exception as exc:
        status = live_block_status(detection)
        payload = {"verification_class": status, "phase": "LIVE-2.2e", "error": str(exc)}
        (evidence / "live22e_five_fixtures.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("LIVE-2.2e failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live22(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live22(adapter, evidence)
        adapter.disconnect()
        print(json.dumps(result, indent=2, default=str))
        failed = [
            key
            for key, value in result.items()
            if value == "FAILED"
            and key
            in {
                "CAPTURE SEMANTICS",
                "REGION ALIGNMENT",
                "TRACK ISOLATION",
                "SUPPORTED ENVELOPE",
            }
        ]
        return 0 if not failed else 2
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "phase": "LIVE-2.2",
            "error": str(exc),
        }
        (evidence / "live22_capture_semantics.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("LIVE-2.2 failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live21(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live21(adapter, evidence)
        adapter.disconnect()
        print(json.dumps(result, indent=2, default=str))
        ok = (
            result.get("MASTER PRECISE CAPTURE") == "VERIFIED"
            and result.get("TRACK PRECISE CAPTURE") == "VERIFIED"
            and result.get("MUSIC OBSERVATION") == "VERIFIED"
        )
        return 0 if ok else 2
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "MASTER PRECISE CAPTURE": "NOT_VERIFIED",
            "TRACK PRECISE CAPTURE": "NOT_VERIFIED",
            "error": str(exc),
        }
        (evidence / "live21_precise_capture.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("LIVE-2.1 failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _live2(evidence: Path, logger) -> int:
    detection = detect_ableton()
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        result = run_live2(adapter, evidence)
        adapter.disconnect()
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("AUDIO CAPTURE") == "VERIFIED" else 2
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "AUDIO CAPTURE": "NOT_VERIFIED",
            "error": str(exc),
            "required_action": _manual_control_surface_action()
            if status == "MANUAL_CONFIGURATION_REQUIRED"
            else None,
        }
        (evidence / "live2_audio_capture.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("LIVE-2 failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _probe(evidence: Path, logger) -> int:
    detection = detect_ableton()
    write_detection(evidence / "ableton_detect.json", detection)
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        if (adapter.handshake_info or {}).get("backend") == "mock":
            raise DawError("BLOCKED_BY_ENVIRONMENT: mock TCP backend is not Live")
        result = adapter.probe()
        adapter.disconnect()
        (evidence / "live_probe.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        status = live_block_status(detection)
        payload = {
            "verification_class": status,
            "status": status,
            "error": str(exc),
            "required_action": _manual_control_surface_action()
            if status == "MANUAL_CONFIGURATION_REQUIRED"
            else None,
            "detection": detection.to_dict(),
        }
        (evidence / "live_probe.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.error("LIVE probe failed: %s", exc)
        print(json.dumps(payload, indent=2))
        return 2


def _session_diagnose(evidence: Path, logger) -> int:
    from copilot.audio.session_diagnose import preflight_session, write_preflight
    from copilot.daw.ableton_tcp import AbletonTcpAdapter

    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        report = preflight_session(adapter)
    finally:
        adapter.disconnect()
    path = write_preflight(report, evidence)
    logger.info(
        "session-diagnose preflight pass=%s missing=%s project=%s",
        report.get("pass"),
        report.get("missing"),
        report.get("project_name"),
    )
    print(json.dumps(
        {
            "pass": report.get("pass"),
            "status": report.get("status"),
            "missing": report.get("missing"),
            "target_source_unsupported": report.get("target_source_unsupported"),
            "kick_source_class": report.get("kick_source_class"),
            "kick_source": report.get("kick_source"),
            "bass_source": report.get("bass_source"),
            "project_ok": report.get("project_ok"),
            "working_copy": report.get("working_copy"),
            "original_open": report.get("original_open"),
            "live_set_path": report.get("live_set_path") or report.get("project_path"),
            "project_name": report.get("project_name"),
            "project_path": report.get("project_path"),
            "project_token": report.get("project_token"),
            "audible_token": report.get("audible_token"),
            "revision": report.get("revision"),
            "drums": report.get("drums"),
            "bass": report.get("bass"),
            "main": report.get("main"),
            "taps": report.get("taps"),
            "slot_collisions": report.get("slot_collisions"),
            "capture_hosts": report.get("capture_hosts"),
            "remote_script": report.get("remote_script"),
            "playing": report.get("playing"),
            "instruction": report.get("instruction"),
            "model_calls": report.get("model_calls"),
            "artifact": str(path),
        },
        indent=2,
        ensure_ascii=False,
    ))
    if not report.get("pass"):
        return 2
    return 0


def _session_run1_astra(evidence: Path, logger) -> int:
    from copilot.reasoning.session_astra import run_session_astra

    report = run_session_astra(
        evidence=evidence,
        source_run="session_run1",
        include_fullmix=False,
        diagnosis_revision=1,
    )
    return _print_astra_public(report, logger)


def _session_run1_fullmix(evidence: Path, logger) -> int:
    from copilot.audio.fullmix import run_session_fullmix

    report = run_session_fullmix(evidence=evidence, source_run="session_run1")
    logger.info(
        "session-run1-fullmix status=%s writes=%s artifact=%s",
        report.get("status"),
        report.get("MUSICAL WRITES"),
        report.get("artifact"),
    )
    public = {
        "status": report.get("status"),
        "analyzer_id": report.get("analyzer_id"),
        "analyzer_sha256": report.get("analyzer_sha256"),
        "configuration_hash": report.get("configuration_hash"),
        "MUSICAL WRITES": report.get("MUSICAL WRITES"),
        "ASTRA CALLS": report.get("ASTRA CALLS"),
        "artifact": report.get("artifact"),
        "regions": [
            {
                "region_id": row.get("region_id"),
                "audio_sha256": row.get("audio_sha256"),
                "duration_s": row.get("duration_s"),
                "energy_event_count": len(row.get("energy_events") or []),
                "energy_events": [
                    {
                        "kind": ev.get("kind"),
                        "start_s": round(float(ev.get("start_s") or 0.0), 3),
                        "end_s": round(float(ev.get("end_s") or 0.0), 3),
                        "duration_s": round(float(ev.get("duration_s") or 0.0), 3),
                        "relative_drop_db": round(float(ev.get("relative_drop_db") or 0.0), 2),
                        "similarity": ev.get("event_similarity_count"),
                        "period_s": ev.get("approx_period_s"),
                        "repetition_strength": ev.get("repetition_strength"),
                    }
                    for ev in (row.get("energy_events") or [])
                ],
                "transient_count": ((row.get("transient") or {}).get("transient_count")),
                "spectral_event_count": len(row.get("spectral_events") or []),
                "cache_hit": row.get("cache_hit"),
            }
            for row in report.get("regions") or []
        ],
    }
    print(json.dumps(public, indent=2, ensure_ascii=False))
    return 0 if report.get("MUSICAL WRITES") == 0 else 2


def _session_run1_astra_r2(evidence: Path, logger) -> int:
    from copilot.reasoning.session_astra import compare_astra_revisions, run_session_astra

    report = run_session_astra(
        evidence=evidence,
        source_run="session_run1",
        include_fullmix=True,
        diagnosis_revision=2,
        artifact_name="session_run1_astra_r2.json",
    )
    code = _print_astra_public(report, logger)
    if report.get("status") == "ASTRA COMPLETE":
        compare = compare_astra_revisions(evidence=evidence)
        print(json.dumps({"compare_artifact": compare.get("artifact"), "regions": compare.get("regions")}, indent=2, ensure_ascii=False))
    return code


def _session_run1_grounding_causal(evidence: Path, logger) -> int:
    """Freeze FullMix V1, fix/revalidate C grounding, gather causal evidence, Astra r3 C only."""
    from copilot.audio.fullmix import freeze_fullmix_v1
    from copilot.reasoning.session_astra import (
        revalidate_historical_r2_region,
        run_region_causal_astra,
    )

    freeze = freeze_fullmix_v1(evidence=evidence, source_run="session_run1")
    reval = revalidate_historical_r2_region(
        evidence=evidence,
        source_run="session_run1",
        region_id="REGION_C",
    )
    # Only call Astra for a new revision if historical r2 still cannot validly pass,
    # OR always for causal revision (r3) after evidence — r3 is a new revision with
    # causal context; r2 is never overwritten.
    causal_report = run_region_causal_astra(
        evidence=evidence,
        source_run="session_run1",
        region_id="REGION_C",
        diagnosis_revision=3,
    )
    out = {
        "FULLMIX V1 FROZEN": freeze.get("status"),
        "analyzer_id": freeze.get("analyzer_id"),
        "configuration_hash": freeze.get("configuration_hash"),
        "analyzer_hash": freeze.get("analyzer_hash"),
        "energy_reference_method": freeze.get("energy_reference_method"),
        "RUN1 MARKED CALIBRATION": freeze.get("run_classification"),
        "R2_C_BEFORE": reval.get("R2_C_BEFORE"),
        "R2_C_AFTER": reval.get("R2_C_AFTER"),
        "R2_remaining_reason": reval.get("remaining_reason"),
        "grounding_revalidation_artifact": reval.get("artifact"),
        "freeze_artifact": freeze.get("artifact"),
        "causal_astra": {
            "status": causal_report.get("status"),
            "diagnosis_revision": causal_report.get("diagnosis_revision"),
            "ASTRA CALLS": causal_report.get("ASTRA CALLS"),
            "MUSICAL WRITES": causal_report.get("MUSICAL WRITES"),
            "MUSICPLAN_GATE": causal_report.get("MUSICPLAN_GATE"),
            "artifact": causal_report.get("artifact"),
            "accepted": (causal_report.get("regions") or [{}])[0].get("accepted"),
            "failure": (causal_report.get("regions") or [{}])[0].get("failure"),
            "category": ((causal_report.get("regions") or [{}])[0].get("output") or {}).get(
                "category"
            ),
            "status_diag": ((causal_report.get("regions") or [{}])[0].get("output") or {}).get(
                "status"
            ),
            "summary": ((causal_report.get("regions") or [{}])[0].get("output") or {}).get(
                "summary"
            ),
            "requested_evidence_from_r2": causal_report.get("requested_evidence_from_r2"),
            "evidence_obtained_summary": causal_report.get("evidence_obtained_summary"),
        },
    }
    summary_path = evidence / "session_run1_grounding_causal_summary.json"
    summary_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "grounding-causal freeze=%s r2_after=%s r3_accepted=%s gate=%s writes=%s",
        freeze.get("status"),
        reval.get("R2_C_AFTER"),
        out["causal_astra"]["accepted"],
        causal_report.get("MUSICPLAN_GATE"),
        causal_report.get("MUSICAL WRITES"),
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if causal_report.get("status") == "REAL_MODEL_UNAVAILABLE":
        return 2
    if causal_report.get("MUSICAL WRITES") != 0:
        return 2
    return 0


def _session_run1_causal_trace(evidence: Path, logger) -> int:
    """Causal Trace V1 + Astra r4 for REGION_C only. No MusicPlan auto-build."""
    from copilot.reasoning.session_astra import run_region_causal_trace_astra

    report = run_region_causal_trace_astra(
        evidence=evidence,
        source_run="session_run1",
        region_id="REGION_C",
        diagnosis_revision=4,
    )
    row = (report.get("regions") or [{}])[0]
    out = report.get("output") if False else (row.get("output") or {})
    public = {
        "status": report.get("status"),
        "EVENT_QN": {
            "event_id": (report.get("primary_gap_event") or {}).get("event_id"),
            "audio_s": [
                (report.get("primary_gap_event") or {}).get("event_audio_start_s"),
                (report.get("primary_gap_event") or {}).get("event_audio_end_s"),
            ],
            "qn": [
                (report.get("primary_gap_event") or {}).get("event_start_qn"),
                (report.get("primary_gap_event") or {}).get("event_end_qn"),
            ],
            "kind": (report.get("primary_gap_event") or {}).get("kind"),
        },
        "ruled_out": report.get("ruled_out"),
        "supported_cause_candidates": report.get("supported_cause_candidates"),
        "next_evidence": report.get("next_evidence"),
        "live_reconciliation": {
            "status": (report.get("live_reconciliation") or {}).get("status"),
            "ok": (report.get("live_reconciliation") or {}).get("ok"),
        },
        "ASTRA_R4": {
            "accepted": row.get("accepted"),
            "failure": row.get("failure"),
            "category": out.get("category"),
            "status": out.get("status"),
            "summary": out.get("summary"),
            "confidence": out.get("confidence"),
        },
        "MUSICPLAN_GATE": report.get("MUSICPLAN_GATE"),
        "MUSICPLAN_GATE_REASON": report.get("MUSICPLAN_GATE_REASON"),
        "MUSICAL WRITES": report.get("MUSICAL WRITES"),
        "ASTRA CALLS": report.get("ASTRA CALLS"),
        "causal_trace_artifact": report.get("causal_trace_artifact"),
        "artifact": report.get("artifact"),
    }
    summary_path = evidence / "session_run1_causal_trace_summary.json"
    summary_path.write_text(json.dumps(public, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "causal-trace r4 accepted=%s gate=%s writes=%s next=%s",
        row.get("accepted"),
        report.get("MUSICPLAN_GATE"),
        report.get("MUSICAL WRITES"),
        report.get("next_evidence"),
    )
    print(json.dumps(public, indent=2, ensure_ascii=False))
    if report.get("status") in {"REAL_MODEL_UNAVAILABLE", "PROJECT_STATE_CONFLICT"}:
        return 2
    if report.get("MUSICAL WRITES") != 0:
        return 2
    return 0


def _session_run1_source_audio(evidence: Path, logger) -> int:
    """SOURCE AUDIO TRACE V1 (Core) + Astra r5 (reasoning). No MusicPlan auto-build."""
    from copilot.audio.source_audio_trace import run_source_audio_trace
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.reasoning.session_astra import run_region_source_audio_astra

    adapter = AbletonTcpAdapter()
    adapter.connect()
    try:
        source_payload = run_source_audio_trace(
            adapter,
            evidence=evidence,
            source_run="session_run1",
        )
        live_project = source_payload.get("project_token")
        live_audible = source_payload.get("audible_token")
    finally:
        try:
            adapter.disconnect()
        except Exception:
            pass

    report = run_region_source_audio_astra(
        source_payload=source_payload,
        evidence=evidence,
        source_run="session_run1",
        region_id="REGION_C",
        diagnosis_revision=5,
        live_project_token=live_project,
        live_audible_token=live_audible,
    )
    row = (report.get("regions") or [{}])[0]
    out = row.get("output") or {}
    obs = {
        o.get("track"): {
            "signal_class": o.get("signal_class"),
            "midi_audio_relation": o.get("midi_audio_relation"),
            "event_rms": o.get("event_rms"),
            "event_peak": o.get("event_peak"),
            "relative_drop_db": o.get("relative_drop_db"),
            "main_event_signal_class": o.get("main_event_signal_class"),
        }
        for o in (report.get("observations") or [])
    }
    public = {
        "status": report.get("status"),
        "event_qn_range": report.get("event_qn_range"),
        "event_audio_range_s": report.get("event_audio_range_s"),
        "Strum": obs.get("Strum"),
        "Transform Seed": obs.get("Transform Seed"),
        "Rainstorm": obs.get("Rainstorm"),
        "decision": report.get("decision"),
        "ASTRA_R5": {
            "accepted": row.get("accepted"),
            "failure": row.get("failure"),
            "category": out.get("category"),
            "status": out.get("status"),
            "summary": out.get("summary"),
            "confidence": out.get("confidence"),
        },
        "MUSICPLAN_GATE": report.get("MUSICPLAN_GATE"),
        "MUSICPLAN_GATE_REASON": report.get("MUSICPLAN_GATE_REASON"),
        "MUSICAL WRITES": report.get("MUSICAL WRITES"),
        "AUDIBLE_MIX_MUTATIONS": report.get("AUDIBLE_MIX_MUTATIONS"),
        "ASTRA CALLS": report.get("ASTRA CALLS"),
        "temp_routing_restores": [
            {"track": p.get("track"), "restore_ok": (p.get("restore") or {}).get("ok")}
            for p in (report.get("passes") or [])
        ],
        "source_audio_artifact": report.get("source_audio_artifact"),
        "artifact": report.get("artifact"),
    }
    summary_path = evidence / "session_run1_source_audio_summary.json"
    summary_path.write_text(json.dumps(public, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "source-audio r5 accepted=%s gate=%s writes=%s audible_mut=%s decision=%s",
        row.get("accepted"),
        report.get("MUSICPLAN_GATE"),
        report.get("MUSICAL WRITES"),
        report.get("AUDIBLE_MIX_MUTATIONS"),
        (report.get("decision") or {}).get("code"),
    )
    print(json.dumps(public, indent=2, ensure_ascii=False))
    if report.get("status") in {
        "REAL_MODEL_UNAVAILABLE",
        "PROJECT_STATE_CONFLICT",
        "PRE-FLIGHT STOP",
    }:
        return 2
    if report.get("MUSICAL WRITES") != 0 or report.get("AUDIBLE_MIX_MUTATIONS") != 0:
        return 2
    if any(p.get("restore_ok") is False for p in public["temp_routing_restores"]):
        return 2
    return 0


def _session_run1_strum_device(evidence: Path, logger) -> int:
    """STRUM DEVICE TRACE V1 (Core) + Astra r6 (reasoning). No MusicPlan auto-build."""
    from copilot.audio.strum_device_trace import run_strum_device_trace
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.reasoning.session_astra import run_region_strum_device_astra

    adapter = AbletonTcpAdapter()
    adapter.connect()
    try:
        strum_payload = run_strum_device_trace(
            adapter,
            evidence=evidence,
            source_run="session_run1",
        )
        snap = strum_payload.get("state_snapshot") or {}
        live_project = snap.get("PROJECT_STATE_TOKEN")
        live_audible = snap.get("AUDIBLE_STATE_TOKEN")
        live_target = snap.get("TARGET_STATE_TOKEN")
    finally:
        try:
            adapter.disconnect()
        except Exception:
            pass

    report = run_region_strum_device_astra(
        strum_payload=strum_payload,
        evidence=evidence,
        source_run="session_run1",
        region_id="REGION_C",
        diagnosis_revision=6,
        live_project_token=live_project,
        live_audible_token=live_audible,
        live_target_token=live_target,
    )
    row = (report.get("regions") or [{}])[0]
    out = row.get("output") or {}
    cause = report.get("cause_result") or {}
    public = {
        "status": report.get("status"),
        "event_qn_range": report.get("event_qn_range"),
        "state_snapshot": report.get("state_snapshot"),
        "cause_status": cause.get("status"),
        "cause_detail": cause.get("detail"),
        "cause_leads": cause.get("leads"),
        "ruled_out": cause.get("ruled_out"),
        "automation_highlights": [
            {
                "parameter_identity": a.get("parameter_identity"),
                "parameter_raw": a.get("parameter_raw"),
                "amplitude_causality": a.get("amplitude_causality"),
                "value_before": a.get("value_before"),
                "value_during": a.get("value_during"),
                "value_after": a.get("value_after"),
                "changed_across_event": a.get("changed_across_event"),
            }
            for a in (report.get("automation_candidates") or [])
            if a.get("changed_across_event") or a.get("amplitude_causality") in {"KNOWN", "PLAUSIBLE"}
        ],
        "ASTRA_R6": {
            "accepted": row.get("accepted"),
            "failure": row.get("failure"),
            "category": out.get("category"),
            "status": out.get("status"),
            "summary": out.get("summary"),
            "confidence": out.get("confidence"),
        },
        "MUSICPLAN_GATE": report.get("MUSICPLAN_GATE"),
        "MUSICPLAN_GATE_REASON": report.get("MUSICPLAN_GATE_REASON"),
        "MUSICAL WRITES": report.get("MUSICAL WRITES"),
        "ASTRA CALLS": report.get("ASTRA CALLS"),
        "strum_device_artifact": report.get("strum_device_artifact"),
        "artifact": report.get("artifact"),
    }
    summary_path = evidence / "session_run1_strum_device_summary.json"
    summary_path.write_text(json.dumps(public, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "strum-device r6 accepted=%s gate=%s cause=%s writes=%s",
        row.get("accepted"),
        report.get("MUSICPLAN_GATE"),
        cause.get("status"),
        report.get("MUSICAL WRITES"),
    )
    print(json.dumps(public, indent=2, ensure_ascii=False))
    if report.get("status") in {"REAL_MODEL_UNAVAILABLE", "PRE-FLIGHT STOP"}:
        return 2
    if report.get("MUSICAL WRITES") != 0:
        return 2
    return 0


def _print_astra_public(report: dict, logger) -> int:
    logger.info(
        "session-astra status=%s rev=%s astra=%s writes=%s artifact=%s",
        report.get("status"),
        report.get("diagnosis_revision"),
        report.get("ASTRA CALLS"),
        report.get("MUSICAL WRITES"),
        report.get("artifact"),
    )
    public = {
        "status": report.get("status"),
        "diagnosis_revision": report.get("diagnosis_revision"),
        "ASTRA CALLS": report.get("ASTRA CALLS"),
        "MUSICAL WRITES": report.get("MUSICAL WRITES"),
        "MusicPlan": report.get("MusicPlan"),
        "provider": report.get("provider"),
        "provider_version": report.get("provider_version"),
        "prompt_version": report.get("prompt_version"),
        "schema_version": report.get("schema_version"),
        "include_fullmix": report.get("include_fullmix"),
        "artifact": report.get("artifact"),
        "regions": [
            {
                "region_id": row.get("region_id"),
                "accepted": row.get("accepted"),
                "failure": row.get("failure"),
                "summary": ((row.get("diagnosis") or {}).get("user_facing") or (row.get("output") or {}).get("summary")),
                "category": (row.get("output") or {}).get("category"),
                "status": (row.get("output") or {}).get("status") or (row.get("diagnosis") or {}).get("status"),
                "human_overall_feel": (row.get("human") or {}).get("overall_feel"),
                "human_would_change": (row.get("human") or {}).get("would_change"),
                "human_notes": (row.get("human") or {}).get("notes"),
            }
            for row in report.get("regions") or []
        ],
    }
    if report.get("status") == "REAL_MODEL_UNAVAILABLE":
        public["reason"] = report.get("reason")
    print(json.dumps(public, indent=2, ensure_ascii=False))
    if report.get("status") == "REAL_MODEL_UNAVAILABLE":
        return 2
    if report.get("MUSICAL WRITES") != 0:
        return 2
    return 0


def _session_run1(evidence: Path, logger) -> int:
    from copilot.audio.session_run1 import run_session_run1
    from copilot.daw.ableton_tcp import AbletonTcpAdapter

    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        report = run_session_run1(adapter, evidence)
    finally:
        adapter.disconnect()
    logger.info(
        "session-run1 status=%s astra=%s writes=%s artifact=%s",
        report.get("status"),
        report.get("ASTRA CALLS"),
        report.get("MUSICAL WRITES"),
        evidence / "session_run1.json",
    )
    public = {key: value for key, value in report.items() if key != "DSP OBSERVATIONS"}
    public["DSP OBSERVATIONS"] = [
        {
            "region": row.get("region"),
            "analyzer_id": row.get("analyzer_id"),
            "analyzer_sha256": row.get("analyzer_sha256"),
            "features": row.get("features"),
            "observation_views": list((row.get("music_observation") or {}).keys()),
        }
        for row in report.get("DSP OBSERVATIONS") or []
    ]
    print(json.dumps(public, indent=2, ensure_ascii=False, default=str))
    status = str(report.get("status") or "")
    if status.startswith("RUN 1 COMPLETE"):
        return 0
    return 2


def _session_arrangement_start(evidence: Path, logger) -> int:
    from copilot.audio.arrangement_activity import inspect_tempo_contract, map_lowend_candidates
    from copilot.audio.arrangement_seek import TRANSPORT_PRIMITIVE_VERSION, run_atomic_transport_trust
    from copilot.audio.session_diagnose import WORKING_COPY_CANDIDATE
    from copilot.daw.ableton_tcp import AbletonTcpAdapter

    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        hello = adapter.handshake_info or {}
        als = Path(str(hello.get("path") or WORKING_COPY_CANDIDATE))
        try:
            session = adapter.snapshot(include_notes=False)
            if session.project_path:
                als = Path(session.project_path)
        except DawError:
            pass
        if als.is_dir():
            candidate = als / "pista_copilot_eval.als"
            als = candidate if candidate.is_file() else Path(WORKING_COPY_CANDIDATE)
        elif not als.is_file():
            als = Path(WORKING_COPY_CANDIDATE)
        tempo = inspect_tempo_contract(als)
        payload = {
            "phase": "ARRANGEMENT PLAYBACK AT QN — PRODUCTION FIX",
            "ATOMIC TRANSPORT IMPLEMENTATION": {
                "command": "start_playback_at_qn",
                "live_side": "start_playing() then current_song_time=target in one main-thread callback",
                "verification": "next_tick via schedule_message(1)",
                "transport_primitive_version": TRANSPORT_PRIMITIVE_VERSION,
                "hello_transport_primitive_version": hello.get("transport_primitive_version"),
                "control_surface_loaded": hello.get("transport_primitive_version")
                == TRANSPORT_PRIMITIVE_VERSION,
            },
            "TEMPO CONTRACT": tempo,
            "MUSICAL WRITES": 0,
            "ASTRA CALLS": 0,
            "DSP": 0,
            "NO CAPTURE": True,
        }
        if not tempo.get("ok"):
            payload["status"] = tempo.get("status") or "TEMPO_MAPPING_UNAVAILABLE"
            payload["TRANSPORT"] = {"ok": False, "skipped": True}
        elif hello.get("transport_primitive_version") != TRANSPORT_PRIMITIVE_VERSION:
            payload["status"] = "CONTROL_SURFACE_RELOAD_REQUIRED"
            payload["instruction"] = (
                "install-script copied AbletonMCP. Toggle Control Surface AbletonMCP Off/On, "
                "then re-run session-arrangement-start. Do not capture."
            )
            payload["TRANSPORT"] = {"ok": False, "skipped": True}
        else:
            transport = run_atomic_transport_trust(adapter, evidence=evidence)
            payload["TRANSPORT"] = transport
            payload["32 RESULT"] = next(
                (row for row in transport.get("rows") or [] if row.get("target_qn") == 32.0), None
            )
            payload["172 RESULT"] = next(
                (row for row in transport.get("rows") or [] if row.get("target_qn") == 172.0), None
            )
            payload["292 RESULT"] = next(
                (row for row in transport.get("rows") or [] if row.get("target_qn") == 292.0), None
            )
            payload["IMPLIED START ERRORS"] = transport.get("implied_start_errors_qn")
            payload["status"] = transport.get("TRANSPORT_START_PROVENANCE")
        payload["READ-ONLY ACTIVITY MAP"] = map_lowend_candidates(als)
        payload["CANDIDATE REAL REGIONS"] = (payload["READ-ONLY ACTIVITY MAP"] or {}).get(
            "candidates"
        )
    finally:
        adapter.disconnect()
    path = evidence / "arrangement_playback_at_qn.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    payload["artifact"] = str(path)
    logger.info(
        "session-arrangement-start status=%s astra=0 dsp=0 artifact=%s",
        payload.get("status"),
        path,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    if payload.get("status") == "VERIFIED":
        return 0
    return 2
    from copilot.audio.arrangement_seek import run_seek_trust
    from copilot.audio.session_run1 import run_region1_recapture
    from copilot.daw.ableton_tcp import AbletonTcpAdapter

    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        seek = run_seek_trust(adapter, evidence)
        payload = {"SEEK": seek}
        if seek.get("ok"):
            payload["REGION_1 RECAPTURE RESULT"] = run_region1_recapture(adapter, evidence)
        else:
            payload["REGION_1 RECAPTURE RESULT"] = {
                "status": "SKIPPED",
                "reason": "ARRANGEMENT_SEEK_FAILED",
            }
    finally:
        adapter.disconnect()
    logger.info(
        "session-seek-trust seek_ok=%s region1=%s",
        (payload.get("SEEK") or {}).get("ok"),
        (payload.get("REGION_1 RECAPTURE RESULT") or {}).get("status"),
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    if not (payload.get("SEEK") or {}).get("ok"):
        return 2
    if (payload.get("REGION_1 RECAPTURE RESULT") or {}).get("status") != "REGION_1 TRUSTWORTHY":
        return 2
    return 0


def _reason_revalidate(evidence: Path, logger) -> int:
    from copilot.reasoning.revalidate import run_m13, write_m13

    report = run_m13(source=evidence / "reason_real_full.json")
    path = write_m13(report, evidence / "reason_real_full_revalidated.json")
    logger.info(
        "reason-revalidate model_calls=%s false_positive=%s quality=%s dest=%s",
        report["model_calls"],
        report["VALIDATOR_FALSE_POSITIVE"],
        report["MODEL_QUALITY"],
        path,
    )
    print(json.dumps(
        {
            "VALIDATOR_FALSE_POSITIVE": report["VALIDATOR_FALSE_POSITIVE"],
            "CLEAR_TEMPORAL_EVIDENCE": report["CLEAR_TEMPORAL_EVIDENCE"],
            "CLEAR_SPECTRAL_EVIDENCE": report["CLEAR_SPECTRAL_EVIDENCE"],
            "STATUS_CONTRACT": report["STATUS_CONTRACT"],
            "ASTRA_HYPOTHESIS_QUALITY": report["ASTRA_HYPOTHESIS_QUALITY"],
            "ASTRA_CALIBRATION": report["ASTRA_CALIBRATION"],
            "MODEL_QUALITY": report["MODEL_QUALITY"],
            "CORE_SAFETY_AFFECTED": report["CORE_SAFETY_AFFECTED"],
            "previous_accepted": report["previous_accepted"],
            "previous_rejected": report["previous_rejected"],
            "sole_10ms_rejections": report["sole_10ms_rejections"],
            "model_calls": report["model_calls"],
            "artifact": str(path),
        },
        indent=2,
    ))
    return 0


def _reason_eval(evidence: Path, logger) -> int:
    from copilot.reasoning.eval import run_eval

    report = run_eval(include_real_model=True)
    path = evidence / "reason_eval.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info(
        "reason-eval musical_writes=%s accepted=%s real=%s",
        report["musical_writes"],
        report["metrics"]["accepted_core_fixtures"],
        report["real_model"].get("status"),
    )
    print(json.dumps(report, indent=2))
    if report["musical_writes"] != 0:
        return 2
    if report["metrics"]["accepted_core_fixtures"] < 6:
        return 2
    if not report["adversarial"]["injected_hallucination_rejected"]:
        return 2
    return 0


def _reason_real(evidence: Path, logger, *, gate: str) -> int:
    from copilot.reasoning.eval import CORE_FIXTURES
    from copilot.reasoning.real_gate import run_real_model_gate

    names = {
        "smoke": "reason_real_smoke.json",
        "mini": "reason_real_mini.json",
        "full": "reason_real_full.json",
    }
    if gate == "smoke":
        fixtures = ("CLEAR_NO_ACTION",)
        repeats = 1
        timeout_s = 180.0
    elif gate == "mini":
        fixtures = CORE_FIXTURES
        repeats = 1
        timeout_s = 180.0
    else:
        fixtures = CORE_FIXTURES
        repeats = 5
        timeout_s = 90.0
    report = run_real_model_gate(
        repeats=repeats,
        fixtures=fixtures,
        timeout_s=timeout_s,
        close_grounding=gate == "full",
        progress_path=evidence / "reason_real_progress.jsonl",
    )
    report["gate"] = gate
    path = evidence / names[gate]
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info(
        "reason-real gate=%s status=%s writes=%s provider=%s contract=%s",
        gate,
        report.get("status"),
        report.get("musical_writes"),
        report.get("provider"),
        (report.get("provider_contract") or {}).get("text.format.type")
        or (report.get("provider_contract") or {}).get("response_format.type"),
    )
    print(json.dumps(report, indent=2))
    if report.get("status") == "REAL_MODEL_UNAVAILABLE":
        return 2
    if report.get("musical_writes") != 0:
        return 2
    if gate == "smoke":
        cases = (report.get("fixtures") or [{}])[0].get("runs") or []
        row = cases[0] if cases else {}
        if row.get("category"):
            return 0
        return 2
    if gate == "mini":
        if report.get("schema_valid_rate", 0) <= 0:
            return 2
        if report.get("core_safety_result") != "PASS":
            return 2
        accepted_bad = report.get("grounding_violations_accepted") or {}
        if any(accepted_bad.values()):
            return 2
        return 0
    if report.get("REAL_MODEL_GROUNDING") == "VERIFIED" and report.get("core_safety_result") == "PASS":
        return 0
    return 2


def _production_write(
    evidence: Path,
    logger,
    *,
    dry_run: bool = False,
    plan_path: str | None = None,
    prepare_controlled: bool = False,
    execute_controlled: bool = False,
    audible_effect: bool = False,
    audible_effect_v2: bool = False,
    autonomous_improvement: bool = False,
    autonomous_evaluation_v2: bool = False,
    autonomous_evaluation_v3: bool = False,
    arrangement_source_isolation: bool = False,
    delta: float = -0.02,
    target_track: str | None = None,
    source_plan_id: str = "plan_82608749d172",
    source_envelope_id: str = "env_5886f4fb0b",
    expected_before: float = 0.75,
) -> int:
    """Canonical write entrypoint.

    Modes:
    - --dry-run / --prepare-controlled-plan: validate/compile only (zero mutations)
    - --execute-controlled-write: CONTROLLED_WRITE_LOOP_V1 (FROZEN — do not reopen)
    - --audible-effect-verification: AUDIBLE_EFFECT_VERIFICATION_V1
    - --audible-effect-verification-v2: AUDIBLE_EFFECT_VERIFICATION_V2
    - --first-autonomous-musical-improvement: FIRST_AUTONOMOUS_MUSICAL_IMPROVEMENT_V1
    """
    from copilot.audio.capture_journal_recovery import unresolved_capture_journals
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.daw.state_tokens import attach_tokens
    from copilot.musicplan import (
        create_controlled_volume_plan,
        dry_run_musicplan,
        plan_from_region_c_r6,
    )
    from copilot.musicplan.execute import (
        build_agent_tools,
        execute_controlled_write_loop,
    )
    from copilot.schemas.musicplan import MusicPlan, PlanIntentClass, PlanStatus

    if (
        not dry_run
        and not prepare_controlled
        and not execute_controlled
        and not audible_effect
        and not audible_effect_v2
        and not autonomous_improvement
        and not autonomous_evaluation_v2
        and not autonomous_evaluation_v3
        and not arrangement_source_isolation
    ):
        payload = {
            "status": "EXECUTION_DISABLED",
            "CANONICAL_WRITE_PATH": CANONICAL_WRITE_PATH,
            "CANONICAL_PRE_WRITE_PATH": CANONICAL_PRE_WRITE_PATH,
            "MUSICPLAN_GATE": "CLOSED",
            "MUSICPLAN_GATE_REASON": "explicit_mode_required",
            "MUSICAL WRITES": 0,
            "note": (
                "Use --dry-run, --prepare-controlled-plan, "
                "--execute-controlled-write, --audible-effect-verification, "
                "--audible-effect-verification-v2, "
                "--first-autonomous-musical-improvement, "
                "--autonomous-musical-evaluation-v2, "
                "--autonomous-musical-evaluation-v3, "
                "or --arrangement-active-source-isolation."
            ),
        }
        print(json.dumps(payload, indent=2))
        return 2

    adapter = AbletonTcpAdapter()
    adapter.connect()
    try:
        session = adapter.snapshot(include_notes=False)
        attach_tokens(session)

        if arrangement_source_isolation:
            from copilot.audio.arrangement_active_source_isolation import (
                run_arrangement_active_source_isolation_v1,
            )

            report = run_arrangement_active_source_isolation_v1(
                adapter,
                evidence=evidence,
            )
            status = report.get("ARRANGEMENT_ACTIVE_SOURCE_ISOLATION_V1")
            logger.info(
                "production-write arrangement-source-isolation status=%s writes=%s",
                status,
                report.get("MUSICAL WRITES"),
            )
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            return 0 if status == "VERIFIED" else 2

        if autonomous_evaluation_v3:
            from copilot.audio.autonomous_musical_evaluation_v3 import (
                run_autonomous_musical_evaluation_v3,
            )

            report = run_autonomous_musical_evaluation_v3(
                adapter,
                evidence=evidence,
            )
            status = report.get("AUTONOMOUS_MUSICAL_EVALUATION_V3")
            logger.info(
                "production-write autonomous-eval-v3 status=%s decision=%s writes=%s",
                status,
                report.get("final_musical_decision"),
                report.get("MUSICAL WRITES"),
            )
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            ok = status in {
                "AUTONOMOUS_IMPROVEMENT_VERIFIED",
                "NO_ACTION_REQUIRED",
                "INSUFFICIENT_EVIDENCE",
                "ACTION_NOT_AVAILABLE",
                "AUTONOMOUS_CHANGE_ROLLED_BACK",
                "DIAGNOSIS_UNSTABLE",
                "NO_INDEPENDENT_ACTIVE_HOLDOUT",
            }
            return 0 if ok else 2

        if autonomous_evaluation_v2:
            from copilot.audio.autonomous_musical_evaluation_v2 import (
                run_autonomous_musical_evaluation_v2,
            )

            report = run_autonomous_musical_evaluation_v2(
                adapter,
                evidence=evidence,
            )
            status = report.get("AUTONOMOUS_MUSICAL_EVALUATION_V2")
            logger.info(
                "production-write autonomous-eval-v2 status=%s decision=%s writes=%s",
                status,
                report.get("final_musical_decision"),
                report.get("MUSICAL WRITES"),
            )
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            ok = status in {
                "AUTONOMOUS_IMPROVEMENT_VERIFIED",
                "NO_ACTION_REQUIRED",
                "INSUFFICIENT_EVIDENCE",
                "ACTION_NOT_AVAILABLE",
                "AUTONOMOUS_CHANGE_ROLLED_BACK",
                "DIAGNOSIS_UNSTABLE",
            }
            return 0 if ok else 2

        if autonomous_improvement:
            from copilot.audio.first_autonomous_musical_improvement_v1 import (
                run_first_autonomous_musical_improvement_v1,
            )

            report = run_first_autonomous_musical_improvement_v1(
                adapter,
                evidence=evidence,
            )
            status = report.get("FIRST_AUTONOMOUS_MUSICAL_IMPROVEMENT_V1")
            logger.info(
                "production-write autonomous-improvement status=%s decision=%s writes=%s",
                status,
                report.get("final_musical_decision"),
                report.get("MUSICAL WRITES"),
            )
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            ok = status in {
                "AUTONOMOUS_IMPROVEMENT_VERIFIED",
                "NO_ACTION_REQUIRED",
                "INSUFFICIENT_EVIDENCE",
                "ACTION_NOT_AVAILABLE",
                "AUTONOMOUS_CHANGE_ROLLED_BACK",
            }
            return 0 if ok else 2

        if audible_effect_v2:
            from copilot.audio.audible_effect_verification_v2 import (
                run_audible_effect_verification_v2,
            )

            report = run_audible_effect_verification_v2(
                adapter,
                evidence=evidence,
                restored_audio=True,
            )
            logger.info(
                "production-write audible-effect-v2 verdict=%s attribution=%s",
                report.get("AUDIBLE_EFFECT_VERIFICATION_V2"),
                report.get("CAUSAL_ATTRIBUTION"),
            )
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            return 0 if report.get("AUDIBLE_EFFECT_VERIFICATION_V2") == "VERIFIED" else 2

        if audible_effect:
            from copilot.audio.audible_effect_verification import (
                run_audible_effect_verification,
            )

            report = run_audible_effect_verification(
                adapter,
                evidence=evidence,
                target_track=target_track or "Coffee Leaf",
                expected_before=expected_before,
                delta=delta,
                restored_audio=True,
            )
            logger.info(
                "production-write audible-effect verdict=%s attribution=%s",
                report.get("AUDIBLE_EFFECT_VERIFICATION_V1"),
                report.get("CAUSAL_ATTRIBUTION"),
            )
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            return 0 if report.get("AUDIBLE_EFFECT_VERIFICATION_V1") == "VERIFIED" else 2

        if execute_controlled:
            name = target_track or "Coffee Leaf"
            tools = build_agent_tools(
                adapter,
                journal_path=evidence / "agent_journal.jsonl",
            )
            report = execute_controlled_write_loop(
                tools,
                target_track=name,
                delta=delta,
                expected_before=expected_before,
                source_plan_id=source_plan_id,
                source_dry_run_envelope_id=source_envelope_id,
                persist_dir=evidence / "musicplans",
            )
            tools.transactions.save(evidence / "agent_transactions.json")
            out = evidence / "controlled_write_loop_v1.json"
            out.write_text(
                json.dumps(report, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            report["public_artifact"] = str(out)
            logger.info(
                "production-write controlled loop plan=%s txn=%s status=%s writes=%s",
                report.get("plan_id"),
                report.get("transaction_id"),
                report.get("CONTROLLED_WRITE_LOOP_V1"),
                report.get("MUSICAL_WRITE_COUNT"),
            )
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            return 0 if report.get("CONTROLLED_WRITE_LOOP_V1") == "VERIFIED" else 2

        if prepare_controlled:
            # Prefer an explicit track; otherwise first musical track that is not capture/lab.
            banned = {
                "Copilot Capture",
                "Copilot Capture Bass",
                "AI Test",
                "Master",
            }
            track = None
            if target_track:
                track = session.track_by_name(target_track)
            if track is None:
                for candidate in session.tracks:
                    if candidate.name in banned:
                        continue
                    if candidate.role in {"midi", "audio"}:
                        track = candidate
                        break
            if track is None:
                print(json.dumps({"status": "NO_TARGET", "MUSICAL WRITES": 0}, indent=2))
                return 2
            plan = create_controlled_volume_plan(
                session=session,
                track=track,
                delta=delta,
            )
            result = dry_run_musicplan(plan, session=session, persist_dir=evidence / "musicplans")
            public = {
                "status": "CONTROLLED_ENGINEERING_VALIDATION_DRY_RUN",
                "intent_class": PlanIntentClass.CONTROLLED_ENGINEERING_VALIDATION.value,
                "plan_id": result.plan_id,
                "plan_status": result.plan_status.value,
                "target_track": track.name,
                "delta_ableton_volume": delta,
                "current_volume": result.current_volume,
                "compiled": None
                if result.compiled is None
                else result.compiled.model_dump(mode="json"),
                "gate": result.gate,
                "would_mutate": False,
                "MUSICAL WRITES": 0,
                "EXECUTED": False,
                "artifact": result.artifact,
                "note": "Not autonomous musical improvement. Dry-run only.",
            }
            out = evidence / "musicplan_v1_controlled_dry_run.json"
            out.write_text(json.dumps(public, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(
                "production-write controlled dry-run plan=%s status=%s writes=0",
                result.plan_id,
                result.plan_status,
            )
            print(json.dumps(public, indent=2, ensure_ascii=False))
            return 0 if result.plan_status == PlanStatus.READY_FOR_EXECUTION else 2

        if plan_path:
            raw = json.loads(Path(plan_path).read_text(encoding="utf-8"))
            # Accept either bare plan or wrapper with "plan" key.
            plan_obj = raw.get("plan") if isinstance(raw, dict) and "plan" in raw else raw
            plan = MusicPlan.model_validate(plan_obj)
            result = dry_run_musicplan(plan, session=session, persist_dir=evidence / "musicplans")
            public = {
                "status": "DRY_RUN",
                "plan_id": result.plan_id,
                "plan_status": result.plan_status.value,
                "gate": result.gate,
                "compiled": None
                if result.compiled is None
                else result.compiled.model_dump(mode="json"),
                "would_mutate": False,
                "MUSICAL WRITES": 0,
                "EXECUTED": False,
                "unresolved_capture_journals": unresolved_capture_journals(),
                "artifact": result.artifact,
            }
            print(json.dumps(public, indent=2, ensure_ascii=False, default=str))
            return 0 if result.musical_writes == 0 else 2

        # Default dry-run without plan: REGION_C abstention check
        r6 = evidence / "session_run1_astra_r6_region_c.json"
        plan = plan_from_region_c_r6(r6)
        result = dry_run_musicplan(plan, session=session, persist_dir=evidence / "musicplans")
        public = {
            "status": "REGION_C_ABSTENTION_DRY_RUN",
            "plan_id": result.plan_id,
            "plan_status": result.plan_status.value,
            "executable_actions": 0,
            "rejection_reason": plan.rejection_reason,
            "MUSICAL WRITES": 0,
            "EXECUTED": False,
            "artifact": result.artifact,
        }
        print(json.dumps(public, indent=2, ensure_ascii=False))
        return 0
    finally:
        try:
            adapter.disconnect()
        except Exception:
            pass


def _capture_journal_recover(evidence: Path, logger) -> int:
    """Append-only recovery for unresolved capture journals. No musical writes."""
    from copilot.audio.capture_journal_recovery import recover_all_unresolved
    from copilot.daw.ableton_tcp import AbletonTcpAdapter

    adapter = AbletonTcpAdapter()
    adapter.connect()
    try:
        report = recover_all_unresolved(daw=adapter)
    finally:
        try:
            adapter.disconnect()
        except Exception:
            pass
    path = evidence / "capture_journal_recovery.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info(
        "capture-journal-recover ok=%s remaining=%s",
        report.get("ok"),
        len(report.get("remaining_unresolved") or []),
    )
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("ok") else 2


def _connect_live_or_block(evidence: Path, artifact: str) -> AbletonTcpAdapter | dict:
    detection = detect_ableton()
    probe = probe_session_ready()
    if probe.status != SESSION_READY:
        payload = {
            "status": "BLOCKED",
            "verification_class": probe.status,
            "reason": probe.status,
            "session": probe.to_dict(),
            "detection": detection.to_dict(),
            "NO MOCK SUCCESS": True,
            "NO WRITE": True,
            "writes_permitted": False,
        }
        (evidence / artifact).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        return payload
    adapter = AbletonTcpAdapter()
    try:
        adapter.connect()
        live = adapter.snapshot(include_notes=False)
        from copilot.audio.cross_project_bootstrap_v1 import retain_tokens

        retain_tokens(live)
        current_identity = live.project_identity or live.project_token or ""
        check = revalidate_project(probe.project_identity, probe)
        if current_identity and probe.project_identity and current_identity != probe.project_identity:
            adapter.disconnect()
            payload = {
                "status": "BLOCKED",
                "verification_class": "STALE_PLAN",
                "reason": "PROJECT_CHANGED",
                "session": probe.to_dict(),
                "revalidate": check,
                "current_identity": current_identity,
                "detection": detection.to_dict(),
                "NO MOCK SUCCESS": True,
                "NO WRITE": True,
                "writes_permitted": False,
                "PROJECT_REVALIDATED": False,
            }
            (evidence / artifact).write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
            return payload
        if not check.get("PROJECT_REVALIDATED"):
            adapter.disconnect()
            payload = {
                "status": "BLOCKED",
                "verification_class": str(check.get("status") or "SESSION_NOT_READY"),
                "reason": str(check.get("reason") or "PROJECT_NOT_REVALIDATED"),
                "session": probe.to_dict(),
                "revalidate": check,
                "detection": detection.to_dict(),
                "NO MOCK SUCCESS": True,
                "NO WRITE": True,
                "writes_permitted": False,
            }
            (evidence / artifact).write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
            return payload
    except Exception as exc:
        try:
            adapter.disconnect()
        except Exception:
            pass
        payload = {
            "status": "BLOCKED",
            "reason": str(exc),
            "verification_class": "SESSION_NOT_READY",
            "session": probe.to_dict(),
            "detection": detection.to_dict(),
            "NO MOCK SUCCESS": True,
            "NO WRITE": True,
            "writes_permitted": False,
        }
        (evidence / artifact).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        return payload
    return adapter


def _project_bootstrap(evidence: Path, logger) -> int:
    from copilot.audio.cross_project_bootstrap_v1 import bootstrap_project, write_blocker

    connected = _connect_live_or_block(evidence, "cross_project_bootstrap_v1.json")
    if isinstance(connected, dict):
        write_blocker(evidence, str(connected.get("reason") or "LIVE_UNAVAILABLE"), **connected)
        print(json.dumps(connected, indent=2, default=str))
        return 2
    try:
        report = bootstrap_project(connected, evidence=evidence)
    finally:
        connected.disconnect()
    logger.info(
        "project-bootstrap status=%s milestone=%s",
        report.get("status"),
        report.get("CROSS_PROJECT_BOOTSTRAP_V1"),
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("CROSS_PROJECT_BOOTSTRAP_V1") == "VERIFIED" else 2


def _project_ready(evidence: Path, logger) -> int:
    from copilot.audio.project_ready_v1 import project_ready

    connected = _connect_live_or_block(evidence, "project_ready_v1.json")
    if isinstance(connected, dict):
        print(json.dumps(connected, indent=2, default=str))
        return 2
    try:
        report = project_ready(connected, evidence=evidence)
    finally:
        connected.disconnect()
    logger.info("project-ready status=%s", report.get("PROJECT_READY"))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("PROJECT_READY") == "VERIFIED" else 2


def _cross_project_validate(evidence: Path, logger) -> int:
    from copilot.audio.cross_project_musical_validation_v1 import (
        NEXT_READ_ONLY_VERIFIED,
        NEXT_VOLUME_LOOP,
        run_cross_project_musical_validation,
    )

    connected = _connect_live_or_block(
        evidence, "cross_project_musical_validation_v1.json"
    )
    if isinstance(connected, dict):
        print(json.dumps(connected, indent=2, default=str))
        return 2
    try:
        report = run_cross_project_musical_validation(connected, evidence=evidence)
    finally:
        connected.disconnect()
    logger.info("cross-project-validate status=%s", report.get("status"))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    status = str(report.get("status") or "")
    return 0 if status in {NEXT_READ_ONLY_VERIFIED, NEXT_VOLUME_LOOP} else 2


def _downstream_causal_state(
    evidence: Path,
    logger,
    *,
    region_id: str,
    start_qn: float | None,
    end_qn: float | None,
    source_names: list[str] | None,
) -> int:
    from copilot.audio.downstream_causal_state_v1 import run_downstream_causal_state_v1

    connected = _connect_live_or_block(evidence, "downstream_causal_state_v1.json")
    if isinstance(connected, dict):
        print(json.dumps(connected, indent=2, default=str))
        return 2
    try:
        report = run_downstream_causal_state_v1(
            connected,
            evidence=evidence,
            region_id=region_id,
            start_qn=start_qn,
            end_qn=end_qn,
            source_names=source_names,
        )
    finally:
        connected.disconnect()
    logger.info("downstream-causal-state status=%s", report.get("status"))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("status") in {"VERIFIED", "READ_ONLY_EVIDENCE"} else 2


def _sidechain_automation_state(
    evidence: Path,
    logger,
    *,
    region_id: str,
    start_qn: float | None,
    end_qn: float | None,
    source_names: list[str] | None,
) -> int:
    from copilot.audio.sidechain_automation_state_v1 import run_sidechain_automation_state

    connected = _connect_live_or_block(evidence, "sidechain_automation_state_v1.json")
    if isinstance(connected, dict):
        print(json.dumps(connected, indent=2, default=str))
        return 2
    try:
        report = run_sidechain_automation_state(
            connected,
            evidence=evidence,
            region_id=region_id,
            start_qn=start_qn,
            end_qn=end_qn,
            source_names=source_names,
        )
    finally:
        connected.disconnect()
    logger.info("sidechain-automation-state status=%s", report.get("status"))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("status") in {"VERIFIED", "READ_ONLY_EVIDENCE"} else 2


def _sample_library(evidence: Path, logger, argv: list[str]) -> int:
    from copilot.sample_library.library_v1 import index_library, load_index, search
    from copilot.sample_library.schemas import SampleRole

    roots_file = evidence / "sample_library_roots.json"
    index_path = evidence / "sample_library_index.json"

    if not argv:
        print("uso: sample-library add|index|status|search|embed|retrieve|scan-key")
        return 2

    sub = argv[0]
    if sub == "add":
        if len(argv) < 2:
            print("uso: sample-library add <folder>")
            return 2
        root = Path(argv[1]).expanduser().resolve()
        if not root.is_dir():
            print(json.dumps({"status": "BLOCKED", "error": f"not a directory: {root}"}, ensure_ascii=False))
            return 2
        roots = json.loads(roots_file.read_text(encoding="utf-8")) if roots_file.is_file() else []
        if str(root) not in roots:
            roots.append(str(root))
        roots_file.parent.mkdir(parents=True, exist_ok=True)
        roots_file.write_text(json.dumps(roots, indent=2), encoding="utf-8")
        print(json.dumps({"status": "OK", "roots": roots}, ensure_ascii=False, indent=2))
        return 0

    if sub == "index":
        roots = json.loads(roots_file.read_text(encoding="utf-8")) if roots_file.is_file() else []
        if not roots:
            print(json.dumps({"status": "BLOCKED", "error": "no roots; use 'sample-library add <folder>'"}, ensure_ascii=False))
            return 2
        counts = index_library([Path(r) for r in roots], index_path)
        print(json.dumps({"status": "OK", **counts}, ensure_ascii=False, indent=2))
        return 0

    if sub == "status":
        idx = load_index(index_path)
        if idx is None:
            print(json.dumps({"status": "EMPTY"}, ensure_ascii=False))
            return 0
        roles: dict[str, int] = {}
        for a in idx.assets.values():
            roles[a.semantic_role.value] = roles.get(a.semantic_role.value, 0) + 1
        print(json.dumps({
            "status": "OK",
            "total": len(idx.assets),
            "roots": idx.roots,
            "duplicates": len(idx.duplicates),
            "roles": roles,
            "updated_at": idx.updated_at,
        }, ensure_ascii=False, indent=2))
        return 0

    if sub == "search":
        if len(argv) < 2:
            print("uso: sample-library search <query> [ROLE]")
            return 2
        idx = load_index(index_path)
        if idx is None:
            print(json.dumps({"status": "EMPTY"}, ensure_ascii=False))
            return 0
        query = argv[1]
        role = None
        if len(argv) >= 3:
            try:
                role = SampleRole(argv[2].upper())
            except ValueError:
                role = None
        hits = search(idx, query, role=role, top_k=10)
        print(json.dumps({
            "status": "OK", "query": query, "role": role.value if role else None,
            "results": [
                {"filename": h.asset.filename, "role": h.asset.semantic_role.value,
                 "path": h.asset.relative_path, "score": h.score, "reasons": h.reasons}
                for h in hits
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    if sub == "scan-key":
        if len(argv) < 2:
            print("uso: sample-library scan-key <KEY> [ROLE]")
            return 2
        idx = load_index(index_path)
        if idx is None:
            print(json.dumps({"status": "EMPTY"}, ensure_ascii=False))
            return 0
        from copilot.sample_library.retrieval import SampleRetriever
        key = argv[1].lower()
        role = None
        if len(argv) >= 3:
            try:
                role = SampleRole(argv[2].upper())
            except ValueError:
                role = None
        hits = SampleRetriever(idx).search_samples(role=role, key=key, top_k=10)
        print(json.dumps({
            "status": "OK", "key": key, "role": role.value if role else None,
            "results": [
                {"filename": h.asset.filename, "key": h.asset.pitch.value,
                 "role": h.asset.semantic_role.value, "path": h.asset.relative_path,
                 "confidence": h.asset.pitch.confidence}
                for h in hits
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    if sub == "retrieve":
        if len(argv) < 2:
            print("uso: sample-library retrieve <ROLE> [BPM]")
            return 2
        idx = load_index(index_path)
        if idx is None:
            print(json.dumps({"status": "EMPTY"}, ensure_ascii=False))
            return 0
        from copilot.sample_library.retrieval import SampleRetriever
        try:
            role = SampleRole(argv[1].upper())
        except ValueError:
            print(json.dumps({"status": "BLOCKED", "error": f"unknown role {argv[1]}"}, ensure_ascii=False))
            return 2
        bpm = float(argv[2]) if len(argv) >= 3 else None
        hits = SampleRetriever(idx).search_samples(role=role, bpm=bpm, top_k=5)
        print(json.dumps({
            "status": "OK", "role": role.value, "bpm": bpm,
            "results": [
                {"filename": h.asset.filename, "role": h.asset.semantic_role.value,
                 "path": h.asset.relative_path, "bpm": h.asset.bpm.value,
                 "dur_s": round(h.asset.descriptors.duration_s, 2) if h.asset.descriptors.duration_s else None,
                 "centroid_hz": h.asset.descriptors.spectral_centroid_hz,
                 "score": round(h.score, 2), "reasons": h.reasons}
                for h in hits
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    if sub == "embed":
        provider_name = argv[1] if len(argv) >= 2 else None
        idx = load_index(index_path)
        if idx is None:
            print(json.dumps({"status": "EMPTY"}, ensure_ascii=False))
            return 0
        from copilot.sample_library.embeddings import EmbeddingProviderUnavailable, get_embedding_provider
        from copilot.sample_library.library_v1 import compute_embeddings, save_index

        try:
            provider = get_embedding_provider(provider_name)
        except (EmbeddingProviderUnavailable, ValueError) as exc:
            print(json.dumps({"status": "BLOCKED", "error": str(exc)}, ensure_ascii=False))
            return 2
        counts = compute_embeddings(idx, provider)
        save_index(idx, index_path)
        print(json.dumps({"status": "OK", **counts}, ensure_ascii=False, indent=2))
        return 0

    print(f"subcommand desconocido: {sub}")
    return 2


def _import_project(evidence: Path, logger, argv: list[str]) -> int:
    from copilot.importing.project_folder_import_v1 import import_project_folder

    folder = " ".join(argv).strip()
    if not folder:
        payload = {
            "status": "BLOCKED",
            "reason": "FOLDER_REQUIRED",
            "detail": 'Usage: python -m copilot.cli import-project "<folder>"',
            "MUSICAL WRITES": 0,
            "NO WRITE": True,
        }
        print(json.dumps(payload, indent=2))
        return 2
    report = import_project_folder(folder, evidence=evidence)
    logger.info("import-project status=%s", report.get("PROJECT_FOLDER_IMPORT_V1"))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("PROJECT_FOLDER_IMPORT_V1") == "READY" else 2


def _capabilities() -> int:
    from copilot.audio.capability_matrix_v1 import capability_matrix
    from copilot.audio.m4l_control_contract_v1 import control_contract

    payload = {
        **capability_matrix(),
        "m4l_control_contract": control_contract(),
        "canonical_commands": list(CANONICAL_COMMANDS),
        "lab_commands_require_flag": sorted(LAB_COMMANDS),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0


def _producer_analyze(
    evidence: Path,
    logger,
    *,
    region_id: str | None,
    start_qn: float | None,
    end_qn: float | None,
) -> int:
    from copilot.audio.producer_analyze_v1 import PRESERVED_STATUSES, producer_analyze

    connected = _connect_live_or_block(evidence, "producer_analyze_v1.json")
    if isinstance(connected, dict):
        print(json.dumps(connected, indent=2, default=str))
        return 2
    try:
        report = producer_analyze(
            connected,
            evidence=evidence,
            region_id=region_id,
            start_qn=start_qn,
            end_qn=end_qn,
        )
    finally:
        connected.disconnect()
    logger.info("producer-analyze status=%s", report.get("status"))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    status = str(report.get("status") or "BLOCKED")
    return 0 if status in PRESERVED_STATUSES else 2


def _producer_run(
    evidence: Path,
    logger,
    *,
    mode: str | None,
    region_id: str | None,
    start_qn: float | None,
    end_qn: float | None,
) -> int:
    from copilot.audio.producer_analyze_v1 import PRESERVED_STATUSES
    from copilot.audio.producer_run_v1 import producer_run

    if mode is None:
        payload = {
            "status": "BLOCKED",
            "reason": "MODE_REQUIRED",
            "detail": "producer-run requires --mode analyze or --mode autonomous",
        }
        print(json.dumps(payload, indent=2))
        return 2
    connected = _connect_live_or_block(evidence, "producer_run_v1.json")
    if isinstance(connected, dict):
        print(json.dumps(connected, indent=2, default=str))
        return 2
    try:
        report = producer_run(
            connected,
            evidence=evidence,
            mode=mode,
            region_id=region_id,
            start_qn=start_qn,
            end_qn=end_qn,
        )
    finally:
        connected.disconnect()
    logger.info("producer-run mode=%s status=%s", mode, report.get("status"))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    status = str(report.get("status") or "BLOCKED")
    return 0 if status in PRESERVED_STATUSES else 2


def _doctor(evidence: Path, logger) -> int:
    from copilot.audio.doctor_v1 import doctor
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.daw.session_ready_v1 import probe_session_ready

    probe = probe_session_ready()
    adapter = None
    if probe.status == SESSION_READY:
        adapter = AbletonTcpAdapter()
        try:
            adapter.connect()
        except Exception:
            adapter = None
    try:
        report = doctor(evidence=evidence, daw=adapter, session_probe=probe)
    finally:
        if adapter is not None:
            try:
                adapter.disconnect()
            except Exception:
                pass
    logger.info("doctor status=%s session=%s", report.get("status"), probe.status)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("status") == "READY" else 2


def _regression_v1(evidence: Path, logger) -> int:
    from copilot.audio.regression_v1 import run_regression_v1

    report = run_regression_v1(evidence=evidence)
    logger.info("regression-v1 overall=%s", report.get("overall"))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("overall") == "PASS" else 2


def _manual_control_surface_action() -> str:
    return (
        "Restart Ableton Live, then Settings → Link, Tempo & MIDI → "
        "Control Surface = AbletonMCP, Input = None, Output = None"
    )


def _track_build(evidence: Path, logger, argv: list[str], live: bool = False, leave: bool = False) -> int:
    """Build a groovy/latin tech house track from 0 (library + recipe + groove + mixing + arrangement)."""
    from copilot.sample_library.library_v1 import load_index
    from copilot.musicplan.tech_house import build_tech_house_plan, TECH_HOUSE_BPM
    from copilot.musicplan.arrangement import (
        TECH_HOUSE_ARRANGEMENT,
        build_arrangement_mute_actions,
    )
    from copilot.musicplan.mixing import MIXING_CHAINS, MASTER_CHAIN, MIXING_PHILOSOPHY

    index_path = evidence / "sample_library_index.json"
    idx = load_index(index_path)
    if idx is None:
        print(json.dumps({"status": "BLOCKED", "error": "no sample index; run 'sample-library index' first"}, ensure_ascii=False))
        return 2

    # build the plan against a blank template (mock) or the real live bridge.
    from copilot.daw.state_tokens import attach_tokens
    if live:
        from copilot.daw.ableton_tcp import AbletonTcpAdapter
        daw = AbletonTcpAdapter(); daw.connect()
    else:
        from copilot.daw.mock import MockAbletonAdapter
        daw = MockAbletonAdapter(); daw.connect()
        daw.session_path = r"D:\sets\trackbuild_lab.als"
        daw.session_name = "trackbuild_lab"
    session = daw.snapshot()
    attach_tokens(session)

    plan = build_tech_house_plan(index=idx, session=session)
    arrangement = build_arrangement_mute_actions(project_identity=session.project_identity)
    plan.actions.extend(arrangement)
    if leave:
        # leave the set in the full-groove (DROP) state: everything active
        drop = [s for s in TECH_HOUSE_ARRANGEMENT if s.name == "DROP"][0]
        plan.actions.extend(
            build_arrangement_mute_actions(project_identity=session.project_identity, arrangement=[drop])
        )

    # ---- print structure ----
    print(f"\n=== GROOVY / LATIN TECH HOUSE — {TECH_HOUSE_BPM} BPM ===\n")
    print("SONIDO (sample por pista, percusión-first):")
    for a in plan.actions:
        if a.action_type.value == "SAMPLE_LOAD":
            print(f"  {a.target.ref.get('name','?'):12s} {a.params.sample_uri}")

    print("\nMIXING (cadenas nativas):")
    for track, devices in MIXING_CHAINS.items():
        print(f"  {track:12s} {' → '.join(devices)}")
    print(f"  {'MASTER':12s} {' → '.join(MASTER_CHAIN)}")

    print("\nARRANGEMENT (substracción/variación):")
    for sec in TECH_HOUSE_ARRANGEMENT:
        muted = 10 - len(sec.active)
        print(f"  {sec.name:8s} {sec.bars:>3d}b  activas: {', '.join(sec.active)}  (mute: {muted})")

    print(f"\nPLAN: {len(plan.actions)} acciones de build + {len(arrangement)} de arreglo")

    # silence write/transaction INFO logs so the structure reads clean
    import logging
    for _name in ("copilot.write", "copilot.transactions", "copilot.agent", "copilot"):
        logging.getLogger(_name).setLevel(logging.WARNING)

    # ---- execute against mock (write + rollback) ----
    from copilot.musicplan.execute import build_agent_tools, execute_track_build_plan
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    tools = build_agent_tools(daw, journal_path=tmp / "journal.jsonl")
    build_report = execute_track_build_plan(tools, plan=plan, session=session, persist_dir=tmp, leave=leave)
    print(f"\nEJECUCIÓN: {build_report['status']}  ·  tracks {build_report['after_track_count']} → rollback {build_report['restored_track_count']}  ·  RESTORE_VERIFIED={build_report['RESTORE_VERIFIED']}")

    result = {
        "status": build_report["status"],
        "style": "groovy latin tech house",
        "bpm": TECH_HOUSE_BPM,
        "plan_actions": len(plan.actions),
        "arrangement_actions": len(arrangement),
        "tracks": [a.params.track_name for a in plan.actions if a.action_type.value == "CREATE_TRACK"],
        "restore_verified": build_report["RESTORE_VERIFIED"],
    }
    return 0 if build_report["status"] == "CONTROLLED_WRITE_LOOP_COMPLETE" else 2


def _vibe(evidence: Path, logger, argv: list[str], live: bool = False, leave: bool = False) -> int:
    """prompt -> Astra -> MusicPlan (vibe coding). Falls back to deterministic if no Astra."""
    from copilot.sample_library.library_v1 import load_index
    from copilot.musicplan.astra_plan import build_plan_from_prompt

    intent = " ".join(argv) if argv else "dark percussive groovy tech house"
    index_path = evidence / "sample_library_index.json"
    idx = load_index(index_path)
    if idx is None:
        print(json.dumps({"status": "BLOCKED", "error": "no sample index; run 'sample-library index' first"}, ensure_ascii=False))
        return 2

    from copilot.daw.state_tokens import attach_tokens
    if live:
        from copilot.daw.ableton_tcp import AbletonTcpAdapter
        daw = AbletonTcpAdapter(); daw.connect()
    else:
        from copilot.daw.mock import MockAbletonAdapter
        daw = MockAbletonAdapter(); daw.connect()
        daw.session_path = r"D:\sets\vibe_lab.als"; daw.session_name = "vibe_lab"
    session = daw.snapshot(); attach_tokens(session)

    plan, meta = build_plan_from_prompt(index=idx, session=session, intent=intent)

    astra_arrangement = meta.get("arrangement")
    plan._astra_arrangement = astra_arrangement
    plan._astra_patch_contracts = meta.get("patch_contracts") or []
    from copilot.musicplan.arrangement import build_arrangement_mute_actions, TECH_HOUSE_ARRANGEMENT
    plan.actions.extend(build_arrangement_mute_actions(project_identity=session.project_identity))
    if leave:
        drop = [s for s in TECH_HOUSE_ARRANGEMENT if s.name == "DROP"][0]
        plan.actions.extend(build_arrangement_mute_actions(project_identity=session.project_identity, arrangement=[drop]))

    print(f'\n=== VIBE: "{intent}" ===\n')
    print(f"astra_used: {meta['astra_used']}")
    print(f"reasoning: {meta.get('reasoning', '')}")
    print("\nSELECCIÓN (sample por pista):")
    for a in plan.actions:
        if a.action_type.value == "SAMPLE_LOAD":
            print(f"  {a.target.ref.get('name','?'):12s} {Path(a.params.sample_uri).name}")

    import logging
    for _n in ("copilot.write", "copilot.transactions", "copilot.agent", "copilot"):
        logging.getLogger(_n).setLevel(logging.WARNING)

    import tempfile
    from copilot.musicplan.execute import build_agent_tools, execute_track_build_plan
    tmp = Path(tempfile.mkdtemp())
    tools = build_agent_tools(daw, journal_path=tmp / "journal.jsonl")
    report = execute_track_build_plan(tools, plan=plan, session=session, persist_dir=tmp, leave=leave)
    if leave:
        print(f"\nEJECUCIÓN: {report['status']} · tracks {report['after_track_count']} · LEAVE (track armado)")
    else:
        print(f"\nEJECUCIÓN: {report['status']} · tracks {report['after_track_count']} → rollback {report['restored_track_count']} · RESTORE_VERIFIED={report['RESTORE_VERIFIED']}")
    ok = report["status"] == "CONTROLLED_WRITE_LOOP_COMPLETE"
    # End-to-end finalization: arrangement timeline + mix/master, before save.
    if ok and leave:
        from copilot.musicplan.arrangement_builder import build_arrangement
        from copilot.musicplan.mix_tweaks import apply_mix
        from copilot.producer.soniq_surface import apply_patch_contract_auto_mode

        final_session = daw.snapshot()

        # ASTRAL planner integration: execute patch contracts before arrangement/mix.
        patch_contracts = getattr(plan, "_astra_patch_contracts", []) or []
        if patch_contracts:
            applied = 0
            failed = 0
            print(f"\nASTRAL PATCH CONTRACTS: {len(patch_contracts)}")
            for c in patch_contracts:
                try:
                    rep = apply_patch_contract_auto_mode(daw, session=daw.snapshot(), contract=c, throttle_ms=40)
                    patch = rep.get("patch", {})
                    if rep.get("ok"):
                        applied += 1
                    else:
                        failed += 1
                    print(
                        f"  - {c.get('track')} / {c.get('device')} -> ok={rep.get('ok')}"
                        f" mode={rep.get('routing_mode')}"
                        f" applied={patch.get('applied')}"
                        f" viol={len(patch.get('violations', []))}"
                    )
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"  - {c.get('track')} / {c.get('device')} -> error ({exc})")
            print(f"ASTRAL PATCH RESULT: applied={applied} failed={failed}")

        arrangement = getattr(plan, "_astra_arrangement", None) or None
        arr = build_arrangement(daw, session=final_session, arrangement=arrangement)
        print(f"\nARREGLO: {arr['placed']} clips · {arr['looped']} loops · {len(arr['errors'])} errores")
        for e in arr["errors"][:6]:
            print(f"  ! {e}")
        mix = apply_mix(daw, session=final_session)
        print(f"MIX/MASTER: {mix['volumes']} volúmenes · {mix['master_devices']} dispositivos master · {mix['master_tweaks']} tweaks · {len(mix['errors'])} errores")
        for e in mix["errors"][:6]:
            print(f"  ! {e}")
    # Post-build: persist the set, then run the structured critique.
    if ok and leave:
        try:
            saved = daw.save_session()
            if saved.get("saved"):
                print(f"\nGUARDADO: {saved.get('path') or 'Sin título'}")
            else:
                print("\nGUARDADO: el LOM de Ableton no expone save — guardá con Cmd+S en Live")
        except Exception as exc:  # noqa: BLE001
            print(f"\nGUARDADO: error ({exc})")

        from copilot.musicplan.critique import critique_track

        after = daw.snapshot()
        critique = critique_track(plan=plan, session=after)
        if critique is not None:
            print(f"\nCRÍTICA: {critique.verdict.upper()}")
            for i in critique.top_3_issues:
                print(f"  {i.priority}. [{i.area}] {i.issue} → {i.minimal_fix}")
            if critique.reasoning:
                print(f"  ({critique.reasoning})")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

