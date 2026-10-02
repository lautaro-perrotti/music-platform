"""Read-only Studio projection of an explicitly configured drum event artifact."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

from copilot.audio.drum_events_v1 import DrumEventSetV1
from copilot.audio.drum_events_v1 import apply_human_role_correction
from copilot.sample_library.config import index_path as sample_index_path
from copilot.sample_library.config import load_config as load_sample_library_config
from copilot.sample_library.library_v1 import load_index
from copilot.sample_library.drum_matching_v1 import match_drum_events, rank_role_family_candidates
from copilot.musicplan.drum_reconstruction_v1 import build_drum_reconstruction
from copilot.sample_library.schemas import AssetStatus


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unavailable(status: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "musical_writes": 0,
        "source": None,
        "grid": None,
        "events": [],
        "sample_matching": {"status": "NOT_AVAILABLE", "reason": reason},
    }


def _sample_matching(event_set: DrumEventSetV1) -> dict[str, Any]:
    """Read only the explicitly configured local library index; never scan roots here."""
    try:
        config = load_sample_library_config()
    except ValueError:
        return {"status": "INVALID_LIBRARY_CONFIG", "reason": "Sample-library configuration failed validation."}
    if not config.roots:
        return {
            "status": "NO_ROOTS_CONFIGURED",
            "reason": "Add an explicit library directory, then run sample-library index.",
            "next_command": "python -m copilot.cli sample-library add <explicit-directory>",
            "next_index_command": "python -m copilot.cli sample-library index",
            "disk_scan": False,
            "event_candidates": {},
        }
    configured_index_path = sample_index_path()
    if not configured_index_path.is_file():
        return {
            "status": "NOT_INDEXED",
            "reason": "A library root is configured, but no sample index exists yet.",
            "next_index_command": "python -m copilot.cli sample-library index",
            "event_candidates": {},
        }
    index = load_index(configured_index_path)
    if index is None:
        return {"status": "INVALID_INDEX", "reason": "The configured sample index could not be parsed.", "event_candidates": {}}
    configured_roots = {os.path.normcase(str(Path(root).resolve(strict=False))) for root in config.roots}
    indexed_roots = {os.path.normcase(str(Path(root).resolve(strict=False))) for root in index.roots}
    if configured_roots != indexed_roots:
        return {"status": "INDEX_ROOT_MISMATCH", "reason": "The index does not describe the currently configured roots.", "event_candidates": {}}
    result = match_drum_events(event_set.events, index)
    catalog = _load_provider_catalog(config.roots, index)
    if catalog is None:
        return result

    role_shortlists: dict[str, dict[str, Any]] = {}
    provisional_selections: dict[str, dict[str, Any]] = {}
    for role in ("KICK", "CLOSED_HAT"):
        if role == "KICK":
            eligible = {sha for sha, row in catalog.items() if row["provider_role_claim"] == "KICK"}
            pool_label = "provider-listed kick samples"
        else:
            eligible = {
                sha for sha, row in catalog.items()
                if row["provider_role_claim"] in {"CLOSED_HAT", "HI_HAT_NO_OPEN_LABEL"}
            }
            pool_label = "provider-listed hi-hats excluding assets explicitly labeled Open Hat; subtype remains unverified"
        candidates = rank_role_family_candidates(
            event_set.events,
            index,
            role=role,
            eligible_sha256=eligible,
        )
        for candidate in candidates:
            entry = catalog[candidate["sha256"]]
            candidate["provider_catalog"] = {
                "provider": "CRATE.hiphop",
                "section": entry["provider_section"],
                "label": entry["provider_label"],
                "role_claim": entry["provider_role_claim"],
                "role_claim_is_ground_truth": False,
                "source_url": entry["provider_source_url"],
            }
        selection = None
        if candidates:
            selected = candidates[0]
            selection = {
                "status": "PROVISIONAL_ENGINEERING_SELECTION",
                "asset_id": selected["asset_id"],
                "sha256": selected["sha256"],
                "filename": selected["filename"],
                "ranking_distance": selected["ranking_distance"],
                "selection_reason": (
                    "lowest deterministic mean factual-descriptor distance across eligible source events "
                    "within the provider-declared candidate pool; not a human preference or quality judgment"
                ),
                "human_selected": False,
                "selected_for_realization": False,
            }
            provisional_selections[role] = selection
        role_shortlists[role] = {
            "status": "CANDIDATES_READY" if candidates else "NO_CANDIDATES_IN_PROVIDER_POOL",
            "candidate_pool_label": pool_label,
            "provider_labels_are_metadata_not_acoustic_truth": True,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "provisional_selection": selection,
        }
    result["provider_catalog"] = {
        "provider": "CRATE.hiphop",
        "source_page": "https://crate.hiphop/free-drum-samples/",
        "label_semantics": "official listing metadata; not acoustic ground truth",
    }
    result["role_shortlists"] = role_shortlists
    result["provisional_selections"] = provisional_selections
    return result


def _load_provider_catalog(roots: list[str], index) -> dict[str, dict[str, Any]] | None:
    """Load optional provider labels only from sidecars inside explicit roots."""
    rows_by_sha: dict[str, dict[str, Any]] = {}
    found_sidecar = False
    allowed_claims = {"KICK", "CLOSED_HAT", "HI_HAT_NO_OPEN_LABEL", "OPEN_HAT", "OTHER"}
    for root_text in roots:
        root = Path(root_text).resolve(strict=False)
        sidecar = root / "provider-catalog.json"
        if not sidecar.is_file():
            continue
        found_sidecar = True
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8-sig"))
            if (
                payload.get("schema_version") != "provider-sample-catalog-v1"
                or payload.get("provider") != "CRATE.hiphop"
                or payload.get("source_page") != "https://crate.hiphop/free-drum-samples/"
                or not isinstance(payload.get("entries"), list)
            ):
                return None
            for row in payload["entries"]:
                if not isinstance(row, dict):
                    return None
                sha = str(row.get("sha256", "")).lower()
                if len(sha) != 64 or row.get("provider_role_claim") not in allowed_claims:
                    return None
                if row.get("role_claim_is_ground_truth") is not False:
                    return None
                if not str(row.get("provider_source_url", "")).startswith("https://crate.hiphop/packs/"):
                    return None
                asset = index.assets.get(sha)
                if asset is None or asset.sha256.lower() != sha or asset.status != AssetStatus.INDEXED:
                    return None
                asset_path = Path(asset.path).resolve(strict=False)
                try:
                    asset_path.relative_to(root)
                except ValueError:
                    return None
                if asset.relative_path.replace("\\", "/") != str(row.get("relative_path", "")):
                    return None
                if not asset_path.is_file() or _sha256(asset_path).lower() != sha:
                    return None
                if sha in rows_by_sha:
                    return None
                rows_by_sha[sha] = row
        except Exception:
            return None
    return rows_by_sha if found_sidecar else None


def load_drum_workbench(
    *,
    artifact_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    review_overlay_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load and validate the exact event artifact and its manifest-backed source.

    Paths are supplied only by process configuration; neither the browser nor an
    HTTP request can select arbitrary local files. Absolute paths are never
    returned in the public view.
    """
    configured_artifact = artifact_path or os.environ.get("COPILOT_STUDIO_DRUM_EVENT_SET")
    configured_manifest = manifest_path or os.environ.get("COPILOT_STUDIO_ASSET_SET_MANIFEST")
    if not configured_artifact or not configured_manifest:
        return _unavailable("NOT_CONFIGURED", "Set the Studio event artifact and source manifest explicitly.")

    event_file = Path(configured_artifact).expanduser()
    manifest_file = Path(configured_manifest).expanduser()
    if not event_file.is_file() or not manifest_file.is_file():
        return _unavailable("ARTIFACT_UNAVAILABLE", "Configured event artifact or source manifest is missing.")
    try:
        event_set = DrumEventSetV1.model_validate_json(event_file.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("records"), list):
            return _unavailable("INVALID_MANIFEST", "Source manifest schema is invalid.")
    except Exception as exc:
        return _unavailable("INVALID_ARTIFACT", type(exc).__name__)

    matching = [
        row for row in manifest["records"]
        if isinstance(row, dict)
        and isinstance(row.get("manifest"), dict)
        and row["manifest"].get("source_asset_id") == event_set.source_asset_id
    ]
    if len(matching) != 1:
        return _unavailable("SOURCE_IDENTITY_UNRESOLVED", "Event source is not uniquely present in the configured manifest.")
    asset = matching[0]["manifest"]
    expected_hash = str(asset.get("immutable_sha256") or "").lower()
    if expected_hash != event_set.source_sha256.lower():
        return _unavailable("SOURCE_HASH_MISMATCH", "Event and manifest source hashes differ.")
    source_path = Path(str(asset.get("immutable_path") or ""))
    if not source_path.is_file():
        return _unavailable("SOURCE_UNAVAILABLE", "Manifest-backed immutable source is missing.")
    if _sha256(source_path).lower() != expected_hash:
        return _unavailable("SOURCE_HASH_MISMATCH", "Immutable source bytes no longer match the manifest.")

    review_path = Path(review_overlay_path) if review_overlay_path else None
    if review_path and review_path.is_file():
        try:
            overlay = json.loads(review_path.read_text(encoding="utf-8"))
            if (
                overlay.get("schema_version") != "drum-human-review-v1"
                or overlay.get("source_asset_id") != event_set.source_asset_id
                or str(overlay.get("source_sha256", "")).lower() != event_set.source_sha256.lower()
                or not isinstance(overlay.get("corrections"), list)
            ):
                return _unavailable("REVIEW_SOURCE_MISMATCH", "Human review overlay does not belong to this immutable source.")
            for correction in overlay["corrections"]:
                event_set = apply_human_role_correction(
                    event_set,
                    event_id=str(correction["event_id"]),
                    role=correction["role"],
                    reviewer_id=str(correction["reviewer_id"]),
                    note=correction.get("note"),
                    recorded_at_utc=str(correction["recorded_at_utc"]),
                )
        except Exception as exc:
            return _unavailable("INVALID_REVIEW_OVERLAY", type(exc).__name__)

    events: list[dict[str, Any]] = []
    sample_matching = _sample_matching(event_set)
    candidates_by_event = sample_matching.get("event_candidates", {})
    for event in event_set.events:
        hypothesis = event.role_hypothesis
        human = event.human_correction
        features = hypothesis.features
        position = event.musical_position
        events.append({
            "event_id": event.event_id,
            "role": event.effective_role,
            "role_status": "HUMAN_VERIFIED" if human else hypothesis.status,
            "machine_role": hypothesis.role,
            "machine_role_status": hypothesis.status,
            "confidence": hypothesis.confidence,
            "confidence_basis": hypothesis.confidence_basis,
            "rule_id": hypothesis.rule_id,
            "bar": position.bar,
            "beat_in_bar": position.beat_in_bar,
            "subdivision": position.subdivision,
            "onset_qn": position.onset_qn,
            "onset_seconds": event.onset_seconds,
            "micro_offset_ms": position.micro_offset_ms,
            "microtiming_status": "UNCALIBRATED_PROVISIONAL",
            "accent_rms_dbfs": event.accent_rms_dbfs,
            "accent_measurement": event.accent_measurement,
            "source_asset_id": event.source_asset_id,
            "source_sha256": event.source_sha256,
            "source_region_seconds": {
                "start": event.source_slice.start_seconds,
                "end": event.source_slice.end_seconds,
            },
            "evidence_refs": hypothesis.evidence_refs,
            "limitations": hypothesis.limitations,
            "features": features.model_dump(mode="json") if features else None,
            "human_correction": human.model_dump(mode="json") if human else None,
            "selected_sample_asset_id": event.selected_sample_asset_id,
            "sample_candidates": candidates_by_event.get(event.event_id, []),
            "ableton_status": "VERIFIED" if event.ableton_realization_ref else "NOT_REALIZED",
        })

    return {
        "status": "READY",
        "reason": None,
        "musical_writes": 0,
        "source": {
            "asset_id": event_set.source_asset_id,
            "sha256": event_set.source_sha256,
            "role": event_set.source_role,
            "rights_status": manifest.get("rights_status", "UNKNOWN"),
            "remote_upload_allowed": bool(manifest.get("remote_upload_allowed", False)),
            "source_immutable": bool(asset.get("immutable_source", False)),
        },
        "analysis": {
            "schema_version": event_set.schema_version,
            "detector_id": event_set.detector_id,
            "role_classifier_version": event_set.role_classifier_version,
            "detector_calibration_id": event_set.detector_calibration_id,
            "onset_latency_calibrated": event_set.onset_latency_calibrated,
            "region_seconds": {"start": event_set.region_start_seconds, "end": event_set.region_end_seconds},
            "limitations": event_set.limitations,
        },
        "grid": {
            "tempo_bpm": event_set.tempo_bpm,
            "tempo_status": event_set.tempo_status,
            "tempo_decisions": [decision.model_dump(mode="json") for decision in event_set.tempo_decisions],
            "tempo_source": event_set.tempo_source,
            "filename_hint_bpm": event_set.tempo_label_hint_bpm,
            "alternate_tempos_bpm": event_set.alternate_tempos_bpm,
            "meter_numerator": event_set.meter_numerator,
            "meter_denominator": event_set.meter_denominator,
            "meter_status": event_set.meter_status,
            "bars": max((event.musical_position.bar for event in event_set.events), default=0),
        },
        "sample_matching": sample_matching,
        "reconstruction": build_drum_reconstruction(event_set).model_dump(mode="json"),
        "events": events,
    }


