import json

import pytest

from copilot.integration.reference_variation_v1 import (
    ReferenceVariationError,
    build_reference_bound_bass_notes,
    load_astra_interpretation,
    validate_midi_reference,
)
from copilot.schemas.music_analysis import (
    GrooveEvidence,
    MusicAnalysisPack,
    MusicAnalysisWindow,
    ReferenceStateTokens,
    TimbreEvidence,
)
from copilot.schemas.musical_understanding import MusicalUnderstanding


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


def _understanding(project_id: str = "project-real") -> MusicalUnderstanding:
    onsets = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    events = [
        {
            "event_id": f"midi:{index}",
            "grid": {"onset_s": onset * 60 / 167, "onset_qn": onset, "bar": onset // 4 + 1,
                     "beat_in_bar": onset % 4 + 1, "subdivision": "quarter", "nearest_grid_qn": onset,
                     "deviation_qn": 0, "deviation_ms": 0},
            "offset_s": (onset + 0.5) * 60 / 167,
            "midi_note": [33, 37, 40, 42, 33, 37, 40, 42][index],
            "onset_qn": onset, "offset_qn": onset + 0.5, "duration_qn": 0.5,
            "confidence": 1.0, "source_kind": "ABLETON_MIDI", "status": "RELIABLE",
            "evidence_refs": [f"midi:{index}"],
        }
        for index, onset in enumerate(onsets)
    ]
    return MusicalUnderstanding.model_validate({
        "reference_id": "real-reference", "source_analysis_id": "reference_pack",
        "stem_analysis_id": "stem-analysis", "tempo_bpm": 167, "timeline": {},
        "bass": {"status": "SUPPORTED", "source_kind": "ABLETON_MIDI", "pitch_events": events,
                 "source_diagnostics": {"expected_project_identity": project_id,
                                        "actual_project_identity": project_id,
                                        "reconciliation": {"ok": True}},
                 "rhythmic_structure": {"event_count": len(events), "density_per_bar": 1}},
        "drums": {"pulse_structure": {}, "rhythmic_structure": {"event_count": 0, "density_per_bar": 0}},
        "relationships": {}, "provenance": {"midi_pack_path": "reference_pack.json"},
    })


def test_reference_bound_notes_use_measured_events_and_transform_them() -> None:
    notes, features = build_reference_bound_bass_notes(
        _pack(), _understanding(), start_qn=160.0, length_beats=32.0,
    )
    assert notes
    assert features["source_not_copied"] is True
    assert features["generated_event_count"] == len(notes)
    assert features["shifted_onsets"] > 0
    assert features["symbolic_validation"]["status"] == "VERIFIED"
    assert features["symbolic_validation"]["direct_note_copy"] is False
    assert features["symbolic_validation"]["rhythm_transformed"] is True
    assert len(features["event_traceability"]) == len(notes)
    assert any(row["changed"] for row in features["event_traceability"])
    assert not all(row["direct_copy"] for row in features["event_traceability"])
    assert {note.pitch for note in notes} == {33, 37, 40, 42}
    assert notes[0].start_time == 0.0
    assert notes[4].start_time == 4.0
    assert notes[1].start_time == 1.25
    assert all(0 <= note.start_time < 32 for note in notes)


def test_reference_bound_notes_fail_closed_without_events() -> None:
    understanding = _understanding()
    understanding.bass.pitch_events = []
    with pytest.raises(ReferenceVariationError, match="AUTHORITATIVE_BASS_EVENTS_MISSING"):
        build_reference_bound_bass_notes(_pack(), understanding, start_qn=160.0, length_beats=32.0)


def test_five_variation_strategies_are_deterministic_and_distinct() -> None:
    signatures = set()
    for variation_index in range(1, 6):
        notes, features = build_reference_bound_bass_notes(
            _pack(), _understanding(), start_qn=160.0, length_beats=32.0,
            variation_index=variation_index,
        )
        assert features["symbolic_validation"]["status"] == "VERIFIED"
        assert features["variation_index"] == variation_index
        assert features["variation_strategy"]
        assert all(note.pitch in {33, 37, 40, 42} for note in notes)
        signatures.add(tuple((note.start_time, note.duration, note.pitch) for note in notes))
    assert len(signatures) == 5


def test_reference_binding_rejects_other_project_or_analysis(tmp_path) -> None:
    path = tmp_path / "reference_pack.json"
    path.write_text(_pack().model_dump_json(), encoding="utf-8")
    understanding_path = tmp_path / "understanding.json"
    understanding = _understanding()
    understanding.provenance["midi_pack_path"] = str(path)
    understanding_path.write_text(understanding.model_dump_json(), encoding="utf-8")
    validate_midi_reference(_pack(), understanding, pack_path=path,
                            understanding_path=understanding_path, project_identity="project-real")
    with pytest.raises(ReferenceVariationError, match="REFERENCE_PROJECT_MISMATCH"):
        validate_midi_reference(_pack(), understanding, pack_path=path,
                                understanding_path=understanding_path, project_identity="other-project")


def test_astra_interpretation_requires_real_persisted_result(tmp_path) -> None:
    path = tmp_path / "astra.json"
    path.write_text(json.dumps({"status": "SIMULATED", "raw_response": "{}"}), encoding="utf-8")
    with pytest.raises(ReferenceVariationError, match="NOT_REAL"):
        load_astra_interpretation(path)

