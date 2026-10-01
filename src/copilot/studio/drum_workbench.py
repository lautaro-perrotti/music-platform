"""Read-only Studio projection of an explicitly configured drum event artifact."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from copilot.audio.drum_events_v1 import DrumEventSetV1


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
    }


def load_drum_workbench(
    *,
    artifact_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
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

    events: list[dict[str, Any]] = []
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
            "tempo_source": event_set.tempo_source,
            "filename_hint_bpm": event_set.tempo_label_hint_bpm,
            "alternate_tempos_bpm": event_set.alternate_tempos_bpm,
            "meter_numerator": event_set.meter_numerator,
            "meter_denominator": event_set.meter_denominator,
            "meter_status": event_set.meter_status,
            "bars": max((event.musical_position.bar for event in event_set.events), default=0),
        },
        "events": events,
    }
