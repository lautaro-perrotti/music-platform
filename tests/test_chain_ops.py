from __future__ import annotations

import pytest

from copilot.daw.adapter import DawError
from copilot.producer.chain_ops import ensure_chain, ensure_order, ensure_sidechain
from copilot.schemas.session import DeviceState, MixerState, RoutingState, SessionState, TrackState, TransportState


class FakeDaw:
    def __init__(self) -> None:
        self._tracks = [
            {"name": "Bass", "devices": ["EQ Eight", "Saturator"]},
            {"name": "Kick", "devices": ["EQ Eight"]},
        ]

    def snapshot(self) -> SessionState:
        tracks: list[TrackState] = []
        for ti, t in enumerate(self._tracks):
            devices = [
                DeviceState(
                    stable_id=f"dev_{ti}_{di}",
                    index=di,
                    name=name,
                    class_name=name,
                    enabled=True,
                    parameters=[],
                )
                for di, name in enumerate(t["devices"])
            ]
            tracks.append(
                TrackState(
                    stable_id=f"trk_{ti}",
                    index=ti,
                    name=t["name"],
                    role="audio",
                    mixer=MixerState(),
                    routing=RoutingState(output_type="Main", monitoring="in"),
                    devices=devices,
                    clips=[],
                )
            )
        return SessionState(
            daw="mock",
            connected=True,
            transport=TransportState(),
            tracks=tracks,
            session_incarnation_id="sess_test",
        )

    def load_instrument_or_effect(self, track_index: int, uri: str):
        self._tracks[track_index]["devices"].append(uri)
        return {"loaded": True, "device_name": uri}

    def bridge_command(self, command_type: str, params: dict | None = None, *, side_effect: bool | None = None):
        params = params or {}
        if command_type == "move_device":
            ti = int(params["track_index"])
            frm = int(params["device_index"])
            to = int(params["new_index"])
            arr = self._tracks[ti]["devices"]
            item = arr.pop(frm)
            arr.insert(to, item)
            return {"moved": True}
        if command_type == "move_device_right":
            ti = int(params["track_index"])
            i = int(params["device_index"])
            arr = self._tracks[ti]["devices"]
            if i < len(arr) - 1:
                arr[i], arr[i + 1] = arr[i + 1], arr[i]
            return {"moved": True}
        raise DawError(f"unsupported command {command_type}")

    def set_device_input_routing(self, track_index: int, device_index: int, routing_type: str, routing_channel: str = ""):
        track_names = {t["name"] for t in self._tracks}
        return {
            "track_index": track_index,
            "device_index": device_index,
            "input_routing_type": routing_type,
            "input_routing_channel": routing_channel,
            "type_matched": routing_type in track_names,
            "channel_matched": True,
        }


def test_ensure_order_reorders_prefix() -> None:
    daw = FakeDaw()
    session = daw.snapshot()
    report = ensure_order(
        daw,
        session=session,
        track_name="Bass",
        desired_order=["Saturator", "EQ Eight"],
    )
    assert report["ok"] is True
    assert report["final_order"][:2] == ["Saturator", "EQ Eight"]


def test_ensure_chain_loads_missing_and_orders() -> None:
    daw = FakeDaw()
    session = daw.snapshot()
    report = ensure_chain(
        daw,
        session=session,
        track_name="Bass",
        devices=["Compressor", "EQ Eight", "Saturator"],
    )
    assert report["ok"] is True
    assert "Compressor" in report["loaded"]
    assert report["final_order"][:3] == ["Compressor", "EQ Eight", "Saturator"]


def test_ensure_sidechain_reports_ok_when_source_exists() -> None:
    daw = FakeDaw()
    session = daw.snapshot()
    report = ensure_sidechain(
        daw,
        session=session,
        track_name="Bass",
        device_name="Saturator",
        source_track="Kick",
        source_channel="Post FX",
    )
    assert report["ok"] is True
    assert report["result"]["type_matched"] is True


def test_ensure_sidechain_fails_when_device_missing() -> None:
    daw = FakeDaw()
    session = daw.snapshot()
    with pytest.raises(DawError):
        ensure_sidechain(
            daw,
            session=session,
            track_name="Bass",
            device_name="Compressor",
            source_track="Kick",
        )
