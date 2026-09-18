"""SOURCE_CAPTURE_BATCH_V1 — several Post Mixer sources in ONE playback pass.

Why this exists
---------------
A traced ``producer-analyze`` on the Groove Rider working copy measured 254.2 s
for four sources: 237 Ableton round trips (110.7 s), 91.4 s of real-time
playback and a 49.6 s Astra call. Each source got its own complete playback
pass, so the region *and* its 16 qn pre-roll were played once per source.

``capture_parallel_pass`` already records several taps in one transport start,
and ``live3r_trust`` evidence shows three simultaneous tap files with
``global_file_crosstalk = false``. The only reason the caller ran one pass per
source is ``source_capture_pool_v1.MAX_PARALLEL = 1``.

Capacity
--------
TapProtocol 3 keeps the legacy ceiling: slot 0 Main + slots 1-2 sources.
TapProtocol 4 discovers up to 8 source slots. Pass width comes from
``discover_capacity``, not a hardcoded Groove Rider host count.

Safety contract (unchanged from the single-source path)
-------------------------------------------------------
* every host is snapshotted before any routing write and restored afterwards;
* ``OFF_MIX_GRAPH`` / ``OFF_DIRECT_MAIN`` is verified per host before recording,
  and ``through_main`` is a hard failure — no source may leak into Main;
* one source failing never reports success for another;
* if any host cannot be restored the whole batch fails closed;
* cancellation stops the transport, restores every host and settles every
  journal;
* one transport owner: ``capture_parallel_pass`` starts and stops playback once.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from copilot.audio.batch_capture import (
    CAPTURE_BASS,
    CAPTURE_HOST,
    capture_parallel_pass,
    route_host_post_mixer,
)
from copilot.audio.live_capture import (
    STAGING_BASS,
    STAGING_KICK,
    AudioCaptureError,
    MASTER_INDEX,
    find_tap,
    set_tap_enabled,
    set_tap_recording,
)
from copilot.audio.source_audio_trace import (
    _apply_host_restore,
    _host_snapshot_from_state,
    _restore_host_full,
    _restore_matches,
    _silence_sends,
    _snapshot_host,
    _snapshot_hosts,
    _verify_off_mix_graph,
)
from copilot.audio.capture_scalability_v2 import (
    LEGACY_SOURCE_CAPACITY,
    discover_capacity,
    host_specs_for_capacity,
    plan_source_passes,
    staging_for_slot,
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
)
from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.mutation_protocol import (
    BatchStatus,
    compound_available,
    compile_prepare_hosts,
    compile_restore_hosts,
    execute_mutation_plan,
    journal_fields,
    prepare_compile_ready,
)
from copilot.daw.object_ref import ResolveStatus, ref_from_track, resolve_track
from copilot.daw.state_tokens import attach_tokens
from copilot.perf.trace import CAT_ABLETON, CAT_CPU, CAT_INTRINSIC, CAT_IO, span
from copilot.runtime.host_state import read_capture_hosts_state
from copilot.schemas.observation import SignalPoint
from copilot.schemas.session import SessionState, TrackState

MILESTONE = "SOURCE_CAPTURE_BATCH_V1"

# Legacy two-host bank. Runtime planning uses discover_capacity + host_specs.
BATCH_HOSTS: tuple[dict[str, Any], ...] = (
    {"name": CAPTURE_HOST, "slot": 1, "staging": STAGING_KICK, "key": "kick"},
    {"name": CAPTURE_BASS, "slot": 2, "staging": STAGING_BASS, "key": "bass"},
)
MAX_SOURCES_PER_PASS = LEGACY_SOURCE_CAPACITY

ACCEPTED_CLAIMS = frozenset({"OFF_MIX_GRAPH", "OFF_DIRECT_MAIN"})


def available_hosts(
    session: SessionState,
    capacity: Any | None = None,
    ready_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Batch hosts present in this project, in slot order.

    When ``ready_names`` is provided, only HOST_AVAILABLE parked hosts are used.
    """
    from copilot.audio.capture_scalability_v2 import slot_for_host_name

    cap = capacity or discover_capacity(session=session, ready_hosts=ready_names)
    found: list[dict[str, Any]] = []
    legacy = {int(row["slot"]): row for row in BATCH_HOSTS}
    allowed = None if ready_names is None else set(ready_names)
    for track in session.tracks:
        slot = slot_for_host_name(str(track.name))
        if slot is None:
            continue
        if allowed is not None and track.name not in allowed:
            continue
        spec = legacy.get(slot) or {
            "name": track.name,
            "slot": slot,
            "staging": staging_for_slot(slot),
            "key": f"s{slot}",
        }
        found.append({**spec, "name": track.name, "index": int(track.index)})
    found.sort(key=lambda row: int(row["slot"]))
    if capacity is None:
        return found
    width = max(1, int(cap.max_concurrent_sources or MAX_SOURCES_PER_PASS))
    return found[:width]


