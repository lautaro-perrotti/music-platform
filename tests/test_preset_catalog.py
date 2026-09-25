from __future__ import annotations

from copilot.producer.preset_catalog import (
    PresetCatalog,
    PresetSelectionRequest,
    catalog_from_payload,
)


def test_preset_first_selection_is_role_compatible_and_deterministic():
    catalog = catalog_from_payload(
        {
            "presets": [
                {
                    "uri": "native://serum2/bass-warm",
                    "name": "Warm Bass",
                    "plugin": "Serum 2",
                    "roles": ["bass"],
                    "tags": ["warm", "mono"],
                },
                {
                    "uri": "native://serum2/bass-clean",
                    "name": "Clean Bass",
                    "plugin": "Serum 2",
                    "roles": ["bass"],
                    "tags": ["clean"],
                },
                {
                    "uri": "native://serum2/lead",
                    "name": "Lead",
                    "plugin": "Serum 2",
                    "roles": ["lead"],
                    "tags": ["bright"],
                },
            ]
        }
    )
    result = catalog.select(
        PresetSelectionRequest(plugin="serum 2", role="bass", desired_tags=["warm"])
    )
    assert result.status == "SELECTED"
    assert [item.name for item in result.candidates] == ["Warm Bass", "Clean Bass"]
    assert result.scores[0] > result.scores[1]
    assert result.writes_authorized == 0


def test_preset_selection_fails_closed_when_role_is_missing():
    catalog = PresetCatalog(presets=[])
    result = catalog.select(PresetSelectionRequest(plugin="Serum 2", role="bass"))
    assert result.status == "UNAVAILABLE"
    assert result.candidates == []
    assert result.reasons == ["NO_ROLE_COMPATIBLE_PRESET"]
