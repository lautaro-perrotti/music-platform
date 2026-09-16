from __future__ import annotations

from typing import Any, Mapping

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

DOMAIN = "lowend"


def pack_from_lowend_features(
    *,
    region_id: str,
    region: str,
    features: dict[str, Any],
    views: Mapping[str, Any],
    project_token: str,
    audible_token: str,
    target_token: str | None,
    kick_name: str,
    bass_name: str,
    alignment_envelope_ms: float = 52.0,
    midi_unread: bool = True,
    device_params_unread: bool = True,
    routing_unread: bool = True,
) -> EvidencePack:
    """Typed evidence only. No expected diagnosis. No fixture names."""
    if not features.get("ok"):
        raise ValueError(f"features not ok: {features.get('missing')}")
    temporal = features["temporal"]
    spectral = features["spectral"]
    attacks = features["attacks"]
    n_kick = int(temporal.get("kick_events") or 0)
    n_overlap = int(temporal.get("events_with_overlap") or 0)
    co_n = int(spectral.get("events_with_co_concentration") or 0)
    band = spectral.get("dominant_attack_band")
    band_hz = _band_to_hz(band)
    shared = bool(spectral.get("simultaneous")) or co_n > 0
    items = [
        _item(
            "ev.kick.count",
            "kick_attack_count",
            n_kick,
            unit="count",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
            view="TRACK_ISOLATED",
        ),
        _item(
            "ev.overlap.count",
            "overlap_events",
            n_overlap,
            unit="count",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
        ),
        _item(
            "ev.persist.energy",
            "median_bass_energy_50_200ms_after_kick",
            float(temporal.get("median_persist") or 0.0),
            unit=None,
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
        ),
        _item(
            "ev.persist.window",
            "persist_analysis_window_ms",
            [50.0, 200.0],
            unit="ms",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
            kind=EvidenceKind.FACT,
        ),
        _item(
            "ev.bass.on",
            "bass_energy_on_attack",
            float(temporal.get("median_on_attack") or 0.0),
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
        ),
        _item(
            "ev.bass.off",
            "bass_energy_off_kick",
            float(temporal.get("off_kick_mean") or 0.0),
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
        ),
        _item(
            "ev.kick.transient",
            "kick_transient_rms",
            float(features.get("kick_rms") or 0.0),
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
        ),
        _item(
            "ev.spectral.co",
            "co_concentrated_attack_count",
            co_n,
            unit="count",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
        ),
        _item(
            "ev.shared",
            "simultaneous_low_band",
            shared,
            kind=EvidenceKind.FACT,
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
        ),
        _item(
            "ev.raw.onsets",
            "raw_kick_onsets_before_prune",
            int(attacks.get("raw_count") or 0),
            unit="count",
            region=region,
            project_token=project_token,
            audible_token=audible_token,
            target_token=target_token,
        ),
    ]
    if band_hz is not None:
        items.append(
            _item(
                "ev.band",
                "dominant_attack_band_hz",
                band_hz,
                unit="Hz",
                region=region,
                project_token=project_token,
                audible_token=audible_token,
                target_token=target_token,
            )
        )
    centroid = _centroid_hz(spectral)
    if centroid is not None:
        items.append(
            _item(
                "ev.centroid",
                "centroid_hz",
                centroid,
                unit="Hz",
                region=region,
                project_token=project_token,
                audible_token=audible_token,
                target_token=target_token,
            )
        )
    drop = (features.get("attack_context") or {}).get("median_drop_at_kicks")
    if drop is not None:
        items.append(
            _item(
                "ev.drop",
                "context_rms_drop",
                float(drop),
                region=region,
                project_token=project_token,
                audible_token=audible_token,
                target_token=target_token,
                view="TRACK_CONTEXT_REMOVAL",
            )
        )
    limitations = [
        ObservationLimitation(
            code="ALIGNMENT_LIMITED",
            limitation_id="ALIGNMENT_LIMITED_52MS",
            detail="Capture alignment LIMITED. Microtiming 5-10 ms is not supported.",
            precision_ms=alignment_envelope_ms,
            capability_ms=[5.0, 10.0, alignment_envelope_ms],
        ),
        ObservationLimitation(
            code="DSP_PERSIST_IS_ENERGY",
            detail=(
                "median_bass_energy_50_200ms_after_kick is normalized energy in a "
                "50-200 ms window, not a measured decay duration in milliseconds."
            ),
        ),
    ]
    if midi_unread:
        limitations.append(ObservationLimitation(code="MIDI_UNREAD", detail="Clip notes were not read."))
    if device_params_unread:
        limitations.append(
            ObservationLimitation(code="DEVICE_PARAMS_UNREAD", detail="Envelope state unknown.")
        )
    if routing_unread:
        limitations.append(ObservationLimitation(code="ROUTING_UNKNOWN", detail="Sidechain/routing unread."))
    kick_asset = views.get("kick")
    quality = CaptureQuality.LIMITED
    capture_quality = getattr(kick_asset, "capture_quality", None) if kick_asset else None
    if capture_quality:
        try:
            quality = CaptureQuality(capture_quality)
        except ValueError:
            quality = CaptureQuality.LIMITED
    for item in items:
        item.quality = quality
    return EvidencePack(
        pack_id=region_id,
        analysis_version=ANALYSIS_VERSION,
        prompt_schema_version=SCHEMA_VERSION,
        region=region,
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
        alignment_claim="LIMITED",
        alignment_envelope_ms=alignment_envelope_ms,
        items=items,
        entities=[
            EntityRef(entity_id="track:kick", kind=EntityKind.TRACK, name=kick_name, role="kick"),
            EntityRef(entity_id="track:bass", kind=EntityKind.TRACK, name=bass_name, role="bass"),
        ],
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
    view: str | None = "TRACK_ISOLATED",
    kind: EvidenceKind = EvidenceKind.MEASUREMENT,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        source_ref="asset:live",
        region=region,
        view=view,
        signal_point="TRACK_POST_MIXER",
        analysis_version=ANALYSIS_VERSION,
        name=name,
        value=value,
        unit=unit,
        quality=CaptureQuality.LIMITED,
        limitations=["ALIGNMENT_LIMITED"],
        project_token=project_token,
        audible_token=audible_token,
        target_token=target_token,
    )


def _band_to_hz(band: str | None) -> list[float] | None:
    if not band or "-" not in str(band):
        return None
    lo, hi = str(band).split("-", 1)
    try:
        return [float(lo), float(hi)]
    except ValueError:
        return None


def _centroid_hz(spectral: dict[str, Any]) -> float | None:
    scores = spectral.get("band_mean_joint") or {}
    if not scores:
        return None
    best = max(scores, key=scores.get)
    hz = _band_to_hz(best)
    if not hz:
        return None
    return (hz[0] + hz[1]) / 2.0