def plan_batches(
    targets: list[Any],
    session: SessionState,
    capacity: Any | None = None,
    ready_names: list[str] | None = None,
) -> list[list[Any]]:
    """Split targets into groups that can share one playback pass.

    Width is discovered capacity, then bounded by HOST_AVAILABLE hosts.
    A one-host project degrades to one source per pass rather than failing.
    """
    cap = capacity or discover_capacity(session=session, ready_hosts=ready_names)
    hosts = available_hosts(session, cap, ready_names=ready_names)
    return plan_source_passes(
        targets,
        capacity=cap,
        available_host_count=len(hosts) if hosts else 1,
    )


def _require_parked_baselines(
    daw: AbletonTcpAdapter, hosts: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Baseline is the verified parked state. Arbitrary current state is not a baseline."""
    from copilot.audio.capture_host_baseline_v1 import (
        evaluate_parked,
        parked_restore_baseline,
    )

    indices = [int(host["index"]) for host in hosts]
    with span("capture.verify_parked_hosts", category=CAT_ABLETON, hosts=len(indices)):
        packed = read_capture_hosts_state(daw, indices, fresh=True)
    baselines: dict[int, dict[str, Any]] = {}
    for host in hosts:
        host_index = int(host["index"])
        state = packed.get(host_index)
        snap = _host_snapshot_from_state(state) if state else None
        verdict = evaluate_parked(
            snap or {},
            expected_slot=int(host["slot"]),
        )
        if snap is None or not verdict.get("ok"):
            raise AudioCaptureError(
                "HOST_NOT_READY",
                f"{host['name']} is not in canonical parked state: "
                f"{(verdict or {}).get('mismatches')}",
            )
        baseline = parked_restore_baseline()
        baseline["sends"] = list(snap.get("sends") or [])
        baseline["info"] = snap.get("info")
        baseline["taps"] = snap.get("taps")
        baselines[host_index] = baseline
    return baselines


def _host_plan_row(host: dict[str, Any], track: TrackState) -> dict[str, Any]:
    return {**host, "target_name": track.name, "target": track.name, "host_id": str(host["name"])}


def _apply_compound_prepare(
    daw: AbletonTcpAdapter,
    session: SessionState,
    prepared: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    project_identity: str,
) -> None:
    hosts = [_host_plan_row(item["host"], item["track"]) for item in prepared]
    baselines = {int(item["host"]["index"]): item["before"] for item in prepared}
    batch = compile_prepare_hosts(
        hosts,
        inventory=inventory,
        baselines=baselines,
        project_identity=project_identity,
        expected_state_tokens={
            "PROJECT_STATE_TOKEN": session.project_token or "",
            "AUDIBLE_STATE_TOKEN": session.audible_token or "",
        },
        expected_project_path=str(session.project_path or ""),
        expected_project_name=str(session.project_name or ""),
    )
    batch.prestate_persisted = True
    batch.journals_prepared = True
    with span(
        "capture.prepare_hosts_compound",
        category=CAT_ABLETON,
        hosts=len(hosts),
        steps=len(batch.steps),
    ):
        result = execute_mutation_plan(daw, batch, session=session)
    for item in prepared:
        item["mutation_prepare"] = result.to_dict()
        item["routing_mutations"].append(f"compound:{result.batch_id}:{result.transport}")
    if result.batch_status is BatchStatus.IN_DOUBT:
        raise AudioCaptureError("IN_DOUBT", "compound prepare response untrusted")
    if result.batch_status is BatchStatus.FAILED_BEFORE_EXECUTION:
        raise AudioCaptureError(
            str(result.error or "FAILED_BEFORE_EXECUTION"),
            "compound prepare did not execute",
        )
    if result.batch_status is not BatchStatus.COMPLETE:
        raise AudioCaptureError(
            "PARTIAL_FAILURE",
            f"compound prepare {result.batch_status.value} failed={result.failed_step_ids()}",
        )
    indices = [int(item["host"]["index"]) for item in prepared]
    with span("capture.prepare_verify_bulk", category=CAT_ABLETON, hosts=len(indices)):
        packed = read_capture_hosts_state(daw, indices, fresh=True)
    for item in prepared:
        host_index = int(item["host"]["index"])
        state = packed.get(host_index)
        snap = _host_snapshot_from_state(state) if state else None
        claim = _verify_off_mix_graph(
            daw, host_index, item["track"].name, state=snap
        )
        if claim.get("claim") not in ACCEPTED_CLAIMS:
            raise AudioCaptureError(
                "CAPTURE_ROUTING_UNSUPPORTED",
                f"host={item['host']['name']} claim={claim.get('claim')} detail={claim}",
            )
        if claim.get("through_main"):
            raise AudioCaptureError(
                "AUDIBLE_MIX_RISK",
                f"{item['host']['name']} still routes through Main while tapping {item['track'].name}",
            )
        item["claim"] = claim
        item["input_channel"] = str(
            claim.get("input_channel")
            or ((snap or {}).get("input") or {}).get("input_routing_channel")
            or ""
        )
        item["journal"].record(
            RECORDING,
            claim=claim.get("claim"),
            channel=item["input_channel"],
            routing_claim=claim.get("claim"),
            **journal_fields(result, str(item["host"]["name"])),
        )


def _restore_all_compound(
    daw: AbletonTcpAdapter, prepared: list[dict[str, Any]]
) -> dict[str, Any]:
    hosts = [item["host"] for item in prepared]
    baselines = {int(item["host"]["index"]): item["before"] for item in prepared}
    identity = ""
    path = ""
    name = ""
    batch = compile_restore_hosts(
        hosts,
        baselines=baselines,
        project_identity=identity,
        expected_project_path=path,
        expected_project_name=name,
    )
    batch.prestate_persisted = True
    batch.journals_prepared = True
    with span(
        "capture.restore_hosts_compound",
        category=CAT_ABLETON,
        hosts=len(hosts),
        steps=len(batch.steps),
    ):
        result = execute_mutation_plan(daw, batch)
    if result.batch_status is BatchStatus.IN_DOUBT:
        # Do not resend the compound batch. Sequential restore from durable baseline.
        return None  # type: ignore[return-value]
    indices = [int(item["host"]["index"]) for item in prepared]
    after_states: dict[int, dict[str, Any]] = {}
    try:
        with span("capture.restore_verify_bulk", category=CAT_ABLETON):
            packed = read_capture_hosts_state(daw, indices, fresh=True)
        after_states = {
            idx: _host_snapshot_from_state(state) for idx, state in packed.items()
        }
    except Exception:
        after_states = {}
    results: dict[str, Any] = {}
    ok = True
    by_host = {row.host_id: [] for row in result.step_results}
    for row in result.step_results:
        by_host.setdefault(row.host_id, []).append(row)
    for item in prepared:
        host = item["host"]
        host_index = int(host["index"])
        host_name = str(host["name"])
        after = after_states.get(host_index)
        errors: list[str] = []
        host_steps = by_host.get(host_name) or []
        if any(step.status.value == "FAILED" for step in host_steps):
            errors.append("compound restore step failed")
        if after is None:
            try:
                after = _snapshot_host(daw, host_index, fresh=True)
            except Exception as exc:  # noqa: BLE001
                results[host_name] = {"ok": False, "errors": errors + [f"verify raised: {exc}"]}
                ok = False
                continue
        matched = _restore_matches(item["before"], after, errors)
        from copilot.audio.capture_host_baseline_v1 import evaluate_parked

        parked = evaluate_parked(after, expected_slot=host.get("slot"))
        if not parked.get("ok"):
            errors = list(errors) + ["RESTORE_VERIFICATION_FAILED"]
            matched = False
        if not matched:
            errors = errors + (["RESTORE_VERIFICATION_FAILED"] if not errors else [])
        outcome = {
            "ok": matched,
            "errors": errors if matched else (errors or ["RESTORE_VERIFICATION_FAILED"]),
            "before_input": item["before"].get("input"),
            "after_input": after.get("input"),
            "before_output": item["before"].get("output"),
            "after_output": after.get("output"),
            "mutation_batch_id": result.batch_id,
            "mutation_transport": result.transport,
            "parked": parked,
        }
        results[host_name] = outcome
        if not outcome.get("ok"):
            ok = False
        item["mutation_restore"] = journal_fields(result, host_name)
    return {"ok": ok, "hosts": results, "mutation": result.to_dict()}


def _main_recorder(daw: AbletonTcpAdapter, inventory: list[dict[str, Any]]) -> dict[str, Any]:
    from copilot.audio.live_capture import STAGING_NAME
    from copilot.schemas.observation import CaptureView, ObservationSource

    by_track = {int(row["track_index"]): row for row in inventory}
    main_tap = by_track.get(MASTER_INDEX) or {}
    device_index = main_tap.get("device_index")
    if device_index is None:
        found = find_tap(daw, MASTER_INDEX)
        if found is None:
            raise AudioCaptureError("TAP_MISSING", "Master missing Copilot Audio Tap")
        device_index = int(found["index"])
    return {
        "key": "master",
        "tap_track_index": MASTER_INDEX,
        "staging": STAGING_NAME,
        "slot": 0,
        "source": "MASTER",
        "source_type": "MASTER",
        "capture_view": CaptureView.MASTER_CONTEXT,
        "observation_source": ObservationSource.MASTER_CONTEXT,
        "signal_point": SignalPoint.MAIN_FINAL,
        "signal_point_label": "Main final",
        "routing": "MAIN",
        "returns_included": True,
        "require_signal": True,
        "device_index": int(device_index),
        "rec_param_index": main_tap.get("rec_param_index"),
    }


def _source_recorder(
    daw: AbletonTcpAdapter,
    inventory: list[dict[str, Any]],
    *,
    host: dict[str, Any],
    target_name: str,
    input_channel: str,
) -> dict[str, Any]:
    from copilot.schemas.observation import CaptureView, ObservationSource

    by_track = {int(row["track_index"]): row for row in inventory}
    host_index = int(host["index"])
    host_tap = by_track.get(host_index) or {}
    device_index = host_tap.get("device_index")
    if device_index is None:
        found = find_tap(daw, host_index)
        if found is None:
            raise AudioCaptureError(
                "TAP_MISSING", f"{host['name']} missing Copilot Audio Tap"
            )
        device_index = int(found["index"])
    return {
        "key": str(host["key"]),
        "tap_track_index": host_index,
        "staging": str(host["staging"]),
        "slot": int(host["slot"]),
        "source": target_name,
        "source_type": "TRACK",
        "capture_view": CaptureView.TRACK_ISOLATED,
        "observation_source": ObservationSource.TRACK_ISOLATED,
        "signal_point": SignalPoint.TRACK_POST_MIXER,
        "signal_point_label": f"{target_name} → {input_channel or 'Post Mixer'}",
        "routing": "OFF_MAIN",
        "returns_included": False,
        "require_signal": True,
        "device_index": int(device_index),
        "rec_param_index": host_tap.get("rec_param_index"),
    }


def _restore_all(
    daw: AbletonTcpAdapter, prepared: list[dict[str, Any]]
) -> dict[str, Any]:
    """Restore every touched host. Reports per host; never raises."""
    if compound_available(daw) and prepared:
        try:
            compound = _restore_all_compound(daw, prepared)
        except Exception:  # noqa: BLE001 — sequential restore remains authority
            compound = None
        if compound is not None:
            return compound
    if not hasattr(daw, "get_tracks_info") and not hasattr(daw, "get_capture_hosts_state"):
        results: dict[str, Any] = {}
        ok = True
        for item in prepared:
            host = item["host"]
            try:
                with span(
                    "capture.restore_host", category=CAT_ABLETON, host=int(host["index"])
                ):
                    outcome = _restore_host_full(daw, int(host["index"]), item["before"])
            except Exception as exc:  # noqa: BLE001 — record, keep restoring others
                outcome = {"ok": False, "errors": [f"restore raised: {exc}"]}
            results[str(host["name"])] = outcome
            if not outcome.get("ok"):
                ok = False
        return {"ok": ok, "hosts": results}
    results: dict[str, Any] = {}
    ok = True
    errors_by_index: dict[int, list[str]] = {}
    for item in prepared:
        host = item["host"]
        host_index = int(host["index"])
        try:
            with span(
                "capture.restore_host", category=CAT_ABLETON, host=host_index
            ):
                errors_by_index[host_index] = _apply_host_restore(
                    daw, host_index, item["before"]
                )
        except Exception as exc:  # noqa: BLE001 — record, keep restoring others
            errors_by_index[host_index] = [f"restore raised: {exc}"]
    indices = [int(item["host"]["index"]) for item in prepared]
    after_states: dict[int, dict[str, Any]] = {}
    try:
        with span("capture.restore_verify_bulk", category=CAT_ABLETON):
            packed = read_capture_hosts_state(daw, indices, fresh=True)
        after_states = {
            idx: _host_snapshot_from_state(state) for idx, state in packed.items()
        }
    except Exception:
        after_states = {}
    for item in prepared:
        host = item["host"]
        host_index = int(host["index"])
        errors = list(errors_by_index.get(host_index) or [])
        after = after_states.get(host_index)
        if after is None:
            try:
                after = _snapshot_host(daw, host_index, fresh=True)
            except Exception as exc:  # noqa: BLE001
                outcome = {"ok": False, "errors": errors + [f"verify raised: {exc}"]}
                results[str(host["name"])] = outcome
                ok = False
                continue
        matched = _restore_matches(item["before"], after, errors)
        from copilot.audio.capture_host_baseline_v1 import evaluate_parked

        parked = evaluate_parked(after, expected_slot=host.get("slot"))
        if not parked.get("ok"):
            errors.append("RESTORE_VERIFICATION_FAILED")
            matched = False
        outcome = {
            "ok": matched,
            "errors": errors if matched else (errors or ["RESTORE_VERIFICATION_FAILED"]),
            "before_input": item["before"].get("input"),
            "after_input": after.get("input"),
            "before_output": item["before"].get("output"),
            "after_output": after.get("output"),
            "parked": parked,
        }
        results[str(host["name"])] = outcome
        if not outcome.get("ok"):
            ok = False
    return {"ok": ok, "hosts": results}


def _cancel_cleanup_batch(
    daw: AbletonTcpAdapter, prepared: list[dict[str, Any]]
) -> None:
    """Terminal cleanup for a cancelled batch. Never raises."""
    try:
        daw.stop_playback()
    except BaseException:  # noqa: BLE001 — cleanup must not mask the cancel
        pass
    for item in prepared:
        try:
            _restore_host_full(daw, int(item["host"]["index"]), item["before"])
        except BaseException:  # noqa: BLE001
            pass
        try:
            # FAILED is already terminal, so recovery semantics are unchanged.
            item["journal"].record(FAILED, error="CANCELLED_BY_USER")
        except BaseException:  # noqa: BLE001
            pass


def capture_sources_post_mixer_batch(
    daw: AbletonTcpAdapter,
    *,
    session: SessionState,
    preflight: dict[str, Any],
    tracks: list[TrackState],
    start_qn: float,
    end_qn: float,
    region_id: str,
    tempo: float,
    dest_root: Path,
    ready_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Capture up to ``MAX_SOURCES_PER_PASS`` sources in one playback pass.

    Returns one result per requested track, in the order given, using the same
    shape as the single-source path so downstream evidence building is
    unchanged. A batch-level failure is reported on every row rather than
    silently succeeding for some.
    """
    from copilot.audio.arrangement_active_source_isolation import (
        _pass_timings,
        _wav_stats,
    )

    attach_tokens(session)
    project_identity = session.project_identity or session.project_token or ""
    hosts = available_hosts(session, ready_names=ready_names)
    if not hosts:
        return [
            {
                "ok": False,
                "signal_status": "CAPTURE_FAILED",
                "error": "no_capture_host",
                "display_name": track.name,
                "milestone": MILESTONE,
            }
            for track in tracks
        ]
    if len(tracks) > len(hosts):
        raise ValueError(
            f"batch of {len(tracks)} exceeds {len(hosts)} available capture hosts; "
            "use plan_batches() to size groups"
        )

    pass_id = uuid4().hex[:12]
    inventory = inventory_taps(daw)
    assert_unique_slots(inventory)

    # Resolve every target before touching routing: a target we cannot resolve
    # must not cause a partially prepared session.
    resolved_rows: list[dict[str, Any]] = []
    for track in tracks:
        ref = ref_from_track(track, project_identity=project_identity)
        outcome = resolve_track(session, ref)
        resolved_rows.append({"track": track, "ref": ref, "resolved": outcome})
    unresolved = [
        row for row in resolved_rows if row["resolved"].status is not ResolveStatus.RESOLVED
    ]
    if unresolved:
        return [
            {
                "ok": False,
                "signal_status": "CAPTURE_FAILED",
                "error": f"resolve_{row['resolved'].status.value}",
                "ref": row["ref"].model_dump(mode="json"),
                "display_name": row["track"].name,
                "milestone": MILESTONE,
            }
            for row in resolved_rows
        ]

    host_slice = hosts[: len(resolved_rows)]
    use_compound = compound_available(daw) and prepare_compile_ready(
        inventory, host_slice
    )

    prepared: list[dict[str, Any]] = []
    settled = False
    try:
        recorders: list[dict[str, Any]] = [_main_recorder(daw, inventory)]
        try:
            befores = _require_parked_baselines(daw, hosts[: len(resolved_rows)])
        except AudioCaptureError as exc:
            return [
                {
                    "ok": False,
                    "signal_status": "CAPTURE_FAILED",
                    "error": exc.code,
                    "display_name": track.name,
                    "milestone": MILESTONE,
                    "detail": str(exc),
                }
                for track in tracks
            ]
        for row, host in zip(resolved_rows, hosts):
            track = row["track"]
            journal = CaptureJournal(f"{pass_id}_{host['slot']}")
            journal.record(
                PREPARED,
                region=region_id,
                start_beat=start_qn,
                end_beat=end_qn,
                target=track.name,
                host=str(host["name"]),
                mode="CAPTURE_SOURCES_POST_MIXER_BATCH",
                milestone=MILESTONE,
                batch_pass_id=pass_id,
                batch_size=len(tracks),
                ref=row["ref"].model_dump(mode="json"),
            )
            host_index = int(host["index"])
            before = befores.get(host_index) or _snapshot_host(daw, host_index)
            item: dict[str, Any] = {
                "host": host,
                "before": before,
                "journal": journal,
                "track": track,
                "ref": row["ref"],
                "routing_mutations": [],
            }
            prepared.append(item)
            if use_compound:
                continue

            set_tap_enabled(daw, host_index, True)
            set_tap_recording(daw, False, host_index, broadcast_udp=False)
            routed = route_host_post_mixer(daw, host_index, track.name)
            item["routing_mutations"].append(f"route_input->{track.name}/Post Mixer")
            item["routing_mutations"].extend(_silence_sends(daw, host_index))

            claim = _verify_off_mix_graph(daw, host_index, track.name)
            if claim.get("claim") not in ACCEPTED_CLAIMS:
                raise AudioCaptureError(
                    "CAPTURE_ROUTING_UNSUPPORTED",
                    f"host={host['name']} claim={claim.get('claim')} detail={claim}",
                )
            if claim.get("through_main"):
                raise AudioCaptureError(
                    "AUDIBLE_MIX_RISK",
                    f"{host['name']} still routes through Main while tapping {track.name}",
                )
            input_channel = str(routed.get("input_channel") or "")
            item["claim"] = claim
            item["input_channel"] = input_channel
            recorders.append(
                _source_recorder(
                    daw,
                    inventory,
                    host=host,
                    target_name=track.name,
                    input_channel=input_channel,
                )
            )
            journal.record(
                RECORDING,
                claim=claim.get("claim"),
                channel=input_channel,
                routing_claim=claim.get("claim"),
            )

        if use_compound and prepared:
            _apply_compound_prepare(
                daw, session, prepared, inventory, project_identity
            )
            for item in prepared:
                recorders.append(
                    _source_recorder(
                        daw,
                        inventory,
                        host=item["host"],
                        target_name=item["track"].name,
                        input_channel=str(item.get("input_channel") or ""),
                    )
                )

        # Distinct slots and staging files are what make one shared pass safe.
        slots = [int(rec["slot"]) for rec in recorders]
        if len(set(slots)) != len(slots):
            raise AudioCaptureError("TAP_SLOT_COLLISION", f"batch slots={slots}")
        staging = [str(rec["staging"]) for rec in recorders]
        if len(set(staging)) != len(staging):
            raise AudioCaptureError("TAP_SLOT_COLLISION", f"batch staging={staging}")

        with span(
            "capture.playback_pass",
            category=CAT_INTRINSIC,
            recorders=len(recorders),
            sources=len(prepared),
            region_qn=float(end_qn) - float(start_qn),
            batched=True,
        ):
            one = capture_parallel_pass(
                daw,
                start_beat=start_qn,
                end_beat=end_qn,
                fire_tracks=[],
                tempo=tempo,
                session_revision=int(preflight.get("revision") or session.revision or 0),
                pass_id=pass_id,
                recorders=recorders,
                transport="arrangement",
            )
        pass_timings = _pass_timings(one)
        for item in prepared:
            item["journal"].record(FINALIZING, timings=pass_timings)

        assets = one.get("assets") or {}
        main_asset = assets.get("master")
        if main_asset is None:
            raise AudioCaptureError(
                "CAPTURE_MISSING_ASSET", f"missing main; keys={list(assets)}"
            )
        main_wav = Path(
            str(getattr(main_asset, "analysis_file_path", None) or main_asset.file_path)
        )

        # Finalize each source independently: one missing asset must not
        # invalidate the sources that did record.
        for item in prepared:
            key = str(item["host"]["key"])
            asset = assets.get(key)
            if asset is None:
                item["error"] = "CAPTURE_MISSING_ASSET"
                continue
            source_wav = Path(
                str(getattr(asset, "analysis_file_path", None) or asset.file_path)
            )
            tag = str(item["track"].name).replace(" ", "_")
            src_dest = dest_root / f"scb_v1_{region_id}_{tag}_{pass_id}.wav"
            main_dest = dest_root / f"scb_v1_{region_id}_Main_with_{tag}_{pass_id}.wav"
            with span("capture.copy_artifacts", category=CAT_IO, files=2):
                dest_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_wav, src_dest)
                shutil.copy2(main_wav, main_dest)
            with span("capture.wav_stats_and_hash", category=CAT_CPU, files=2):
                item["src_stats"] = _wav_stats(src_dest)
                item["main_stats"] = _wav_stats(main_dest)
            item["src_dest"] = src_dest
            item["main_dest"] = main_dest

        restore = _restore_all(daw, prepared)
        settled = True

        results: list[dict[str, Any]] = []
        for item in prepared:
            journal = item["journal"]
            host = item["host"]
            base: dict[str, Any] = {
                "pass_id": pass_id,
                "batch_pass_id": pass_id,
                "batch_size": len(prepared),
                "journal": str(journal.path),
                "display_name": item["track"].name,
                "ref": item["ref"].model_dump(mode="json"),
                "routing_mutations": item["routing_mutations"],
                "milestone": MILESTONE,
                "timings": pass_timings,
                "protocol": "TapProtocol3_parallel_arrangement",
                "shared_transport_pass": True,
                "transport": "arrangement",
                "region": {"id": region_id, "start_qn": start_qn, "end_qn": end_qn},
                "restore": restore["hosts"].get(str(host["name"])),
                "host": str(host["name"]),
                "host_slot": int(host["slot"]),
                "tokens_at_bind": {
                    "PROJECT_STATE_TOKEN": session.project_token or "",
                    "AUDIBLE_STATE_TOKEN": session.audible_token or "",
                },
            }
            # A restore failure anywhere fails the whole batch closed: the
            # session is no longer known-good, so no row may claim success.
            if not restore["ok"]:
                journal.record(FAILED, error="RESTORE_FAILED", restore=restore)
                results.append(
                    {
                        **base,
                        "ok": False,
                        "signal_status": "CAPTURE_FAILED",
                        "error": "ROUTING_RESTORE_FAILED",
                        "restore_all": restore,
                    }
                )
                continue
            if item.get("error") or "src_stats" not in item:
                journal.record(
                    FAILED, error=str(item.get("error") or "CAPTURE_MISSING_ASSET")
                )
                results.append(
                    {
                        **base,
                        "ok": False,
                        "signal_status": "CAPTURE_FAILED",
                        "error": str(item.get("error") or "CAPTURE_MISSING_ASSET"),
                    }
                )
                continue
            src_stats = item["src_stats"]
            main_stats = item["main_stats"]
            journal.record(
                VERIFIED,
                hashes={
                    "source": src_stats["audio_sha256"],
                    "main": main_stats["audio_sha256"],
                },
                signal_class=src_stats["signal_class"],
            )
            claim = item.get("claim") or {}
            results.append(
                {
                    **base,
                    "ok": True,
                    "resolve_status": ResolveStatus.RESOLVED.value,
                    "signal_point": "POST_MIXER",
                    "signal_status": src_stats["signal_class"],
                    "rms": src_stats["rms"],
                    "peak": src_stats["peak"],
                    "sample_rate": src_stats["sample_rate"],
                    "duration_s": src_stats["duration_s"],
                    "audio_sha256": src_stats["audio_sha256"],
                    "wav_path": str(item["src_dest"]),
                    "main_signal_status": main_stats["signal_class"],
                    "main_rms": main_stats["rms"],
                    "main_wav_path": str(item["main_dest"]),
                    "main_audio_sha256": main_stats["audio_sha256"],
                    "routing_claim": claim.get("claim"),
                    "through_main": bool(claim.get("through_main")),
                    "input_channel": item.get("input_channel"),
                    "routing_snapshot_before": {
                        "input": (item["before"] or {}).get("input"),
                        "output": (item["before"] or {}).get("output"),
                        "monitoring": (item["before"] or {}).get("monitoring"),
                    },
                }
            )
        return results
    except Exception as exc:  # noqa: BLE001 — fail closed for the whole batch
        restore = _restore_all(daw, prepared)
        settled = True
        for item in prepared:
            try:
                item["journal"].record(FAILED, error=str(exc))
            except Exception:  # noqa: BLE001
                pass
        return [
            {
                "ok": False,
                "signal_status": "CAPTURE_FAILED",
                "error": str(exc),
                "pass_id": pass_id,
                "display_name": track.name,
                "milestone": MILESTONE,
                "restore_all": restore,
                "batch_failed_closed": True,
            }
            for track in tracks
        ]
    finally:
        if not settled:
            # BaseException only (KeyboardInterrupt / SystemExit).
            _cancel_cleanup_batch(daw, prepared)
