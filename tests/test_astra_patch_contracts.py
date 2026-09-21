from __future__ import annotations

from copilot.musicplan.astra_plan import validate_patch_contracts


def test_validate_patch_contracts_clamps_and_filters() -> None:
    raw = [
        {
            "track": "Bass",
            "device": "Compressor",
            "writes": [
                {"name": "Threshold", "value": 1.5},
                {"index": 2, "value": -1.0},
            ],
            "constraints": {"max_delta_norm": 9.0, "max_writes": 999, "forbid_device_on_toggle": False},
        },
        {"track": "Unknown", "device": "X", "writes": [{"index": 1, "value": 0.5}]},
    ]
    out = validate_patch_contracts(raw, known_tracks={"Bass", "Kick"})
    assert out is not None
    assert len(out) == 1
    c = out[0]
    assert c["track"] == "Bass"
    assert c["writes"][0]["value"] == 1.0
    assert c["writes"][1]["value"] == 0.0
    assert c["constraints"]["max_delta_norm"] == 0.5
    assert c["constraints"]["max_writes"] == 16
