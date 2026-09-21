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
        ProductionActionKind.SET_DEVICE_PARAMETER,
        ProductionActionKind.SET_TRACK_VOLUME,
    }


def test_only_volume_is_certified_until_its_full_lifecycle_is_verified():
    assert CERTIFIED_PRODUCTION_ACTION_KINDS == {ProductionActionKind.SET_TRACK_VOLUME}
    assert spec_for(ProductionActionKind.CREATE_TRACK).certified is False
    assert len(MINIMUM_PRODUCTION_ACTIONS) == 6
