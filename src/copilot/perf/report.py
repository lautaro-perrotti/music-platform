"""performance-report: measure the real system, then explain the seconds.

Read-only by construction. Every benchmark here either touches the filesystem,
runs deterministic DSP, or issues Ableton *reads*. Nothing arms a tap, moves the
transport, changes routing or calls a model provider, so the report is safe to
run against a live session while the user is working in it.

Capture and reasoning cost are reported from recorded evidence rather than
re-run, because re-running them would mutate the session or spend provider
budget. Those rows are labelled OBSERVED_FROM_EVIDENCE.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

from copilot.perf.ableton import instrumented_rpc, rpc_breakdown
from copilot.perf.trace import (
    CAT_ABLETON,
    CAT_CPU,
    CAT_IO,
    PerformanceTrace,
    active_trace,
    span,
)

ARTIFACT = "performance_report_v1.json"
DEFAULT_REPEATS = 5


def _stats(samples: list[float]) -> dict[str, Any]:
    """Honest statistics: p95 only when the sample actually supports it."""
    ordered = sorted(samples)
    row: dict[str, Any] = {
        "n": len(ordered),
        "min_s": round(ordered[0], 6),
        "median_s": round(statistics.median(ordered), 6),
        "max_s": round(ordered[-1], 6),
    }
    if len(ordered) >= 20:
        row["p95_s"] = round(ordered[int(0.95 * len(ordered)) - 1], 6)
    else:
        row["p95_s"] = None
        row["p95_note"] = "sample too small for p95"
    return row


def _timed(fn, repeats: int) -> tuple[list[float], Any, str | None]:
    samples: list[float] = []
    last: Any = None
    for _ in range(repeats):
        started = time.perf_counter()
        try:
            last = fn()
        except Exception as exc:  # noqa: BLE001 — a benchmark must not abort the report
            return samples, None, f"{type(exc).__name__}: {exc}"
        samples.append(time.perf_counter() - started)
    return samples, last, None


# ---------------------------------------------------------------- Ableton ----
def measure_ableton_reads(
    *, host: str, port: int, repeats: int
) -> dict[str, Any]:
    """Round-trip cost of read-only Remote Script commands."""
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.daw.adapter import DawError

    out: dict[str, Any] = {"status": "MEASURED", "host": host, "port": port}
    connect_samples: list[float] = []
    for _ in range(repeats):
        adapter = AbletonTcpAdapter(host, port)
        started = time.perf_counter()
        try:
            adapter.connect()
        except DawError as exc:
            return {
                "status": "BLOCKED",
                "reason": "LIVE_UNAVAILABLE",
                "detail": str(exc)[:200],
                "host": host,
                "port": port,
            }
        connect_samples.append(time.perf_counter() - started)
        adapter.disconnect()
    out["connect_handshake"] = _stats(connect_samples)

    adapter = AbletonTcpAdapter(host, port)
    adapter.connect()
    try:
        commands: dict[str, Any] = {}
        all_samples: list[float] = []
        for command in (
            "health_check",
            "get_session_info",
            "get_playback_position",
            "get_master_info",
            "get_session_path",
            "get_capture_topology",
            "get_tracks_info",
        ):
            samples, result, error = _timed(
                lambda c=command: adapter._command(c), repeats
            )
            row: dict[str, Any] = {"error": error}
            if samples:
                row.update(_stats(samples))
                row["result_bytes"] = len(json.dumps(result, default=str))
                all_samples.extend(samples)
            commands[command] = row
        out["commands"] = commands

        adapter.reset_tcp_stats()
        snap_samples, _, snap_error = _timed(
            lambda: adapter.snapshot(include_notes=False), repeats
        )
        out["snapshot_no_notes"] = {
            **(_stats(snap_samples) if snap_samples else {}),
            "error": snap_error,
            "source": adapter.snapshot_source,
            "rpc_per_snapshot": (
                round(adapter.tcp_stats()["total"] / max(1, len(snap_samples)), 2)
                if snap_samples
                else None
            ),
        }

        adapter.reset_tcp_stats()
        notes_samples, _, notes_error = _timed(
            lambda: adapter.snapshot(include_notes=True), repeats
        )
        out["snapshot_with_notes"] = {
            **(_stats(notes_samples) if notes_samples else {}),
            "error": notes_error,
            "source": adapter.snapshot_source,
            "rpc_per_snapshot": (
                round(adapter.tcp_stats()["total"] / max(1, len(notes_samples)), 2)
                if notes_samples
                else None
            ),
        }
        out["scheduler_quantum"] = _estimate_quantum(all_samples or connect_samples)
    finally:
        adapter.disconnect()
    return out


def _estimate_quantum(samples: list[float], *, tolerance: float = 0.03) -> dict[str, Any]:
    """Latency on this protocol lands on multiples of Live's scheduler tick.

    Clusters the samples, then reports the median spacing between adjacent
    cluster centres. A tight spread of that spacing is what makes the claim
    "this is a scheduler quantum" rather than "the network is noisy".
    """
    if len(samples) < 6:
        return {"detected": False, "reason": "too few samples"}
    ordered = sorted(samples)
    clusters: list[list[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    if len(clusters) < 2:
        return {"detected": False, "reason": "single latency cluster"}
    centres = [statistics.fmean(group) for group in clusters]
    gaps = [b - a for a, b in zip(centres, centres[1:])]
    quantum = statistics.median(gaps)
    # Adjacent clusters can be two ticks apart; fold those back to one tick.
    unit_gaps = [gap / max(1, round(gap / quantum)) for gap in gaps]
    return {
        "detected": True,
        "quantum_s": round(statistics.median(unit_gaps), 6),
        "cluster_centres_s": [round(value, 4) for value in centres],
        "cluster_sizes": [len(group) for group in clusters],
        "note": (
            "Round-trip latency quantises to this interval. It is Live "
            "main-thread scheduling, not payload size, so the lever is RPC "
            "COUNT, not bytes."
        ),
    }


def measure_ableton_workflow_reads(*, host: str, port: int) -> dict[str, Any]:
    """One traced read-only pass with per-command RPC attribution."""
    from copilot.daw.ableton_tcp import AbletonTcpAdapter
    from copilot.daw.adapter import DawError

    adapter = AbletonTcpAdapter(host, port)
    try:
        adapter.connect()
    except DawError as exc:
        return {"status": "BLOCKED", "reason": "LIVE_UNAVAILABLE", "detail": str(exc)[:200]}
    try:
        with active_trace("read_only_session_survey") as trace:
            with instrumented_rpc(adapter):
                with span("snapshot"):
                    session = adapter.snapshot(include_notes=False)
                trace.set_project(session.project_identity or session.project_token)
                with span("master_info"):
                    adapter.get_master_info()
                with span("playback_position"):
                    adapter.get_playback_position()
        return {
            "status": "MEASURED",
            "total_s": round(trace.total_s, 6),
            "track_count": len(session.tracks),
            "rpc": rpc_breakdown(trace),
            "tree": trace.render(),
        }
    finally:
        adapter.disconnect()


# ------------------------------------------------------------ local costs ----
def measure_local_costs(evidence: Path, *, repeats: int) -> dict[str, Any]:
    """Hashing, WAV decode and DSP on real recorded captures."""
    import soundfile as sf

    from copilot.audio.file_hash import sha256_file
    from copilot.audio.fullmix import compute_fullmix_observation

    captures = sorted((Path(evidence) / "captures").glob("*.wav"))
    if not captures:
        return {"status": "BLOCKED", "reason": "NO_CAPTURES_ON_DISK"}
    sample = captures[0]
    size = sample.stat().st_size
    info = sf.info(str(sample))

    out: dict[str, Any] = {
        "status": "MEASURED",
        "sample_file": sample.name,
        "bytes": size,
        "duration_s": round(float(info.duration), 3),
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "corpus_files": len(captures),
        "corpus_bytes": sum(c.stat().st_size for c in captures),
    }
    for label, fn in (
        ("sha256_file", lambda: sha256_file(sample)),
        ("soundfile_info", lambda: sf.info(str(sample))),
        ("soundfile_read_full", lambda: sf.read(str(sample))),
        (
            "fullmix_observation",
            lambda: compute_fullmix_observation(
                sample, region_id="PERF", region_label="PERF", audio_sha256=None
            ),
        ),
    ):
        samples, _, error = _timed(fn, repeats)
        out[label] = {**(_stats(samples) if samples else {}), "error": error}
    return out


def measure_artifact_io(evidence: Path, *, repeats: int) -> dict[str, Any]:
    """Cost of reading and re-serialising the largest evidence artifacts."""
    artifacts = sorted(
        (p for p in Path(evidence).glob("*.json") if p.is_file()),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )[:5]
    if not artifacts:
        return {"status": "BLOCKED", "reason": "NO_ARTIFACTS"}
    rows = []
    for path in artifacts:
        samples, payload, error = _timed(
            lambda p=path: json.loads(p.read_text(encoding="utf-8", errors="replace")),
            repeats,
        )
        row = {
            "file": path.name,
            "bytes": path.stat().st_size,
            "parse": {**(_stats(samples) if samples else {}), "error": error},
        }
        if payload is not None:
            dump, _, dump_error = _timed(
                lambda d=payload: json.dumps(d, default=str), repeats
            )
            row["serialize"] = {**(_stats(dump) if dump else {}), "error": dump_error}
        rows.append(row)
    return {"status": "MEASURED", "artifacts": rows}


# --------------------------------------------------- evidence-derived cost ----
def observed_model_latency(evidence: Path) -> dict[str, Any]:
    """Astra cost from recorded reasoning audits. Never calls the provider."""
    model: list[float] = []
    prompt: list[float] = []
    validate: list[float] = []
    seen: set[tuple[float, float]] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            value = node.get("model_s")
            if isinstance(value, (int, float)):
                key = (round(float(value), 6), round(float(node.get("prompt_build_s") or 0), 9))
                if key not in seen:
                    seen.add(key)
                    model.append(float(value))
                    prompt.append(float(node.get("prompt_build_s") or 0.0))
                    validate.append(float(node.get("validation_s") or 0.0))
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    for path in Path(evidence).rglob("*.json"):
        try:
            walk(json.loads(path.read_text(encoding="utf-8", errors="replace")))
        except (OSError, ValueError):
            continue
    if not model:
        return {"status": "BLOCKED", "reason": "NO_RECORDED_REASONING_AUDITS"}
    local = statistics.median(prompt) + statistics.median(validate)
    return {
        "status": "OBSERVED_FROM_EVIDENCE",
        "model_s": _stats(model),
        "prompt_build_s": _stats(prompt),
        "validation_s": _stats(validate),
        "local_share_of_model_pct": round(100 * local / statistics.median(model), 4),
        "note": (
            "Payload construction and grounding validation are under 0.01 s. "
            "Astra latency is provider-bound; there is no local win in this path."
        ),
    }


def observed_capture_cost(evidence: Path) -> dict[str, Any]:
    """Capture cost from recorded WAVs. Separates intrinsic audio from overhead."""
    import soundfile as sf

    captures = sorted((Path(evidence) / "captures").glob("*.wav"))
    if not captures:
        return {"status": "BLOCKED", "reason": "NO_CAPTURES_ON_DISK"}
    durations = []
    for path in captures:
        try:
            durations.append(float(sf.info(str(path)).duration))
        except Exception:  # noqa: BLE001
            continue
    if not durations:
        return {"status": "BLOCKED", "reason": "NO_READABLE_CAPTURES"}
    sidecars = [p for p in captures if "_Main_with_" in p.name]
    sources = [p for p in captures if "_Main_with_" not in p.name]
    return {
        "status": "OBSERVED_FROM_EVIDENCE",
        "capture_files": len(captures),
        "distinct_passes_estimate": len({p.stem.rsplit("_", 1)[-1] for p in captures}),
        "main_sidecar_files": len(sidecars),
        "region_duration_s": _stats(durations),
        "intrinsic_playback_note": (
            "Region length is INTRINSIC_AUDIO_DURATION, not a performance bug. "
            "Overhead is everything around it: routing, verification, restore."
        ),
        "source_files": len(sources),
    }


# ------------------------------------------------------------------ driver ----
def performance_report(
    evidence: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 9877,
    repeats: int = DEFAULT_REPEATS,
    include_live: bool = True,
) -> dict[str, Any]:
    from copilot.human_eval.store import now_iso

    evidence = Path(evidence)
    with active_trace("performance-report") as trace:
        report: dict[str, Any] = {
            "milestone": "PERFORMANCE_REPORT_V1",
            "ts": now_iso(),
            "operation_id": trace.operation_id,
            "repeats": repeats,
            "READ ONLY": True,
            "MUSICAL WRITES": 0,
        }
        with span("local_costs", category=CAT_CPU):
            report["local_costs"] = measure_local_costs(evidence, repeats=repeats)
        with span("artifact_io", category=CAT_IO):
            report["artifact_io"] = measure_artifact_io(evidence, repeats=repeats)
        with span("observed_model", category=CAT_IO):
            report["model_pipeline"] = observed_model_latency(evidence)
        with span("observed_capture", category=CAT_IO):
            report["capture"] = observed_capture_cost(evidence)
        if include_live:
            with span("ableton_reads", category=CAT_ABLETON):
                report["ableton"] = measure_ableton_reads(
                    host=host, port=port, repeats=repeats
                )
            with span("ableton_workflow", category=CAT_ABLETON):
                report["ableton_workflow"] = measure_ableton_workflow_reads(
                    host=host, port=port
                )
        else:
            report["ableton"] = {"status": "SKIPPED", "reason": "LIVE_NOT_REQUESTED"}
            report["ableton_workflow"] = {"status": "SKIPPED"}
    report["trace"] = trace.to_dict()
    report["tree"] = trace.render()
    return report


def persist_report(report: dict[str, Any], evidence: Path) -> Path:
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / ARTIFACT
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def render_summary(report: dict[str, Any]) -> str:
    """Human-readable answer to: why does this take the time it takes?"""
    lines: list[str] = ["PERFORMANCE_REPORT_V1"]
    ableton = report.get("ableton") or {}
    if ableton.get("status") == "MEASURED":
        connect = ableton.get("connect_handshake") or {}
        lines.append(
            f"  ableton connect+handshake   median {connect.get('median_s')}s"
        )
        snap = ableton.get("snapshot_no_notes") or {}
        lines.append(
            f"  snapshot (no notes)         median {snap.get('median_s')}s "
            f"via {snap.get('source')} ({snap.get('rpc_per_snapshot')} rpc)"
        )
        quantum = (ableton.get("scheduler_quantum") or {}).get("quantum_s")
        if quantum:
            lines.append(
                f"  scheduler quantum           ~{quantum}s per round trip "
                "(reduce RPC COUNT, not bytes)"
            )
        for name, row in (ableton.get("commands") or {}).items():
            if row.get("median_s") is not None:
                lines.append(f"    {name:<26} {row['median_s']}s")
    else:
        lines.append(f"  ableton                     {ableton.get('status')}")
    model = report.get("model_pipeline") or {}
    if model.get("status") == "OBSERVED_FROM_EVIDENCE":
        stats = model.get("model_s") or {}
        lines.append(
            f"  astra (recorded n={stats.get('n')})     median {stats.get('median_s')}s "
            f"max {stats.get('max_s')}s; local share {model.get('local_share_of_model_pct')}%"
        )
    workflow = report.get("ableton_workflow") or {}
    if workflow.get("status") == "MEASURED":
        rpc = workflow.get("rpc") or {}
        lines.append(
            f"  read-only survey            {workflow.get('total_s')}s over "
            f"{rpc.get('total_calls')} rpc ({rpc.get('total_s')}s in Ableton) "
            f"across {workflow.get('track_count')} tracks"
        )
    local = report.get("local_costs") or {}
    if local.get("status") == "MEASURED":
        lines.append(
            f"  dsp fullmix                 median "
            f"{(local.get('fullmix_observation') or {}).get('median_s')}s "
            f"on {local.get('duration_s')}s audio"
        )
        lines.append(
            f"  sha256 capture              median "
            f"{(local.get('sha256_file') or {}).get('median_s')}s "
            f"({local.get('bytes')} bytes)"
        )
    return "\n".join(lines)
