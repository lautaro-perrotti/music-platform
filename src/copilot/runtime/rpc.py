"""Operation-scoped authoritative Live read view V2.

Domain-scoped generations, not one giant valid/invalid cache.
READ ONCE, QUERY LOCALLY MANY TIMES — only while State Trust tokens and
the relevant freshness domain generation still match.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from typing import Any

from copilot.perf.ableton import rpc_breakdown
from copilot.runtime.freshness import (
    MONITORING_VALUE,
    TRACK_INFO_DOMAINS,
    FreshnessClock,
    FreshnessDomain,
)
from copilot.runtime.rpc_trace import RpcTraceEvent, classify_rpc, summarize_trace


class ProjectReadView:
    def __init__(self, clock: FreshnessClock | None = None) -> None:
        self.clock = clock or FreshnessClock()
        self.project_identity: str | None = None
        self.project_token: str | None = None
        self.audible_token: str | None = None
        self.tracks: dict[int, dict[str, Any]] = {}
        self.master: dict[str, Any] | None = None
        self.duplicate_reads_eliminated = 0
        self.bulk_calls = 0
        self.fresh_reads_forced = 0
        self.post_mutation_verifications = 0
        self.eliminated_by_method: Counter[str] = Counter()
        self.invalidation_reason: str | None = None
        self.rpc_events: list[RpcTraceEvent] = []
        self._seq = 0

    @property
    def valid(self) -> bool:
        """True if any domain is currently reusable. Not a global cache flag."""
        return bool(self.tracks) and self.clock.is_fresh(FreshnessDomain.PROJECT_TOPOLOGY)

    def bind(self, daw: Any, session: Any) -> None:
        identity = getattr(session, "project_identity", None)
        token = getattr(session, "project_token", None)
        self.clock.bind_project(identity, token)
        self.project_identity = identity
        self.project_token = token
        self.audible_token = getattr(session, "audible_token", None)
        self.tracks = {
            int(index): dict(info)
            for index, info in dict(getattr(daw, "last_track_infos", {}) or {}).items()
        }
        topology = getattr(daw, "last_topology", None)
        if isinstance(topology, dict) and isinstance(topology.get("tracks"), list):
            for item in topology["tracks"]:
                if "index" in item:
                    self.tracks[int(item["index"])] = dict(item)
        self.master = dict(getattr(daw, "last_master_info", None) or {}) or None
        self.clock.mark_topology_read(list(self.tracks))
        self.invalidation_reason = None
        daw_clock = getattr(daw, "freshness", None)
        if daw_clock is not None:
            daw.freshness = self.clock

    def ingest_track(self, info: dict[str, Any]) -> None:
        if "index" not in info:
            return
        index = int(info["index"])
        merged = dict(self.tracks.get(index) or {})
        merged.update(info)
        self.tracks[index] = merged
        self.clock.mark_track_bundle_read(index)

    def tokens_match(self, session: Any) -> bool:
        identity = getattr(session, "project_identity", None)
        token = getattr(session, "project_token", None)
        audible = getattr(session, "audible_token", None)
        if self.project_identity and identity and identity != self.project_identity:
            return False
        if self.project_token and token and token != self.project_token:
            return False
        if self.audible_token and audible and audible != self.audible_token:
            return False
        return True

    def invalidate(self, reason: str) -> None:
        """Project-level invalidation only (switch / mismatch)."""
        self.clock.invalidate_all(reason)
        self.invalidation_reason = reason
        self.tracks = {}
        self.master = None

    def apply_mutation(self, command: str, params: dict[str, Any] | None = None) -> list[str]:
        bumped = self.clock.apply_mutation(command, params)
        self.sync_after_mutation(command, params)
        return bumped

    def sync_after_mutation(self, command: str, params: dict[str, Any] | None = None) -> None:
        """Clock already bumped (adapter or apply_mutation). Sync the view map."""
        self.invalidation_reason = command
        if self.clock.topology_dirty or not self.clock.is_fresh(
            FreshnessDomain.PROJECT_TOPOLOGY
        ):
            self.tracks = {}
            self.master = None

    def _hit(
        self,
        method: str,
        domain: FreshnessDomain,
        track: int,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if payload is None or not self.clock.is_fresh(domain, track):
            return None
        self.duplicate_reads_eliminated += 1
        self.eliminated_by_method[method] += 1
        return payload

    def track_info(self, track_index: int) -> dict[str, Any] | None:
        index = int(track_index)
        info = self.tracks.get(index)
        if info is None or not self.clock.all_fresh(TRACK_INFO_DOMAINS, index):
            return None
        self.duplicate_reads_eliminated += 1
        self.eliminated_by_method["get_track_info"] += 1
        return info

    def track_monitoring(self, track_index: int) -> dict[str, Any] | None:
        index = int(track_index)
        info = self.tracks.get(index)
        if info is None or "monitoring" not in info:
            return None
        monitoring = str(info.get("monitoring") or "")
        return self._hit(
            "get_track_monitoring",
            FreshnessDomain.MONITORING_STATE,
            index,
            {
                "track_index": index,
                "monitoring": monitoring,
                "monitoring_value": MONITORING_VALUE.get(monitoring.lower(), -1),
                "can_monitor": monitoring not in {"", "NOT_APPLICABLE", "n/a"},
            },
        )

    def track_routing(self, track_index: int) -> dict[str, Any] | None:
        index = int(track_index)
        info = self.tracks.get(index)
        if info is None:
            return None
        if "input_routing_type" not in info and "output_routing_type" not in info:
            return None
        return self._hit(
            "read_routing",
            FreshnessDomain.ROUTING_STATE,
            index,
            {
                "track_index": index,
                "input_routing_type": info.get("input_routing_type"),
                "input_routing_channel": info.get("input_routing_channel"),
                "output_routing_type": info.get("output_routing_type"),
                "output_routing_channel": info.get("output_routing_channel"),
            },
        )

    def track_sends(self, track_index: int) -> list[dict[str, Any]] | None:
        index = int(track_index)
        info = self.tracks.get(index)
        if info is None or "sends" not in info:
            return None
        if not self.clock.is_fresh(FreshnessDomain.SEND_STATE, index):
            return None
        self.duplicate_reads_eliminated += 1
        self.eliminated_by_method["get_track_sends"] += 1
        return list(info.get("sends") or [])

    def devices_for_track(self, track_index: int) -> list[dict[str, Any]] | None:
        index = int(track_index)
        info = self.tracks.get(index)
        if info is None or "devices" not in info:
            return None
        if not self.clock.is_fresh(FreshnessDomain.DEVICE_INVENTORY, index):
            return None
        self.duplicate_reads_eliminated += 1
        self.eliminated_by_method["get_devices_for_tracks"] += 1
        return list(info.get("devices") or [])

    def device_parameters(
        self, track_index: int, device_index: int
    ) -> dict[str, Any] | None:
        index = int(track_index)
        if not self.clock.is_fresh(FreshnessDomain.DEVICE_PARAMETER_STATE, index):
            return None
        devices = self.devices_for_track(index)
        if devices is None:
            return None
        for device in devices:
            if int(device.get("index", -1)) != int(device_index):
                continue
            params = device.get("parameters")
            if not isinstance(params, list):
                return None
            self.eliminated_by_method["get_device_parameters"] += 1
            return {
                "track_index": index,
                "device_index": int(device_index),
                "parameters": params,
                "source": "project_read_view",
            }
        return None

    def capture_host_state(self, track_index: int) -> dict[str, Any] | None:
        from copilot.runtime.host_state import host_state_from_info

        index = int(track_index)
        info = self.tracks.get(index)
        if info is None:
            return None
        if not self.clock.all_fresh(TRACK_INFO_DOMAINS, index):
            return None
        if not self.clock.is_fresh(FreshnessDomain.CAPTURE_HOST_STATE, index):
            return None
        self.duplicate_reads_eliminated += 1
        self.eliminated_by_method["get_capture_hosts_state"] += 1
        return host_state_from_info(info)

    def tracks_info(self, indices: list[int] | None = None) -> dict[str, Any] | None:
        wanted = list(self.tracks) if indices is None else [int(i) for i in indices]
        rows = []
        for index in wanted:
            info = self.track_info(index)
            if info is None:
                return None
            rows.append(info)
        self.bulk_calls += 1
        return {"tracks": rows}

    def tracks_monitoring(self, indices: list[int] | None = None) -> dict[str, Any] | None:
        wanted = list(self.tracks) if indices is None else [int(i) for i in indices]
        rows = []
        for index in wanted:
            row = self.track_monitoring(index)
            if row is None:
                return None
            rows.append(row)
        self.bulk_calls += 1
        return {"tracks": rows}

    def tracks_sends(self, indices: list[int] | None = None) -> dict[str, Any] | None:
        wanted = list(self.tracks) if indices is None else [int(i) for i in indices]
        rows = []
        for index in wanted:
            sends = self.track_sends(index)
            if sends is None:
                return None
            rows.append({"track_index": index, "sends": sends})
        self.bulk_calls += 1
        return {"tracks": rows}

    def record_rpc(
        self,
        method: str,
        *,
        duration_s: float = 0.0,
        side_effect: bool = False,
        track_index: int | None = None,
        already_known: bool = False,
        previous_valid: bool = False,
        post_mutation: bool = False,
        node: str | None = None,
    ) -> None:
        self._seq += 1
        classification = classify_rpc(
            method,
            side_effect=side_effect,
            already_known=already_known,
            previous_valid=previous_valid,
            post_mutation=post_mutation,
        )
        self.rpc_events.append(
            RpcTraceEvent(
                seq=self._seq,
                method=method,
                duration_s=duration_s,
                classification=classification,
                track_index=track_index,
                already_known=already_known,
                previous_valid=previous_valid,
                invalidated_by=self.clock.last_mutation,
                side_effect=side_effect,
                node=node,
            )
        )

    def metrics(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "invalidation_reason": self.invalidation_reason,
            "duplicate_reads_eliminated": self.duplicate_reads_eliminated,
            "bulk_calls": self.bulk_calls,
            "fresh_reads_forced": self.fresh_reads_forced,
            "post_mutation_verifications": self.post_mutation_verifications,
            "eliminated_by_method": dict(self.eliminated_by_method),
            "track_count": len(self.tracks),
            "epoch": self.clock.epoch,
            "topology_dirty": self.clock.topology_dirty,
            "rpc_trace": summarize_trace(self.rpc_events),
        }


def _bulk_from_infos(daw: Any, indices: list[int] | None = None) -> dict[int, dict[str, Any]]:
    infos = dict(getattr(daw, "last_track_infos", {}) or {})
    if indices is None:
        return {int(k): v for k, v in infos.items()}
    missing = [i for i in indices if int(i) not in infos]
    if missing:
        batched = daw.get_tracks_info(missing)
        for item in batched.get("tracks") or []:
            if "index" in item:
                infos[int(item["index"])] = item
    return {int(i): infos[int(i)] for i in (indices or infos) if int(i) in infos}


def get_tracks_monitoring(daw: Any, indices: list[int] | None = None) -> dict[str, Any]:
    view: ProjectReadView | None = getattr(daw, "_project_read_view", None)
    if view is not None:
        cached = view.tracks_monitoring(indices)
        if cached is not None:
            return cached
    infos = _bulk_from_infos(daw, indices)
    rows = []
    for index, info in infos.items():
        monitoring = str(info.get("monitoring") or "")
        rows.append(
            {
                "track_index": int(index),
                "monitoring": monitoring,
                "monitoring_value": MONITORING_VALUE.get(monitoring.lower(), -1),
                "can_monitor": monitoring not in {"", "NOT_APPLICABLE", "n/a"},
            }
        )
    return {"tracks": rows}


def get_tracks_sends(daw: Any, indices: list[int] | None = None) -> dict[str, Any]:
    view: ProjectReadView | None = getattr(daw, "_project_read_view", None)
    if view is not None:
        cached = view.tracks_sends(indices)
        if cached is not None:
            return cached
    infos = _bulk_from_infos(daw, indices)
    return {
        "tracks": [
            {"track_index": int(index), "sends": list(info.get("sends") or [])}
            for index, info in infos.items()
        ]
    }


def get_devices_for_tracks(daw: Any, indices: list[int] | None = None) -> dict[str, Any]:
    view: ProjectReadView | None = getattr(daw, "_project_read_view", None)
    if view is not None:
        wanted = list(view.tracks) if indices is None else [int(i) for i in indices]
        rows = []
        for index in wanted:
            devices = view.devices_for_track(index)
            if devices is None:
                rows = []
                break
            rows.append({"track_index": index, "devices": devices})
        else:
            view.bulk_calls += 1
            return {"tracks": rows}
    infos = _bulk_from_infos(daw, indices)
    return {
        "tracks": [
            {"track_index": int(index), "devices": list(info.get("devices") or [])}
            for index, info in infos.items()
        ]
    }


def read_routing_bulk(daw: Any, indices: list[int] | None = None) -> dict[str, Any]:
    view: ProjectReadView | None = getattr(daw, "_project_read_view", None)
    if view is not None:
        wanted = list(view.tracks) if indices is None else [int(i) for i in indices]
        rows = []
        for index in wanted:
            row = view.track_routing(index)
            if row is None:
                rows = []
                break
            rows.append(row)
        else:
            view.bulk_calls += 1
            return {"tracks": rows}
    infos = _bulk_from_infos(daw, indices)
    return {
        "tracks": [
            {
                "track_index": int(index),
                "input_routing_type": info.get("input_routing_type"),
                "input_routing_channel": info.get("input_routing_channel"),
                "output_routing_type": info.get("output_routing_type"),
                "output_routing_channel": info.get("output_routing_channel"),
            }
            for index, info in infos.items()
        ]
    }


def get_device_parameters_bulk(
    daw: Any, specs: list[tuple[int, int]]
) -> dict[str, Any]:
    view: ProjectReadView | None = getattr(daw, "_project_read_view", None)
    rows = []
    missed: list[tuple[int, int]] = []
    for track_index, device_index in specs:
        cached = view.device_parameters(track_index, device_index) if view else None
        if cached is None:
            missed.append((track_index, device_index))
        else:
            rows.append(cached)
    for track_index, device_index in missed:
        rows.append(daw.get_device_parameters(track_index, device_index))
    if view is not None and not missed:
        view.bulk_calls += 1
    return {"devices": rows}


def attach_runtime_instrumentation(
    daw: Any, view: ProjectReadView, ctx: Any
) -> None:
    from copilot.perf.ableton import instrumented_rpc

    if getattr(daw, "freshness", None) is None:
        daw.freshness = view.clock
    else:
        view.clock = daw.freshness
    read_cm = install_read_view(daw, view)
    rpc_cm = instrumented_rpc(daw)
    read_cm.__enter__()
    rpc_cm.__enter__()

    def detach() -> None:
        rpc_cm.__exit__(None, None, None)
        read_cm.__exit__(None, None, None)

    ctx.finalizers.register(detach, name="detach_read_view", order=20)


@contextmanager
def install_read_view(daw: Any, view: ProjectReadView):
    """Serve domain-fresh reads locally. Mutations bump generations, not the world."""
    original_info = daw.get_track_info
    original_mon = daw.get_track_monitoring
    original_in = daw.get_track_input_routing
    original_out = daw.get_track_output_routing
    original_params = daw.get_device_parameters
    original_tracks = daw.get_tracks_info
    original_sends = getattr(daw, "get_track_sends", None)
    original_command = daw._command
    daw._project_read_view = view
    if getattr(daw, "freshness", None) is None:
        daw.freshness = view.clock
    else:
        view.clock = daw.freshness

    def get_track_info(track_index: int) -> dict[str, Any]:
        cached = view.track_info(track_index)
        if cached is not None:
            daw.last_track_infos[int(track_index)] = cached
            return cached
        info = original_info(track_index)
        view.ingest_track(info)
        return info

    def get_track_monitoring(track_index: int) -> dict[str, Any]:
        cached = view.track_monitoring(track_index)
        if cached is not None:
            return cached
        result = original_mon(track_index)
        view.clock.mark_read(FreshnessDomain.MONITORING_STATE, int(track_index))
        if int(track_index) in view.tracks:
            view.tracks[int(track_index)]["monitoring"] = result.get("monitoring")
        return result

    def get_track_input_routing(track_index: int) -> dict[str, Any]:
        cached = view.track_routing(track_index)
        if cached is not None:
            return {
                "input_routing_type": cached.get("input_routing_type"),
                "input_routing_channel": cached.get("input_routing_channel"),
            }
        return original_in(track_index)

    def get_track_output_routing(track_index: int) -> dict[str, Any]:
        cached = view.track_routing(track_index)
        if cached is not None:
            return {
                "output_routing_type": cached.get("output_routing_type"),
                "output_routing_channel": cached.get("output_routing_channel"),
            }
        return original_out(track_index)

    def get_device_parameters(track_index: int, device_index: int) -> dict[str, Any]:
        cached = view.device_parameters(track_index, device_index)
        if cached is not None:
            return cached
        return original_params(track_index, device_index)

    def get_tracks_info(indices: list[int] | None = None, *, fresh: bool = False) -> dict[str, Any]:
        if not fresh:
            cached = view.tracks_info(indices)
            if cached is not None:
                return cached
        result = original_tracks(indices)
        for item in result.get("tracks") or []:
            view.ingest_track(item)
            if "index" in item:
                daw.last_track_infos[int(item["index"])] = item
        view.fresh_reads_forced += 1 if fresh else 0
        if fresh:
            view.post_mutation_verifications += 1
        return result

    def get_track_sends(track_index: int, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        cached = view.track_sends(track_index)
        if cached is not None:
            return cached
        result = original_sends(track_index, *args, **kwargs)
        view.clock.mark_read(FreshnessDomain.SEND_STATE, int(track_index))
        if int(track_index) in view.tracks and isinstance(result, list):
            view.tracks[int(track_index)]["sends"] = list(result)
        return result

    def command(command_type: str, params: dict[str, Any] | None = None, **kwargs: Any):
        side_effect = bool(kwargs.get("side_effect"))
        previous_valid = view.valid
        already_known = False
        if not side_effect and command_type == "get_track_info" and params:
            already_known = view.track_info(int(params.get("track_index", -1))) is not None
        result = original_command(command_type, params, **kwargs)
        if side_effect:
            if getattr(daw, "_last_freshness_mutation", None) == command_type:
                view.sync_after_mutation(command_type, params)
            else:
                view.apply_mutation(command_type, params)
        elif command_type in {"get_capture_topology", "get_tracks_info", "get_capture_hosts_state"}:
            if isinstance(result, dict):
                if command_type == "get_capture_topology":
                    daw.last_topology = result
                    view.clock.mark_topology_read(
                        [int(item["index"]) for item in result.get("tracks") or [] if "index" in item]
                    )
                for item in result.get("tracks") or []:
                    if "index" in item:
                        daw.last_track_infos[int(item["index"])] = item
                        view.ingest_track(item)
        view.record_rpc(
            command_type,
            side_effect=side_effect,
            track_index=(params or {}).get("track_index"),
            already_known=already_known,
            previous_valid=previous_valid,
            post_mutation=side_effect is False and view.clock.last_mutation is not None
            and command_type.startswith("get_"),
        )
        return result

    daw.get_track_info = get_track_info
    daw.get_track_monitoring = get_track_monitoring
    daw.get_track_input_routing = get_track_input_routing
    daw.get_track_output_routing = get_track_output_routing
    daw.get_device_parameters = get_device_parameters
    daw.get_tracks_info = get_tracks_info
    if original_sends is not None:
        daw.get_track_sends = get_track_sends
    daw._command = command
    try:
        yield view
    finally:
        daw.get_track_info = original_info
        daw.get_track_monitoring = original_mon
        daw.get_track_input_routing = original_in
        daw.get_track_output_routing = original_out
        daw.get_device_parameters = original_params
        daw.get_tracks_info = original_tracks
        if original_sends is not None:
            daw.get_track_sends = original_sends
        daw._command = original_command
        daw._project_read_view = None


def rpc_metrics(trace: Any, view: ProjectReadView | None = None) -> dict[str, Any]:
    breakdown = (
        rpc_breakdown(trace)
        if trace is not None
        else {"total_calls": 0, "total_s": 0.0, "by_command": {}}
    )
    view_metrics = view.metrics() if view is not None else {}
    return {
        "rpc_count": breakdown.get("total_calls", 0),
        "rpc_wall_time": breakdown.get("total_s", 0.0),
        "rpc_by_method": breakdown.get("by_command", {}),
        "duplicate_reads_eliminated": view_metrics.get("duplicate_reads_eliminated", 0),
        "bulk_calls": view_metrics.get("bulk_calls", 0),
        "fresh_reads_forced": view_metrics.get("fresh_reads_forced", 0),
        "post_mutation_verifications": view_metrics.get("post_mutation_verifications", 0),
        "eliminated_by_method": view_metrics.get("eliminated_by_method", {}),
        "read_view": view_metrics,
        "rpc_trace": view_metrics.get("rpc_trace"),
    }
