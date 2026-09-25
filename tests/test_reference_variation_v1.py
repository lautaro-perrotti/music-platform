import json

import pytest

from copilot.integration.reference_variation_v1 import (
    ReferenceVariationError,
    build_reference_bound_bass_notes,
    load_astra_interpretation,
)
from copilot.schemas.music_analysis import (
    GrooveEvidence,
    MusicAnalysisPack,
    MusicAnalysisWindow,
    ReferenceStateTokens,
    TimbreEvidence,
)


def _pack() -> MusicAnalysisPack:
    return MusicAnalysisPack(
        tokens=ReferenceStateTokens(reference_state_token="reference:real", target_state_token="target:live"),
        reference_id="real-reference",
        project_id="project-real",
        tempo_bpm=167,
        mode="SELECTED_REGION",
        timeline={"start_qn": 160.0, "end_qn": 288.0, "duration_s": 46.0},
        windows=[
            MusicAnalysisWindow(
                index=0,
                start_beat=160.0,
                end_beat=288.0,
                energy_db=-18.0,
                low_band_energy=0.8,
                groove=GrooveEvidence(
                    onset_count=8,
                    onset_density_per_s=2.0,
                    repetition_strength=0.7,
                    variation_score=0.3,
                    event_locations=[160.0, 162.0, 164.0, 168.0, 176.0, 180.0, 184.0, 188.0],
                ),
                timbre=TimbreEvidence(spectral_centroid_hz=240.0),
                evidence_refs=["music_analyzer.onsets", "fullmix.spectral_trajectory"],
            )
        ],
        evidence_refs=["music_analyzer.onsets"],
        provenance={"audio_sha256": "abc"},
    )


def test_reference_bound_notes_use_measured_events_and_transform_them() -> None:
    notes, features = build_reference_bound_bass_notes(
        _pack(), length_beats=32.0, transformation_seed="variation_1234",
    )
    assert notes
    assert features["source_not_copied"] is True
    assert features["generated_event_count"] == len(notes)
    assert features["register_base_pitch"] == 36
    assert features["evidence_refs"] == ["music_analyzer.onsets", "fullmix.spectral_trajectory"]
    assert all(0 <= note.start_time < 32 for note in notes)


def test_reference_bound_notes_fail_closed_without_events() -> None:
    pack = _pack()
    pack.windows[0].groove.event_locations = []
    with pytest.raises(ReferenceVariationError, match="no measured events"):
        build_reference_bound_bass_notes(pack, length_beats=32.0, transformation_seed="x")


def test_astra_interpretation_requires_real_persisted_result(tmp_path) -> None:
    path = tmp_path / "astra.json"
    path.write_text(json.dumps({"status": "SIMULATED", "raw_response": "{}"}), encoding="utf-8")
    with pytest.raises(ReferenceVariationError, match="NOT_REAL"):
        load_astra_interpretation(path)

