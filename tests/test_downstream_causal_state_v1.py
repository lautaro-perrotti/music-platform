from __future__ import annotations

from copilot.audio.downstream_causal_state_v1 import (
    build_child_pack,
    build_causal_event_table,
    resolve_output_path,
)
from copilot.schemas.session import SessionState, TrackState


class FakeDaw:
    def __init__(self, routing):
        self._routing = routing  # index -> (type, channel)

    def get_track_output_routing(self, track_index):
        t, c = self._routing.get(track_index, ("Master", ""))
        return {
            "track_index": track_index,
            "output_routing_type": t,
            "output_routing_channel": c,
        }

    def get_track_info(self, track_index):
        return {"devices": []}

    def get_device_parameters(self, track_index, device_index):
        return {"parameters": []}


def _session():
    src = TrackState(stable_id="s1", index=0, name="Bass", role="audio")
    grp = TrackState(stable_id="g1", index=1, name="Low Group", role="unknown")
    grp2 = TrackState(stable_id="g2", index=2, name="All Group", role="unknown")
    return SessionState(
        revision=1,
        tracks=[src, grp, grp2],
        project_token="tok",
        audible_token="aud",
    )


def test_resolve_path_source_to_master():
    sess = _session()
    # Bass -> Low Group -> All Group -> Master
    daw = FakeDaw({0: ("Group Track", "Low Group"), 1: ("Group Track", "All Group"), 2: ("Master", "")})
    path = resolve_output_path(daw, sess, sess.tracks[0])
    names = [hop["name"] for hop in path]
    assert names == ["Bass", "Low Group", "All Group"]
    assert path[-1]["output_routing_type"] == "Master"


def test_causal_table_reaches_main():
    sess = _session()
    daw = FakeDaw({0: ("Group Track", "Low Group"), 1: ("Master", "")})
    table = build_causal_event_table(daw, sess, [sess.tracks[0]])
    row = table["sources"][0]
    assert row["reaches_main"] is True
    assert row["output_path"][-1]["output_routing_type"] == "Master"


def test_child_pack_has_new_id_and_items():
    sess = _session()
    daw = FakeDaw({0: ("Master", "")})
    table = build_causal_event_table(daw, sess, [sess.tracks[0]])
    pack = build_child_pack(
        region_label="R:0-32",
        project_token="tok",
        audible_token="aud",
        target_token=None,
        causal_table=table,
        parent_pack_id="pack_parent123",
    )
    assert pack.pack_id.startswith("pack_")
    assert pack.pack_id != "pack_parent123"
    assert len(pack.items) >= 2
    ids = [i.evidence_id for i in pack.items]
    assert len(ids) == len(set(ids))
