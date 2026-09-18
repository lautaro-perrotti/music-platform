from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CaptureQuality(StrEnum):
    OK = "OK"
    LIMITED = "LIMITED"
    WARNING = "WARNING"
    UNKNOWN = "UNKNOWN"


class EvidenceKind(StrEnum):
    MEASUREMENT = "MEASUREMENT"
    SESSION_ENTITY = "SESSION_ENTITY"
    LIMITATION = "LIMITATION"
    STATE_TOKEN = "STATE_TOKEN"
    FACT = "FACT"
    OBSERVATION = "OBSERVATION"
    RELATIONSHIP = "RELATIONSHIP"
    INTERPRETATION = "INTERPRETATION"
    DIAGNOSIS = "DIAGNOSIS"


class FusionStatus(StrEnum):
    AGREE = "AGREE"
    PARTIALLY_AGREE = "PARTIALLY_AGREE"
    CONTRADICT = "CONTRADICT"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class LimitationCode(StrEnum):
    ALIGNMENT_LIMITED = "ALIGNMENT_LIMITED"
    MIDI_UNAVAILABLE = "MIDI_UNAVAILABLE"
    AUTOMATION_UNREAD = "AUTOMATION_UNREAD"
    ROUTING_UNRESOLVED = "ROUTING_UNRESOLVED"
    CAPTURE_FAILED = "CAPTURE_FAILED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    SOURCE_MISSING = "SOURCE_MISSING"


class ValidityStatus(StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


# Pack-era aliases keep existing EvidencePack JSON readable.
LIMITATION_ALIASES: dict[str, LimitationCode] = {
    "MIDI_UNREAD": LimitationCode.MIDI_UNAVAILABLE,
}


MEASURED_KINDS = frozenset(
    {
        EvidenceKind.MEASUREMENT,
        EvidenceKind.OBSERVATION,
        EvidenceKind.FACT,
        EvidenceKind.SESSION_ENTITY,
        EvidenceKind.STATE_TOKEN,
        EvidenceKind.RELATIONSHIP,
    }
)
INTERPRETIVE_KINDS = frozenset(
    {
        EvidenceKind.INTERPRETATION,
        EvidenceKind.DIAGNOSIS,
    }
)
CANONICAL_LIMITATIONS = frozenset(item.value for item in LimitationCode)


class NumericProvenance(StrEnum):
    """Why a number appears in model text. Only some kinds may skip evidence match."""

    MEASUREMENT_VALUE = "MEASUREMENT_VALUE"
    SESSION_STATE_VALUE = "SESSION_STATE_VALUE"
    CAPABILITY_LIMIT = "CAPABILITY_LIMIT"
    ANALYSIS_METADATA = "ANALYSIS_METADATA"
    TIME_POSITION = "TIME_POSITION"
    TIME_RANGE = "TIME_RANGE"
    CONTRACT_CONSTANT = "CONTRACT_CONSTANT"
    UNKNOWN = "UNKNOWN"


class EvidenceRequestKind(StrEnum):
    CAPTURE_VIEW = "CAPTURE_VIEW"
    READ_MIDI = "READ_MIDI"
    READ_DEVICE_PARAMETERS = "READ_DEVICE_PARAMETERS"
    ANALYZE_REGION = "ANALYZE_REGION"
    READ_ROUTING = "READ_ROUTING"


class EvidenceRequestDecision(StrEnum):
    ALLOWED = "ALLOWED"
    ALREADY_CACHED = "ALREADY_CACHED"
    UNSUPPORTED = "UNSUPPORTED"
    TOO_EXPENSIVE = "TOO_EXPENSIVE"
    REDUNDANT = "REDUNDANT"


class EntityKind(StrEnum):
    TRACK = "TRACK"
    DEVICE = "DEVICE"
    CLIP = "CLIP"


class EvidenceRef(BaseModel):
    evidence_id: str


class ObservationLimitation(BaseModel):
    code: str
    detail: str = ""
    precision_ms: float | None = None
    limitation_id: str | None = None
    capability_ms: list[float] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    evidence_id: str
    kind: EvidenceKind = EvidenceKind.MEASUREMENT
    source_ref: str
    region: str
    view: str | None = None
    signal_point: str | None = None
    analysis_version: str
    name: str
    value: Any
    unit: str | None = None
    quality: CaptureQuality = CaptureQuality.OK
    confidence: float | None = None
    limitations: list[str] = Field(default_factory=list)
    project_token: str | None = None
    audible_token: str | None = None
    target_token: str | None = None


class EntityRef(BaseModel):
    entity_id: str
    kind: EntityKind
    name: str
    role: str | None = None
    parent_id: str | None = None
    class_name: str | None = None


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_kind: EvidenceRequestKind
    why_needed: str
    target: str
    region: str
    expected_information_gain: str


class EvidencePack(BaseModel):
    pack_id: str
    analysis_version: str
    prompt_schema_version: str
    region: str
    project_token: str
    audible_token: str
    target_token: str | None = None
    alignment_claim: str = "LIMITED"
    alignment_envelope_ms: float = 52.0
    items: list[EvidenceItem]
    entities: list[EntityRef] = Field(default_factory=list)
    limitations: list[ObservationLimitation] = Field(default_factory=list)
    domain: str = "lowend"

    def by_id(self) -> dict[str, EvidenceItem]:
        return {item.evidence_id: item for item in self.items}

    def entity_by_id(self) -> dict[str, EntityRef]:
        return {item.entity_id: item for item in self.entities}
