from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.track_level_review_audio_v1 import _generic_capture_preflight, attach_track_review_audio
from copilot.schemas.harmonic_human_review import (
    BassRelationshipSummary,
    HarmonicHumanReview,
    HarmonicHumanReviewWindow,
    ReviewRegion,
)


def _review() -> HarmonicHumanReview:
    windows = []
    for index in range(2):
        start = float(index * 4)
        region = ReviewRegion(
            start_qn=start,
            end_qn=start + 4.0,
            start_bar=start / 4.0 + 1.0,
            end_bar=start / 4.0 + 2.0,
            start_seconds=start * 0.5,
            end_seconds=(start + 4.0) * 0.5,
        )
        windows.append(
            HarmonicHumanReviewWindow(
                window_id=f"harmonic_window_{index + 1:02d}",
                analysis_region=region,
                listening_region=region,
                bass_harmony=BassRelationshipSummary(event_count=0),
            )
        )
    return HarmonicHumanReview(
        analysis_artifact_id="fixture",
        reference_id="fixture-reference",
        source_hash="fixture-source",
        review_windows=windows,
        global_tonality_status="INSUFFICIENT_EVIDENCE",
        provenance={"tempo_bpm": 120.0},
    )


def test_attach_track_review_audio_slices_once_into_existing_windows(tmp_path: Path) -> None:
    review_path = tmp_path / "review.json"
    review_path.write_text(_review().model_dump_json(), encoding="utf-8")
    source = tmp_path / "track.wav"
    samples = np.sin(np.linspace(0.0, 64.0 * np.pi, 32_000)).astype(np.float32)
    sf.write(source, samples, 8_000)
    manifest_path = tmp_path / "track_level_review_audio_v1.json"
    manifest_path.write_text(
        json.dumps(
            {
                "region": {"start_qn": 0.0, "end_qn": 8.0},
                "captures": [
                    {
                        "track_ref": {"content_fingerprint": "authoritative-ref"},
                        "runtime_track_id": "runtime-1",
                        "display_name": "Rose Bass",
                        "track_kind": "midi",
                        "status": "HAS_SIGNAL",
                        "capture": {"artifact_path": str(source), "hash": "source"},
                        "capture_point": "POST_MIXER",
                        "capture_provenance": {"method": "capture_source_post_mixer"},
                        "limitations": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "review"
    result = attach_track_review_audio(review_path, output_dir=output, manifest_path=manifest_path)

    manifest = result.source_artifacts["track_review_audio_manifest"]
    assert manifest["captured_count"] == 1
    assert manifest["tracks"][0]["window_artifacts"].keys() == {
        "harmonic_window_01",
        "harmonic_window_02",
    }
    assert (output / "audio" / "window_01" / "tracks" / "track_01.wav").is_file()
    html = (output / "harmonic_sanity_check_v1.html").read_text(encoding="utf-8")
    assert "Pistas individuales del proyecto" in html
    assert "Rose Bass" in html
    assert html.count("<audio ") == 2
    audit = json.loads((output / "track_level_review_audio_audit_v1.json").read_text(encoding="utf-8"))
    assert audit["window_slice_count"] == 2
    assert audit["files_valid"] is True


def test_attach_track_review_audio_preserves_silence_as_typed_status(tmp_path: Path) -> None:
    review_path = tmp_path / "review.json"
    review_path.write_text(_review().model_dump_json(), encoding="utf-8")
    manifest_path = tmp_path / "track_level_review_audio_v1.json"
    manifest_path.write_text(
        json.dumps(
            {
                "region": {"start_qn": 0.0, "end_qn": 8.0},
                "captures": [
                    {
                        "track_ref": {"content_fingerprint": "authoritative-ref"},
                        "runtime_track_id": "runtime-2",
                        "display_name": "Vocal Main",
                        "track_kind": "audio",
                        "status": "EXPECTED_OR_OBSERVED_SILENCE",
                        "capture": {"artifact_path": None},
                        "capture_point": "POST_MIXER",
                        "capture_provenance": {},
                        "limitations": ["No signal in selected region"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = attach_track_review_audio(review_path, output_dir=tmp_path / "review", manifest_path=manifest_path)
    row = result.source_artifacts["track_review_audio_manifest"]["tracks"][0]
    assert row["status"] == "EXPECTED_OR_OBSERVED_SILENCE"
    assert row["window_artifacts"] == {}
    assert result.musical_writes == 0
    assert result.ableton_mutations == 0


def test_generic_preflight_waives_only_legacy_source_specific_checks() -> None:
    adapted = _generic_capture_preflight(
        {
            "pass": False,
            "status": "TARGET_SOURCE_UNSUPPORTED",
            "missing": [
                "TARGET_SOURCE_UNSUPPORTED: legacy kick source is not selected",
                "Copilot Capture Bass routing claim=FAILED expected OFF_MIX_GRAPH",
            ],
        }
    )
    assert adapted["pass"] is True
    assert adapted["status"] == "GENERIC_TRACK_CAPTURE_READY"
    assert adapted["waived_source_specific_checks"]

    blocked = _generic_capture_preflight(
        {
            "pass": False,
            "status": "PROJECT_MISMATCH",
            "missing": ["PROJECT_MISMATCH: wrong working copy"],
        }
    )
    assert blocked["pass"] is False
