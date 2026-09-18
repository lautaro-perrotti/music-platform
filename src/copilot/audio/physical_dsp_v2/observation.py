"""Build typed DspObservation rows with provenance and judgment scan."""

from __future__ import annotations

from typing import Any

from copilot.audio.physical_dsp_v2.cache import parameters_hash
from copilot.audio.physical_dsp_v2.contract import ANALYZER_VERSION
from copilot.audio.physical_dsp_v2.judgment import assert_no_judgment
from copilot.schemas.dsp import (
    DspLimitation,
    DspObservation,
    DspProvenance,
    DspQuality,
    DspSubject,
    MeasuredValue,
    TimeSpan,
)


def build_observation(
    *,
    observation_id: str,
    analyzer_id: str,
    subject: DspSubject,
    time_span: TimeSpan,
    values: list[MeasuredValue],
    quality: DspQuality,
    limitations: list[DspLimitation],
    provider_id: str,
    provider_version: str,
    method: str,
    params: dict[str, Any],
    source_artifact_hash: str,
    cache_key: str,
    cache_hit: bool = False,
    analyzer_version: str = ANALYZER_VERSION,
) -> DspObservation:
    obs = DspObservation(
        observation_id=observation_id,
        analyzer_id=analyzer_id,
        analyzer_version=analyzer_version,
        subject=subject,
        time_span=time_span,
        values=values,
        quality=quality,
        limitations=list(limitations),
        provenance=DspProvenance(
            provider_id=provider_id,
            provider_version=provider_version,
            method=method,
            parameters_hash=parameters_hash(params),
            source_artifact_hash=source_artifact_hash,
            cache_key=cache_key,
            cache_hit=cache_hit,
        ),
        source_artifact_hash=source_artifact_hash,
    )
    assert_no_judgment(obs)
    return obs


def merge_quality(*qualities: DspQuality) -> DspQuality:
    if DspQuality.UNSUPPORTED in qualities:
        return DspQuality.UNSUPPORTED
    if DspQuality.LIMITED in qualities:
        return DspQuality.LIMITED
    return DspQuality.OK
