"""RPC_TRACE_V2 — classify every Ableton round trip."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RpcClass(StrEnum):
    NECESSARY_FRESH_READ = "NECESSARY_FRESH_READ"
    REDUNDANT_READ = "REDUNDANT_READ"
    BATCHABLE_READ = "BATCHABLE_READ"
    POST_MUTATION_VERIFICATION = "POST_MUTATION_VERIFICATION"
    MUTATION = "MUTATION"
    UNAVOIDABLE_PROTOCOL_CALL = "UNAVOIDABLE_PROTOCOL_CALL"


READS = frozenset(
    {
        "get_track_info",
        "get_tracks_info",
        "get_track_monitoring",
        "get_track_sends",
        "get_tracks_sends",
        "get_track_input_routing",
        "get_track_output_routing",
        "get_device_parameters",
        "get_device_parameter",
        "get_capture_topology",
        "get_capture_hosts_state",
        "get_master_info",
        "get_session_info",
        "get_playback_position",
        "get_available_inputs",
        "get_available_outputs",
        "get_session_path",
        "health_check",
        "protocol_hello",
    }
)

BATCHABLE = frozenset(
    {
        "get_track_info",
        "get_track_monitoring",
        "get_track_sends",
        "get_track_input_routing",
        "get_track_output_routing",
        "get_device_parameters",
    }
)

UNAVOIDABLE = frozenset(
    {
        "get_available_inputs",
        "get_available_outputs",
        "protocol_hello",
        "health_check",
        "start_playback_at_qn",
        "stop_playback",
        "set_current_song_time",
        "set_arrangement_loop",
        "get_playback_position",
    }
)


@dataclass
class RpcTraceEvent:
    seq: int
    method: str
    duration_s: float
    classification: str
    track_index: int | None = None
    fields: list[str] = field(default_factory=list)
    already_known: bool = False
    previous_valid: bool = False
    invalidated_by: str | None = None
    side_effect: bool = False
    node: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "method": self.method,
            "duration_s": round(self.duration_s, 6),
            "classification": self.classification,
            "track_index": self.track_index,
            "fields": self.fields,
            "already_known": self.already_known,
            "previous_valid": self.previous_valid,
            "invalidated_by": self.invalidated_by,
            "side_effect": self.side_effect,
            "node": self.node,
        }


def classify_rpc(
    method: str,
    *,
    side_effect: bool,
    already_known: bool,
    previous_valid: bool,
    post_mutation: bool,
) -> str:
    if side_effect:
        return RpcClass.MUTATION.value
    if method in UNAVOIDABLE:
        return RpcClass.UNAVOIDABLE_PROTOCOL_CALL.value
    if already_known and previous_valid:
        return RpcClass.REDUNDANT_READ.value
    if post_mutation:
        return RpcClass.POST_MUTATION_VERIFICATION.value
    if method in BATCHABLE:
        return RpcClass.BATCHABLE_READ.value
    if method in READS:
        return RpcClass.NECESSARY_FRESH_READ.value
    return RpcClass.UNAVOIDABLE_PROTOCOL_CALL.value


def summarize_trace(events: list[RpcTraceEvent]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.classification] = counts.get(event.classification, 0) + 1
    return {
        "event_count": len(events),
        "by_class": counts,
        "events": [event.to_dict() for event in events],
    }
