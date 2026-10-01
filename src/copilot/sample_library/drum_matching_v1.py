"""Evidence-explicit acoustic shortlist for detected drum events.

This ranks indexed local files from comparable factual descriptors. It is a
retrieval aid, not a perceptual similarity or "best sounding" judgment.
Filename/folder role labels are returned as separate weak metadata and never
gate or determine acoustic ranking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copilot.audio.drum_events_v1 import DrumEventV1
from copilot.sample_library.schemas import AssetStatus, LibraryIndex, SampleAsset


DRUM_MATCHING_VERSION = "drum-acoustic-shortlist-v1"
SUPPORTED_ROLES = {"KICK", "CLOSED_HAT"}
MAX_CANDIDATES = 5


def _unit_delta(left: float, right: float, scale: float) -> float:
    return min(1.0, abs(left - right) / scale)


def _components(event: DrumEventV1, asset: SampleAsset) -> list[dict[str, Any]]:
    source = event.role_hypothesis.features
    if source is None:
        return []

    output: list[dict[str, Any]] = []
    if source.spectral_centroid_hz is not None and asset.descriptors.spectral_centroid_hz is not None:
        delta = _unit_delta(
            source.spectral_centroid_hz,
            asset.descriptors.spectral_centroid_hz,
            8_000.0,
        )
        output.append({
            "feature": "spectral_centroid",
            "source_value_hz": source.spectral_centroid_hz,
            "sample_value_hz": asset.descriptors.spectral_centroid_hz,
            "normalized_absolute_delta": round(delta, 6),
            "limitation": "attack-window versus whole-sample centroid",
        })

    if event.effective_role == "KICK":
        source_value = source.low_band_energy_fraction_20_150_hz
        sample_value = asset.descriptors.low_band_energy
        feature = "low_frequency_energy_tendency"
        limitation = "source band 20-150 Hz; library descriptor band 20-250 Hz"
    elif event.effective_role == "CLOSED_HAT":
        source_value = source.high_band_energy_fraction_2000_12000_hz
        sample_value = asset.descriptors.high_band_energy
        feature = "high_frequency_energy_tendency"
        limitation = "source band 2-12 kHz; library descriptor band 4-16 kHz"
    else:
        return []

    if source_value is not None and sample_value is not None:
        output.append({
            "feature": feature,
            "source_value_fraction": source_value,
            "sample_value_fraction": sample_value,
            "normalized_absolute_delta": round(_unit_delta(source_value, sample_value, 1.0), 6),
            "limitation": limitation,
        })
    return output


def _asset_matches_indexed_file_state(asset: SampleAsset) -> bool:
    """Avoid ranking a disappeared or plainly stale indexed library entry."""
    try:
        stat = Path(asset.path).stat()
    except OSError:
        return False
    if stat.st_size != asset.size_bytes:
        return False
    return asset.mtime_ns is None or stat.st_mtime_ns == asset.mtime_ns


def rank_event_candidates(
    event: DrumEventV1,
    index: LibraryIndex,
    *,
    top_k: int = MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """Return up to five deterministic candidates with component evidence.

    Only inferred or human-confirmed KICK/CLOSED_HAT events are eligible.
    Source features are attack-window measurements while most library
    descriptors cover a complete file; that comparison limitation is explicit
    in every component. No role inference from library names participates in
    the distance.
    """
    if not 1 <= top_k <= MAX_CANDIDATES:
        raise ValueError(f"top_k must be between 1 and {MAX_CANDIDATES}")
    role = event.effective_role
    if role not in SUPPORTED_ROLES or event.role_hypothesis.features is None:
        return []
    if event.role_hypothesis.status not in {"INFERRED", "HUMAN_VERIFIED"} and event.human_correction is None:
        return []

    ranked: list[tuple[float, str, SampleAsset, list[dict[str, Any]]]] = []
    for asset in index.assets.values():
        if asset.status != AssetStatus.INDEXED or not _asset_matches_indexed_file_state(asset):
            continue
        components = _components(event, asset)
        if not components:
            continue
        distance = sum(item["normalized_absolute_delta"] for item in components) / len(components)
        ranked.append((distance, asset.sha256, asset, components))

    ranked.sort(key=lambda row: (row[0], row[1]))
    return [
        {
            "asset_id": asset.id,
            "filename": asset.filename,
            "library_path_label": asset.relative_path,
            "sha256": asset.sha256,
            "ranking_distance": round(distance, 6),
            "ranking_distance_semantics": "mean normalized absolute descriptor delta; lower is closer; not perceptual quality",
            "components": components,
            "sample_role_metadata": {
                "role": asset.semantic_role.value,
                "classification_confidence": asset.classification_confidence,
                "tags": asset.tags,
                "used_to_filter_or_rank": False,
            },
            "provisional": True,
            "human_selected": False,
            "selected_for_realization": False,
        }
        for distance, _, asset, components in ranked[:top_k]
    ]


def rank_role_family_candidates(
    events: list[DrumEventV1],
    index: LibraryIndex,
    *,
    role: str,
    eligible_sha256: set[str],
    top_k: int = MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """Rank a provider-declared candidate pool across a source role family.

    Provider catalog labels constrain only the candidate pool; they are
    surfaced as catalog metadata and never treated as acoustic truth. Within
    that pool, the existing factual descriptor comparison is reused and
    averaged over all eligible source events of the requested inferred role.
    """
    if role not in SUPPORTED_ROLES:
        raise ValueError(f"unsupported drum role: {role}")
    if not 1 <= top_k <= MAX_CANDIDATES:
        raise ValueError(f"top_k must be between 1 and {MAX_CANDIDATES}")
    reference_events = [
        event for event in events
        if event.effective_role == role
        and event.role_hypothesis.features is not None
        and (event.role_hypothesis.status in {"INFERRED", "HUMAN_VERIFIED"} or event.human_correction is not None)
    ]
    if not reference_events or not eligible_sha256:
        return []

    ranked: list[tuple[float, str, SampleAsset, list[dict[str, Any]], int]] = []
    for asset in index.assets.values():
        if asset.status != AssetStatus.INDEXED or asset.sha256 not in eligible_sha256:
            continue
        if not _asset_matches_indexed_file_state(asset):
            continue
        components_by_feature: dict[str, list[dict[str, Any]]] = {}
        event_distances: list[float] = []
        for event in reference_events:
            components = _components(event, asset)
            if not components:
                continue
            event_distances.append(sum(row["normalized_absolute_delta"] for row in components) / len(components))
            for component in components:
                components_by_feature.setdefault(component["feature"], []).append(component)
        if not event_distances:
            continue
        summaries: list[dict[str, Any]] = []
        for feature, rows in sorted(components_by_feature.items()):
            summary: dict[str, Any] = {
                "feature": feature,
                "mean_normalized_absolute_delta": round(
                    sum(row["normalized_absolute_delta"] for row in rows) / len(rows), 6
                ),
                "comparison_count": len(rows),
                "limitation": rows[0]["limitation"],
            }
            for field in ("source_value_hz", "sample_value_hz", "source_value_fraction", "sample_value_fraction"):
                values = [row[field] for row in rows if row.get(field) is not None]
                if values:
                    summary[f"mean_{field}"] = round(sum(values) / len(values), 6)
            summaries.append(summary)
        ranked.append((sum(event_distances) / len(event_distances), asset.sha256, asset, summaries, len(event_distances)))

    ranked.sort(key=lambda row: (row[0], row[1]))
    return [
        {
            "asset_id": asset.id,
            "filename": asset.filename,
            "library_path_label": asset.relative_path,
            "sha256": asset.sha256,
            "ranking_distance": round(distance, 6),
            "ranking_distance_semantics": "mean factual descriptor distance across source events; lower is closer; not perceptual quality",
            "compared_source_event_count": compared_count,
            "reference_source_event_count": len(reference_events),
            "components": components,
            "sample_role_metadata": {
                "role": asset.semantic_role.value,
                "classification_confidence": asset.classification_confidence,
                "tags": asset.tags,
                "used_to_filter_or_rank": False,
            },
            "provisional": True,
            "human_selected": False,
            "selected_for_realization": False,
        }
        for distance, _, asset, components, compared_count in ranked[:top_k]
    ]


def match_drum_events(
    events: list[DrumEventV1],
    index: LibraryIndex,
    *,
    top_k: int = MAX_CANDIDATES,
) -> dict[str, Any]:
    """Rank candidates per eligible event; preserve role uncertainty and provenance."""
    by_event = {event.event_id: rank_event_candidates(event, index, top_k=top_k) for event in events}
    matched = sum(bool(candidates) for candidates in by_event.values())
    return {
        "status": "CANDIDATES_READY" if matched else "NO_COMPARABLE_CANDIDATES",
        "version": DRUM_MATCHING_VERSION,
        "indexed_root_count": len(index.roots),
        "indexed_asset_count": sum(asset.status == AssetStatus.INDEXED for asset in index.assets.values()),
        "event_count": len(events),
        "events_with_candidates": matched,
        "candidate_limit_per_event": top_k,
        "ranking_is_quality_judgment": False,
        "event_candidates": by_event,
    }


__all__ = [
    "DRUM_MATCHING_VERSION",
    "MAX_CANDIDATES",
    "match_drum_events",
    "rank_event_candidates",
    "rank_role_family_candidates",
]
