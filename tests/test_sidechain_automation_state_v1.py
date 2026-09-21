from __future__ import annotations

from copilot.audio.sidechain_automation_state_v1 import (
    build_child_pack,
    read_available_routing,
    read_automation_state,
)
from copilot.schemas.evidence import EvidencePack


class _FakeDaw:
    def get_track_available_input_types(self, i):
        return {"types": [{"display_name": "All Ins"}, {"display_name": "7-TBSP - Kick 1"}]}

    def get_track_available_output_types(self, i):
        return {"types": [{"display_name": "Main"}, {"display_name": "BASS GROUP"}]}

    def get_session_automation_record(self):
        return {"session_automation_record": False}

    def get_clip_automation(self, t, c, p):
        return {"has_automation": False}


def test_read_available_routing_captures_sidechain_candidate():
    daw = _FakeDaw()
    out = read_available_routing(daw, 8)
    names = [t["display_name"] for t in out["input_types"]["types"]]
    assert "7-TBSP - Kick 1" in names


def test_read_automation_state_records_record_flag():
    daw = _FakeDaw()
    out = read_automation_state(daw, None, [])
    assert out["session_automation_record"] is False


def test_build_child_pack_has_sidechain_and_automation_items():
    causal = {
        "sources": [
            {
                "source": {"index": 8, "name": "BASS", "role": "midi"},
                "available_routing": {
                    "8": {
                        "input_types": {"types": [{"display_name": "7-TBSP - Kick 1"}]},
                        "output_types": {"types": [{"display_name": "Main"}]},
                    }
                },
            }
        ]
    }
    pack = build_child_pack(
        region_label="SIDECHAIN:224-256",
        project_token="tok",
        audible_token="aud",
        causal_table=causal,
        automation={"session_automation_record": False, "clip_automation": []},
    )
    assert isinstance(pack, EvidencePack)
    ids = [i.evidence_id for i in pack.items]
    assert any(i.startswith("sc.avail.") for i in ids)
    assert any(i.startswith("sc.sidechain_hint.") for i in ids)
    assert "sc.automation" in ids
    assert "sidechain_source_not_directly_readable" in [l.code for l in pack.limitations]
