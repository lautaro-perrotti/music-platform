from copilot.runtime.producer_execution_v1 import (
    CERTIFIED_PRODUCTION_ACTION_KINDS,
    MINIMUM_PRODUCTION_ACTION_KINDS,
    MINIMUM_PRODUCTION_ACTIONS,
    spec_for,
)
from copilot.schemas.musicplan import ProductionActionKind


def test_producer_execution_v1_has_exact_minimum_surface():
    assert MINIMUM_PRODUCTION_ACTION_KINDS == {
        ProductionActionKind.CREATE_TRACK,
        ProductionActionKind.LOAD_SAMPLE,
        ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT,
        ProductionActionKind.LOAD_DEVICE,
        "SET_DEVICE_PARAMETER",
        ProductionActionKind.SET_TRACK_VOLUME,
    }


def test_minimum_production_action_set_is_certified_as_one_lifecycle():
    assert CERTIFIED_PRODUCTION_ACTION_KINDS == {
        ProductionActionKind.CREATE_TRACK,
        ProductionActionKind.LOAD_SAMPLE,
        ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT,
        ProductionActionKind.LOAD_DEVICE,
        "SET_DEVICE_PARAMETER",
        ProductionActionKind.SET_TRACK_VOLUME,
    }
    assert spec_for(ProductionActionKind.CREATE_TRACK).certified is True
    assert len(MINIMUM_PRODUCTION_ACTIONS) == 6