def read_drum_event_preview(
    event_id: str,
    *,
    artifact_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> tuple[bytes, int]:
    """Return a short source-context WAV for one verified event ID only."""
    view = load_drum_workbench(artifact_path=artifact_path, manifest_path=manifest_path)
    if view.get("status") != "READY":
        raise ValueError(str(view.get("status") or "DRUM_SOURCE_NOT_READY"))
    configured_artifact = artifact_path or os.environ.get("COPILOT_STUDIO_DRUM_EVENT_SET")
    if not configured_artifact:
        raise ValueError("DRUM_EVENT_ARTIFACT_NOT_CONFIGURED")
    event_file = Path(configured_artifact).expanduser()
    event_set = DrumEventSetV1.model_validate_json(event_file.read_text(encoding="utf-8"))
    event = next((item for item in event_set.events if item.event_id == event_id), None)
    if event is None:
        raise KeyError(event_id)
    start = event.source_slice.start_seconds
    end = event.source_slice.end_seconds
    if end - start > 1.0 or start < event_set.region_start_seconds or end > event_set.region_end_seconds:
        raise ValueError("DRUM_PREVIEW_WINDOW_OUT_OF_BOUNDS")
    source_path = Path(event_set.source_path).expanduser()
    if not source_path.is_file() or _sha256(source_path).lower() != event_set.source_sha256.lower():
        raise ValueError("DRUM_PREVIEW_SOURCE_HASH_MISMATCH")

    import soundfile as sf

    with sf.SoundFile(str(source_path), mode="r") as source:
        sample_rate = int(source.samplerate)
        first = max(0, int(start * sample_rate))
        frame_count = max(1, int(end * sample_rate) - first)
        source.seek(first)
        audio = source.read(frame_count, dtype="float32", always_2d=True)
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue(), sample_rate


def read_drum_sample_preview(asset_id: str) -> tuple[bytes, int]:
    """Audition a listed shortlist asset, resolved only through the trusted index."""
    workbench = load_drum_workbench()
    if workbench.get("status") != "READY":
        raise ValueError("DRUM_WORKBENCH_SOURCE_NOT_READY")
    allowed_ids = {
        candidate.get("asset_id")
        for shortlist in workbench.get("sample_matching", {}).get("role_shortlists", {}).values()
        for candidate in shortlist.get("candidates", [])
    }
    if asset_id not in allowed_ids:
        raise KeyError(asset_id)
    config = load_sample_library_config()
    index_file = sample_index_path()
    index = load_index(index_file)
    if index is None:
        raise ValueError("SAMPLE_LIBRARY_INDEX_UNAVAILABLE")
    configured_roots = {os.path.normcase(str(Path(root).resolve(strict=False))) for root in config.roots}
    indexed_roots = {os.path.normcase(str(Path(root).resolve(strict=False))) for root in index.roots}
    if not configured_roots or configured_roots != indexed_roots:
        raise ValueError("SAMPLE_INDEX_ROOT_MISMATCH")
    asset = next((item for item in index.assets.values() if item.id == asset_id), None)
    if asset is None or asset.status != AssetStatus.INDEXED:
        raise KeyError(asset_id)
    path = Path(asset.path).resolve(strict=False)
    allowed_roots = [Path(root).resolve(strict=False) for root in config.roots]
    if not any(_is_relative_to(path, root) for root in allowed_roots):
        raise ValueError("SAMPLE_ASSET_OUTSIDE_CONFIGURED_ROOTS")
    if not path.is_file() or _sha256(path).lower() != asset.sha256.lower():
        raise ValueError("SAMPLE_ASSET_HASH_MISMATCH")

    import soundfile as sf

    with sf.SoundFile(str(path), mode="r") as source:
        sample_rate = int(source.samplerate)
        frame_count = min(len(source), max(1, sample_rate))
        audio = source.read(frame_count, dtype="float32", always_2d=True)
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue(), sample_rate


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
