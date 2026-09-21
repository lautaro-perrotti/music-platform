"""The intentionally small Producer Execution V1 certification boundary."""

from __future__ import annotations

from dataclasses import dataclass

from copilot.schemas.musicplan import ProductionActionKind

PRODUCER_EXECUTION_V1 = "PRODUCER_EXECUTION_V1"


@dataclass(frozen=True)
class ProductionActionSpec:
    kind: str
    operation: str
    certified: bool = False
    note: str = ""


MINIMUM_PRODUCTION_ACTIONS: tuple[ProductionActionSpec, ...] = (
    ProductionActionSpec(ProductionActionKind.CREATE_TRACK, "create_track", certified=True),
    ProductionActionSpec(ProductionActionKind.LOAD_SAMPLE, "load_sample", certified=True),
    ProductionActionSpec(
        ProductionActionKind.DUPLICATE_CLIP_TO_ARRANGEMENT,
        "duplicate_clip_to_arrangement",
        certified=True,
    ),
    ProductionActionSpec(ProductionActionKind.LOAD_DEVICE, "load_device", certified=True),
    ProductionActionSpec(
        "SET_DEVICE_PARAMETER",
        "set_device_parameter",
        certified=True,
    ),
    ProductionActionSpec(
        ProductionActionKind.SET_TRACK_VOLUME,
        "set_mixer_volume",
        certified=True,
    ),
)

MINIMUM_PRODUCTION_ACTION_KINDS = frozenset(item.kind for item in MINIMUM_PRODUCTION_ACTIONS)
CERTIFIED_PRODUCTION_ACTION_KINDS = frozenset(
    item.kind for item in MINIMUM_PRODUCTION_ACTIONS if item.certified
)


def spec_for(kind: ProductionActionKind | str) -> ProductionActionSpec | None:
    value = kind.value if isinstance(kind, ProductionActionKind) else str(kind)
    return next((item for item in MINIMUM_PRODUCTION_ACTIONS if item.kind == value), None)
