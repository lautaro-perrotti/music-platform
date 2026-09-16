"""Pack SourceAudioObservation into EvidencePack for Astra."""

from __future__ import annotations

from typing import Any

from copilot.reasoning.schema import ANALYSIS_VERSION, SCHEMA_VERSION
from copilot.schemas.evidence import (
    CaptureQuality,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)

OBS_VERSION = "source-audio-trace-1"
DOMAIN = "fullmix+lowend+causal-trace+source-audio"


def merge_source_audio_into_pack(
    pack: EvidencePack,
    source_payload: dict[str, Any],
) -> EvidencePack:
    region = pack.region
    project_token = pack.project_token
    audible_token = pack.audible_token
    target_token = pack.target_token
    source_ref = f"source_audio:{source_payload.get('artifact') or 'inline'}"
    items = list(pack.items)

    items.append(
        _fact(
            "sa.event.id",
            "source_audio_event_id",
            source_payload.get("event_id"),
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )
    items.append(
        _fact(
            "sa.event.qn_range",
            "source_audio_event_qn_range",
            source_payload.get("event_qn_range"),
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )
    items.append(
        _fact(
            "sa.decision",
            "source_audio_decision",
            source_payload.get("decision"),
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )

    rows = []
    for obs in source_payload.get("observations") or []:
        track = str(obs.get("track") or "unknown")
        key = track.replace(" ", "_").lower()
        rows.append(
            {
                "track": track,
                "signal_class": obs.get("signal_class"),
                "midi_audio_relation": obs.get("midi_audio_relation"),
                "event_rms": obs.get("event_rms"),
                "event_peak": obs.get("event_peak"),
                "relative_drop_db": obs.get("relative_drop_db"),
                "main_event_signal_class": obs.get("main_event_signal_class"),
                "midi_active_count": obs.get("midi_active_count"),
            }
        )
        items.extend(
            [
                _fact(
                    f"sa.{key}.signal_class",
                    "source_audio_signal_class",
                    obs.get("signal_class"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _fact(
                    f"sa.{key}.midi_audio_relation",
                    "source_audio_midi_relation",
                    obs.get("midi_audio_relation"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _meas(
                    f"sa.{key}.event_rms",
                    "source_audio_event_rms",
                    obs.get("event_rms"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _meas(
                    f"sa.{key}.event_peak",
                    "source_audio_event_peak",
                    obs.get("event_peak"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                ),
                _meas(
                    f"sa.{key}.relative_drop_db",
                    "source_audio_relative_drop_db",
                    obs.get("relative_drop_db"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                    unit="dB",
                ),
                _meas(
                    f"sa.{key}.midi_active_count",
                    "source_audio_midi_active_count",
                    obs.get("midi_active_count"),
                    region,
                    project_token,
                    audible_token,
                    target_token,
                    source_ref,
                    unit="count",
                ),
            ]
        )
    items.append(
        _fact(
            "sa.observations",
            "source_audio_observations",
            rows,
            region,
            project_token,
            audible_token,
            target_token,
            source_ref,
        )
    )

    limitations = list(pack.limitations)
    limitations.append(
        ObservationLimitation(
            code="SOURCE_AUDIO_TRACE_V1",
            detail=(
                f"{OBS_VERSION}: Post Mixer source audio for MIDI-active tracks "
                "during Main gap. OBSERVED≠CAUSAL≠JUDGMENT."
            ),
        )
    )
    for note in source_payload.get("limitations") or []:
        limitations.append(ObservationLimitation(code="SOURCE_AUDIO_LIMITATION", detail=str(note)))
    for obs in source_payload.get("observations") or []:
        for note in obs.get("limitations") or []:
            limitations.append(
                ObservationLimitation(code="SOURCE_AUDIO_OBS_LIMITATION", detail=str(note))
            )

    return EvidencePack(
        pack_id=pack.pack_id,
        analysis_version=f"{ANALYSIS_VERSION}+{OBS_VERSION}",
        prompt_schema_version=SCHEMA_VERSION,
        region=pack.region,
        project_token=pack.project_token,
        audible_token=pack.audible_token,
        target_token=pack.target_token,
        alignment_claim=pack.alignment_claim,
        alignment_envelope_ms=pack.alignment_envelope_ms,
        items=items,
        entities=list(pack.entities),
        limitations=limitations,
        domain=DOMAIN,
    )


def _fact(
    evidence_id: str,
    name: str,
    value: Any,
    region: str,
    project_token: str,
    audible_token: str,
    target_token: str | None,
    source_ref: str,
    unit: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=EvidenceKind.FACT,
        source_ref=source_ref,
        region=region,
        analysis_version=OBS_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.OK,
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
    )


def _meas(
    evidence_id: str,
    name: str,
    value: Any,
    region: str,
    project_token: str,
    audible_token: str,
    target_token: str | None,
    source_ref: str,
    unit: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=EvidenceKind.MEASUREMENT,
        source_ref=source_ref,
        region=region,
        analysis_version=OBS_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.OK,
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
    )
