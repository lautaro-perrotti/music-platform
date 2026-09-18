"""Explicit freshness domains and mutation generations.

Timestamp is never freshness authority. An entry is reusable only when
project identity, state token, and domain generation all match.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

MONITORING_VALUE = {"in": 0, "auto": 1, "off": 2}


class FreshnessDomain(StrEnum):
    PROJECT_TOPOLOGY = "PROJECT_TOPOLOGY"
    TRACK_METADATA = "TRACK_METADATA"
    MONITORING_STATE = "MONITORING_STATE"
    ROUTING_STATE = "ROUTING_STATE"
    SEND_STATE = "SEND_STATE"
    DEVICE_INVENTORY = "DEVICE_INVENTORY"
    DEVICE_PARAMETER_STATE = "DEVICE_PARAMETER_STATE"
    AUTOMATION_STATE = "AUTOMATION_STATE"
    ARRANGEMENT_STATE = "ARRANGEMENT_STATE"
    TRANSPORT_STATE = "TRANSPORT_STATE"
    CAPTURE_HOST_STATE = "CAPTURE_HOST_STATE"


GLOBAL_DOMAINS = frozenset(
    {
        FreshnessDomain.PROJECT_TOPOLOGY,
        FreshnessDomain.ARRANGEMENT_STATE,
        FreshnessDomain.TRANSPORT_STATE,
        FreshnessDomain.AUTOMATION_STATE,
    }
)

# FROZEN: RPC_OPTIMIZATION_V2. Domain-scoped generations, not one giant cache.
# Bundle required to serve a full get_track_info row.
TRACK_INFO_DOMAINS = (
    FreshnessDomain.TRACK_METADATA,
    FreshnessDomain.MONITORING_STATE,
    FreshnessDomain.ROUTING_STATE,
    FreshnessDomain.SEND_STATE,
    FreshnessDomain.DEVICE_INVENTORY,
    FreshnessDomain.DEVICE_PARAMETER_STATE,
)

HOST_STATE_DOMAINS = TRACK_INFO_DOMAINS + (FreshnessDomain.CAPTURE_HOST_STATE,)

# command → (domains, track_scoped)
MUTATION_DOMAINS: dict[str, tuple[tuple[FreshnessDomain, ...], bool]] = {
    "set_track_input_routing": (
        (FreshnessDomain.ROUTING_STATE, FreshnessDomain.CAPTURE_HOST_STATE),
        True,
    ),
    "set_track_output_routing": (
        (FreshnessDomain.ROUTING_STATE, FreshnessDomain.CAPTURE_HOST_STATE),
        True,
    ),
    "set_track_monitoring": (
        (FreshnessDomain.MONITORING_STATE, FreshnessDomain.CAPTURE_HOST_STATE),
        True,
    ),
    "set_send_level": (
        (FreshnessDomain.SEND_STATE, FreshnessDomain.CAPTURE_HOST_STATE),
        True,
    ),
    "set_device_parameter": (
        (FreshnessDomain.DEVICE_PARAMETER_STATE, FreshnessDomain.CAPTURE_HOST_STATE),
        True,
    ),
    "set_device_parameters": (
        (FreshnessDomain.DEVICE_PARAMETER_STATE, FreshnessDomain.CAPTURE_HOST_STATE),
        True,
    ),
    "execute_mutation_batch": (
        (
            FreshnessDomain.ROUTING_STATE,
            FreshnessDomain.MONITORING_STATE,
            FreshnessDomain.SEND_STATE,
            FreshnessDomain.DEVICE_PARAMETER_STATE,
            FreshnessDomain.CAPTURE_HOST_STATE,
        ),
        False,
    ),
    "set_track_mute": ((FreshnessDomain.TRACK_METADATA,), True),
    "set_track_solo": ((FreshnessDomain.TRACK_METADATA,), True),
    "set_track_arm": ((FreshnessDomain.TRACK_METADATA,), True),
    "set_track_volume": ((FreshnessDomain.TRACK_METADATA,), True),
    "set_track_name": (
        (FreshnessDomain.TRACK_METADATA, FreshnessDomain.PROJECT_TOPOLOGY),
        True,
    ),
    "create_audio_track": (
        (
            FreshnessDomain.PROJECT_TOPOLOGY,
            FreshnessDomain.TRACK_METADATA,
            FreshnessDomain.DEVICE_INVENTORY,
            FreshnessDomain.ROUTING_STATE,
        ),
        False,
    ),
    "create_midi_track": (
        (
            FreshnessDomain.PROJECT_TOPOLOGY,
            FreshnessDomain.TRACK_METADATA,
            FreshnessDomain.DEVICE_INVENTORY,
        ),
        False,
    ),
    "delete_track": (
        (FreshnessDomain.PROJECT_TOPOLOGY, FreshnessDomain.TRACK_METADATA),
        False,
    ),
    "create_group_track": ((FreshnessDomain.PROJECT_TOPOLOGY,), False),
    "load_instrument_or_effect": (
        (
            FreshnessDomain.DEVICE_INVENTORY,
            FreshnessDomain.DEVICE_PARAMETER_STATE,
            FreshnessDomain.PROJECT_TOPOLOGY,
        ),
        True,
    ),
    "delete_device": (
        (
            FreshnessDomain.DEVICE_INVENTORY,
            FreshnessDomain.DEVICE_PARAMETER_STATE,
            FreshnessDomain.PROJECT_TOPOLOGY,
        ),
        True,
    ),
    "move_device": (
        (FreshnessDomain.DEVICE_INVENTORY, FreshnessDomain.PROJECT_TOPOLOGY),
        True,
    ),
    "start_playback": ((FreshnessDomain.TRANSPORT_STATE,), False),
    "stop_playback": ((FreshnessDomain.TRANSPORT_STATE,), False),
    "start_playback_at_qn": ((FreshnessDomain.TRANSPORT_STATE,), False),
    "set_current_song_time": ((FreshnessDomain.TRANSPORT_STATE,), False),
    "jump_to_time": ((FreshnessDomain.TRANSPORT_STATE,), False),
    "set_arrangement_loop": ((FreshnessDomain.TRANSPORT_STATE,), False),
    "set_tempo": (
        (FreshnessDomain.TRANSPORT_STATE, FreshnessDomain.PROJECT_TOPOLOGY),
        False,
    ),
}


class FreshnessClock:
    def __init__(self) -> None:
        self.project_identity: str | None = None
        self.project_token: str | None = None
        self.epoch = 0
        self.global_gen: dict[str, int] = {d.value: 0 for d in GLOBAL_DOMAINS}
        self.track_gen: dict[int, dict[str, int]] = {}
        self.read_gen: dict[tuple[str, int | None], int] = {}
        self.last_mutation: str | None = None
        self.topology_dirty = True

    def current(self, domain: FreshnessDomain, track: int | None = None) -> int:
        if domain in GLOBAL_DOMAINS or track is None:
            return int(self.global_gen.get(domain.value, 0))
        return int(self.track_gen.get(int(track), {}).get(domain.value, 0))

    def bump(self, domain: FreshnessDomain, track: int | None = None) -> None:
        if domain in GLOBAL_DOMAINS or track is None:
            self.global_gen[domain.value] = self.current(domain) + 1
        else:
            bucket = self.track_gen.setdefault(int(track), {})
            bucket[domain.value] = self.current(domain, track) + 1
        if domain is FreshnessDomain.PROJECT_TOPOLOGY:
            self.topology_dirty = True

    def mark_read(self, domain: FreshnessDomain, track: int | None = None) -> None:
        key_track = None if domain in GLOBAL_DOMAINS else (None if track is None else int(track))
        self.read_gen[(domain.value, key_track)] = self.current(domain, track)

    def is_fresh(self, domain: FreshnessDomain, track: int | None = None) -> bool:
        key_track = None if domain in GLOBAL_DOMAINS else (None if track is None else int(track))
        key = (domain.value, key_track)
        if key not in self.read_gen:
            return False
        return self.read_gen[key] == self.current(domain, track)

    def all_fresh(self, domains: tuple[FreshnessDomain, ...], track: int | None) -> bool:
        return all(self.is_fresh(domain, track) for domain in domains)

    def mark_track_bundle_read(self, track: int) -> None:
        for domain in HOST_STATE_DOMAINS:
            if domain not in GLOBAL_DOMAINS:
                self.mark_read(domain, track)

    def mark_topology_read(self, tracks: list[int]) -> None:
        self.mark_read(FreshnessDomain.PROJECT_TOPOLOGY)
        self.mark_read(FreshnessDomain.TRANSPORT_STATE)
        self.topology_dirty = False
        for index in tracks:
            self.mark_track_bundle_read(int(index))

    def bind_project(self, identity: str | None, token: str | None) -> None:
        if self.project_identity and identity and identity != self.project_identity:
            self.invalidate_all("PROJECT_MISMATCH")
        if self.project_token and token and token != self.project_token:
            self.invalidate_all("PROJECT_TOKEN_CHANGED")
        self.project_identity = identity
        self.project_token = token

    def invalidate_all(self, reason: str) -> None:
        self.epoch += 1
        self.last_mutation = reason
        for key in list(self.global_gen):
            self.global_gen[key] += 1
        self.track_gen.clear()
        self.read_gen.clear()
        self.topology_dirty = True

    def apply_mutation(self, command: str, params: dict[str, Any] | None = None) -> list[str]:
        params = params or {}
        self.last_mutation = command
        spec = MUTATION_DOMAINS.get(command)
        track = params.get("track_index")
        bumped: list[str] = []
        if spec is None:
            if track is not None:
                self.bump(FreshnessDomain.CAPTURE_HOST_STATE, int(track))
                bumped.append(f"{FreshnessDomain.CAPTURE_HOST_STATE.value}:{track}")
            else:
                self.bump(FreshnessDomain.PROJECT_TOPOLOGY)
                bumped.append(FreshnessDomain.PROJECT_TOPOLOGY.value)
            self.topology_dirty = True
            return bumped
        domains, track_scoped = spec
        for domain in domains:
            if domain in GLOBAL_DOMAINS or not track_scoped or track is None:
                self.bump(domain)
                bumped.append(domain.value)
                if not track_scoped and domain not in GLOBAL_DOMAINS:
                    for track_id in list(self.track_gen):
                        self.track_gen[track_id].pop(domain.value, None)
                    for key in list(self.read_gen):
                        if key[0] == domain.value:
                            del self.read_gen[key]
            else:
                self.bump(domain, int(track))
                bumped.append(f"{domain.value}:{int(track)}")
        if FreshnessDomain.PROJECT_TOPOLOGY in domains:
            self.topology_dirty = True
            self.read_gen = {
                key: gen
                for key, gen in self.read_gen.items()
                if key[0] != FreshnessDomain.PROJECT_TOPOLOGY.value
            }
        return bumped

    def need(self, domain: FreshnessDomain, track: int | None = None) -> bool:
        return not self.is_fresh(domain, track)

    def snapshot_reusable(self) -> bool:
        """True only when the bundled topology payload is still authoritative.

        Topology snapshots include routing, monitoring, sends and devices.
        Any track-domain generation newer than the last topology read forces
        a fresh get_capture_topology. NO_CHANGES_REQUIRED bootstrap does not.
        """
        if self.topology_dirty:
            return False
        if not self.is_fresh(FreshnessDomain.PROJECT_TOPOLOGY):
            return False
        if not self.is_fresh(FreshnessDomain.TRANSPORT_STATE):
            return False
        for (domain, track), read_gen in list(self.read_gen.items()):
            if track is None:
                if self.current(FreshnessDomain(domain)) != read_gen:
                    return False
                continue
            if self.current(FreshnessDomain(domain), track) != read_gen:
                return False
        return True
