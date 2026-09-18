"""PHYSICAL_DSP_V2 public contract. Factual measurements only. No musical judgment."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from copilot.schemas.evidence import CaptureQuality, EvidenceItem, EvidenceKind


ANALYZER_VERSION = "2.0.0"
CAPABILITY_ID = "PHYSICAL_DSP_V2"


class DspQuality(StrEnum):
    OK = "OK"
    LIMITED = "LIMITED"
    UNSUPPORTED = "UNSUPPORTED"


class DspGranularity(StrEnum):
    TRACK = "TRACK"
    REGION = "REGION"
    BAR = "BAR"
    BEAT = "BEAT"
    EVENT = "EVENT"
    SOURCE = "SOURCE"
    SOURCE_PAIR = "SOURCE_PAIR"


class DspSubjectKind(StrEnum):
    SOURCE = "SOURCE"
    SOURCE_PAIR = "SOURCE_PAIR"
    MIX = "MIX"


class AnalyzerFamily(StrEnum):
    LEVEL_DYNAMICS = "physical-dsp-v2.level-dynamics"
    SPECTRUM = "physical-dsp-v2.spectrum"
    TRANSIENTS = "physical-dsp-v2.transients"
    STEREO = "physical-dsp-v2.stereo"
    RHYTHM = "physical-dsp-v2.rhythm"
    TONAL = "physical-dsp-v2.tonal"
    TIMBRE = "physical-dsp-v2.timbre"
    RELATIONAL = "physical-dsp-v2.relational"
    FULLMIX_WRAP = "physical-dsp-v2.fullmix-wrap"
    LOWEND_WRAP = "physical-dsp-v2.lowend-wrap"
    GRANULARITY = "physical-dsp-v2.granularity"


class DspSubject(BaseModel):
    kind: DspSubjectKind = DspSubjectKind.SOURCE
    source_id: str | None = None
    source_id_b: str | None = None
    label: str | None = None


class TimeSpan(BaseModel):
    start_s: float
    end_s: float
    start_qn: float | None = None
    end_qn: float | None = None
    tempo_bpm: float | None = None
    granularity: DspGranularity = DspGranularity.REGION

    @property
    def duration_s(self) -> float:
        return max(0.0, float(self.end_s) - float(self.start_s))


class MeasuredValue(BaseModel):
    name: str
    value: Any
    unit: str | None = None
    confidence: float | None = None

    def __init__(
        self,
        name: str,
        value: Any = None,
        unit: str | None = None,
        confidence: float | None = None,
        **data: Any,
    ) -> None:
        super().__init__(name=name, value=value, unit=unit, confidence=confidence, **data)


class DspLimitation(BaseModel):
    code: str
    detail: str = ""

    def __init__(self, code: str, detail: str = "", **data: Any) -> None:
        super().__init__(code=code, detail=detail, **data)


class DspProvenance(BaseModel):
    provider_id: str
    provider_version: str
    method: str
    parameters_hash: str
    source_artifact_hash: str
    cache_key: str
    cache_hit: bool = False


class DspObservation(BaseModel):
    """One typed factual observation. Public API is this model, not a free dict."""

    observation_id: str
    analyzer_id: str
    analyzer_version: str = ANALYZER_VERSION
    subject: DspSubject
    time_span: TimeSpan
    values: list[MeasuredValue] = Field(default_factory=list)
    quality: DspQuality = DspQuality.OK
    limitations: list[DspLimitation] = Field(default_factory=list)
    provenance: DspProvenance
    source_artifact_hash: str

    def value_map(self) -> dict[str, MeasuredValue]:
        return {item.name: item for item in self.values}

    def get(self, name: str) -> MeasuredValue | None:
        return self.value_map().get(name)

    def limitation_codes(self) -> list[str]:
        return [item.code for item in self.limitations]

    def to_evidence_item(
        self,
        measured: MeasuredValue,
        *,
        project_token: str | None = None,
        audible_token: str | None = None,
        target_token: str | None = None,
        region: str | None = None,
    ) -> EvidenceItem:
        quality = (
            CaptureQuality.OK
            if self.quality is DspQuality.OK
            else CaptureQuality.LIMITED
        )
        kind = EvidenceKind.MEASUREMENT
        if (
            self.analyzer_id.startswith(AnalyzerFamily.RELATIONAL.value)
            or self.subject.kind is DspSubjectKind.SOURCE_PAIR
        ):
            kind = EvidenceKind.RELATIONSHIP
        return EvidenceItem(
            evidence_id=f"pdsp.{self.analyzer_id}.{measured.name}.{self.observation_id[:12]}",
            kind=kind,
            source_ref=f"artifact:{self.source_artifact_hash}",
            region=region or f"{self.time_span.start_s:.6f}:{self.time_span.end_s:.6f}",
            analysis_version=self.analyzer_version,
            name=measured.name,
            value=measured.value,
            unit=measured.unit,
            quality=quality,
            confidence=measured.confidence,
            limitations=self.limitation_codes(),
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
        )

    def to_evidence_items(
        self,
        *,
        project_token: str | None = None,
        audible_token: str | None = None,
        target_token: str | None = None,
        region: str | None = None,
    ) -> list[EvidenceItem]:
        items = [
            self.to_evidence_item(
                measured,
                project_token=project_token,
                audible_token=audible_token,
                target_token=target_token,
                region=region,
            )
            for measured in self.values
        ]
        for lim in self.limitations:
            items.append(
                EvidenceItem(
                    evidence_id=f"pdsp.{self.analyzer_id}.lim.{lim.code}.{self.observation_id[:12]}",
                    kind=EvidenceKind.LIMITATION,
                    source_ref=f"artifact:{self.source_artifact_hash}",
                    region=region or f"{self.time_span.start_s:.6f}:{self.time_span.end_s:.6f}",
                    analysis_version=self.analyzer_version,
                    name=lim.code,
                    value=lim.detail,
                    quality=CaptureQuality.LIMITED,
                    limitations=[lim.code],
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                )
            )
        return items


class DspBundle(BaseModel):
    """Typed pipeline result. Not an anonymous analyzer dict."""

    capability_id: str = CAPABILITY_ID
    analyzer_version: str = ANALYZER_VERSION
    observations: list[DspObservation] = Field(default_factory=list)
    wall_s: float = 0.0
    cpu_s: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    quality: DspQuality = DspQuality.OK
    limitations: list[DspLimitation] = Field(default_factory=list)

    def by_analyzer(self, analyzer_id: str) -> list[DspObservation]:
        return [obs for obs in self.observations if obs.analyzer_id == analyzer_id]
