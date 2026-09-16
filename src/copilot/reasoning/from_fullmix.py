from __future__ import annotations

from typing import Any

from copilot.reasoning.schema import ANALYSIS_VERSION, SCHEMA_VERSION
from copilot.schemas.evidence import (
    CaptureQuality,
    EntityKind,
    EntityRef,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ObservationLimitation,
)
from copilot.schemas.fullmix import FullMixObservation

# Analyzer id only — do not import fullmix module (pulls capture stack).
ANALYZER_ID = "fullmix-obs-1"
DOMAIN = "fullmix+lowend"


def fullmix_items(
    obs: FullMixObservation,
    *,
    project_token: str,
    audible_token: str,
    target_token: str | None = None,
) -> list[EvidenceItem]:
    """Typed factual items only. No musical judgment."""
    region = obs.region_label
    items: list[EvidenceItem] = [
        _item(
            "fm.energy.event_count",
            "fullmix_energy_event_count",
            len(obs.energy_events),
            unit="count",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
            source_ref=f"asset:{obs.audio_sha256}",
        ),
        _item(
            "fm.energy.frame_count",
            "fullmix_energy_frame_count",
            len(obs.energy_frames),
            unit="count",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
            source_ref=f"asset:{obs.audio_sha256}",
        ),
        _item(
            "fm.window_ms",
            "fullmix_window_ms",
            float(obs.window_s * 1000.0),
            unit="ms",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
            source_ref=f"asset:{obs.audio_sha256}",
            kind=EvidenceKind.FACT,
        ),
        _item(
            "fm.hop_ms",
            "fullmix_hop_ms",
            float(obs.hop_s * 1000.0),
            unit="ms",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
            source_ref=f"asset:{obs.audio_sha256}",
            kind=EvidenceKind.FACT,
        ),
        _item(
            "fm.strong_dip_threshold_db",
            "fullmix_strong_dip_threshold_db",
            float((obs.thresholds or {}).get("strong_dip_db") or 0.0),
            unit="dB",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
            source_ref=f"config:{obs.configuration_hash}",
            kind=EvidenceKind.FACT,
        ),
    ]
    for idx, event in enumerate(obs.energy_events):
        prefix = f"fm.event.{idx}"
        items.extend(
            [
                _item(
                    f"{prefix}.kind",
                    "fullmix_energy_event_kind",
                    event.kind.value,
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                    kind=EvidenceKind.FACT,
                ),
                _item(
                    f"{prefix}.start_s",
                    "fullmix_energy_event_start_s",
                    event.start_s,
                    unit="s",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
                _item(
                    f"{prefix}.end_s",
                    "fullmix_energy_event_end_s",
                    event.end_s,
                    unit="s",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
                _item(
                    f"{prefix}.duration_ms",
                    "fullmix_energy_event_duration_ms",
                    float(event.duration_s * 1000.0),
                    unit="ms",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
                _item(
                    f"{prefix}.min_rms",
                    "fullmix_energy_event_min_rms",
                    event.minimum_rms,
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
                _item(
                    f"{prefix}.reference_rms",
                    "fullmix_energy_event_reference_rms",
                    event.reference_rms,
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
                _item(
                    f"{prefix}.relative_drop_db",
                    "fullmix_energy_event_relative_drop_db",
                    event.relative_drop_db,
                    unit="dB",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
                _item(
                    f"{prefix}.similarity_count",
                    "fullmix_energy_event_similarity_count",
                    event.event_similarity_count,
                    unit="count",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
                _item(
                    f"{prefix}.repetition_strength",
                    "fullmix_energy_event_repetition_strength",
                    float(event.repetition_strength or 0.0),
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
            ]
        )
        if event.approx_period_s is not None:
            items.append(
                _item(
                    f"{prefix}.approx_period_s",
                    "fullmix_energy_event_approx_period_s",
                    event.approx_period_s,
                    unit="s",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                )
            )
            items.append(
                _item(
                    f"{prefix}.approx_period_ms",
                    "fullmix_energy_event_approx_period_ms",
                    float(event.approx_period_s * 1000.0),
                    unit="ms",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                )
            )

    if obs.transient is not None:
        tr = obs.transient
        items.extend(
            [
                _item(
                    "fm.transient.count",
                    "fullmix_transient_count",
                    tr.transient_count,
                    unit="count",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
                _item(
                    "fm.transient.density",
                    "fullmix_transient_density_per_s",
                    tr.transient_density_per_s,
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                ),
                _item(
                    "fm.transient.precision_ms",
                    "fullmix_transient_precision_ms",
                    tr.precision_ms,
                    unit="ms",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                    kind=EvidenceKind.FACT,
                ),
            ]
        )
        if tr.median_ioi_s is not None:
            items.append(
                _item(
                    "fm.transient.median_ioi_ms",
                    "fullmix_transient_median_ioi_ms",
                    float(tr.median_ioi_s * 1000.0),
                    unit="ms",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                )
            )

    # Compact spectral change summary: counts + strongest |dB| per band.
    by_band: dict[str, list[float]] = {}
    for event in obs.spectral_events:
        by_band.setdefault(event.band, []).append(float(event.relative_change_db))
    items.append(
        _item(
            "fm.spectral.event_count",
            "fullmix_spectral_event_count",
            len(obs.spectral_events),
            unit="count",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
            source_ref=f"asset:{obs.audio_sha256}",
        )
    )
    for band, values in sorted(by_band.items()):
        strongest = max(values, key=lambda x: abs(x))
        items.append(
            _item(
                f"fm.spectral.{band}.strongest_change_db",
                "fullmix_spectral_strongest_change_db",
                strongest,
                unit="dB",
                region=region,
                project_token=project_token,
                audible_token=audible_token,
                target_token=target_token,
                source_ref=f"asset:{obs.audio_sha256}",
            )
        )

    if obs.stereo_frames:
        corr_vals = [f.correlation for f in obs.stereo_frames if f.correlation is not None]
        ratios = [f.side_mid_ratio for f in obs.stereo_frames if f.side_mid_ratio is not None]
        if corr_vals:
            items.append(
                _item(
                    "fm.stereo.median_correlation",
                    "fullmix_stereo_median_correlation",
                    float(sorted(corr_vals)[len(corr_vals) // 2]),
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                )
            )
        if ratios:
            items.append(
                _item(
                    "fm.stereo.median_side_mid_ratio",
                    "fullmix_stereo_median_side_mid_ratio",
                    float(sorted(ratios)[len(ratios) // 2]),
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                )
            )

    if obs.dynamics:
        crests = [d.crest_factor for d in obs.dynamics if d.crest_factor is not None]
        ranges = [d.short_term_dynamic_range_db for d in obs.dynamics if d.short_term_dynamic_range_db is not None]
        if crests:
            items.append(
                _item(
                    "fm.dynamics.median_crest",
                    "fullmix_median_crest_factor",
                    float(sorted(crests)[len(crests) // 2]),
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                )
            )
        if ranges:
            items.append(
                _item(
                    "fm.dynamics.median_range_db",
                    "fullmix_median_short_term_dynamic_range_db",
                    float(sorted(ranges)[len(ranges) // 2]),
                    unit="dB",
                    region=region,
                    project_token=project_token,
                    audible_token=audible_token,
                    target_token=target_token,
                    source_ref=f"asset:{obs.audio_sha256}",
                )
            )
    return items


def merge_lowend_and_fullmix(
    lowend_pack: EvidencePack,
    obs: FullMixObservation,
) -> EvidencePack:
    extra = fullmix_items(
        obs,
        project_token=lowend_pack.project_token,
        audible_token=lowend_pack.audible_token,
        target_token=lowend_pack.target_token,
    )
    limitations = list(lowend_pack.limitations)
    for code in obs.limitations:
        limitations.append(
            ObservationLimitation(
                code=code if code.isupper() or "_" in code else "FULLMIX_NOTE",
                detail=code,
            )
        )
    limitations.append(
        ObservationLimitation(
            code="FULLMIX_ANALYZER",
            detail=f"{ANALYZER_ID} sha={obs.analyzer_sha256[:12]} config={obs.configuration_hash[:12]}",
            capability_ms=[float(obs.window_s * 1000.0), float(obs.hop_s * 1000.0)],
        )
    )
    if obs.transient is not None:
        limitations.append(
            ObservationLimitation(
                code="FULLMIX_TRANSIENT_PRECISION",
                detail="Main-internal transient grid precision.",
                precision_ms=float(obs.transient.precision_ms),
                capability_ms=[float(obs.transient.precision_ms)],
            )
        )
    entities = list(lowend_pack.entities)
    if not any(e.entity_id == "bus:main" for e in entities):
        entities.append(EntityRef(entity_id="bus:main", kind=EntityKind.TRACK, name="Main", role="main"))
    return EvidencePack(
        pack_id=lowend_pack.pack_id,
        analysis_version=f"{ANALYSIS_VERSION}+{ANALYZER_ID}",
        prompt_schema_version=SCHEMA_VERSION,
        region=lowend_pack.region,
        project_token=lowend_pack.project_token,
        audible_token=lowend_pack.audible_token,
        target_token=lowend_pack.target_token,
        alignment_claim=lowend_pack.alignment_claim,
        alignment_envelope_ms=lowend_pack.alignment_envelope_ms,
        items=list(lowend_pack.items) + extra,
        entities=entities,
        limitations=limitations,
        domain=DOMAIN,
    )


def _item(
    evidence_id: str,
    name: str,
    value: Any,
    *,
    region: str,
    project_token: str,
    audible_token: str,
    target_token: str | None,
    unit: str | None = None,
    source_ref: str,
    kind: EvidenceKind = EvidenceKind.MEASUREMENT,
    view: str | None = "MASTER_CONTEXT",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        source_ref=source_ref,
        region=region,
        view=view,
        signal_point="MAIN_FINAL",
        analysis_version=ANALYZER_ID,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.OK,
        limitations=["MEASURE_ONLY"],
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
    )
