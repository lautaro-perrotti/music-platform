from __future__ import annotations

from copilot.integration.multi_element_variation_v1 import build_multi_element_variation_bundle
from copilot.schemas.harmonic_understanding import ChordHypothesis, HarmonicUnderstanding, HarmonicWindow
from copilot.schemas.music_analysis import MusicAnalysisPack, MusicAnalysisWindow, ReferenceStateTokens
from copilot.schemas.musical_understanding import MusicalUnderstanding


def _grid(onset_qn: float) -> dict:
    return {
        "onset_s": onset_qn / 2,
        "onset_qn": onset_qn,
        "bar": onset_qn // 4,
        "beat_in_bar": onset_qn % 4,
        "subdivision": "quarter",
        "nearest_grid_qn": onset_qn,
        "deviation_qn": 0,
        "deviation_ms": 0,
    }


def _inputs() -> tuple[MusicAnalysisPack, MusicalUnderstanding, HarmonicUnderstanding]:
    project_id = "project-real"
    pack = MusicAnalysisPack(
        tokens=ReferenceStateTokens(reference_state_token="reference:real", target_state_token="target:live"),
        reference_id="reference-real",
        project_id=project_id,
        tempo_bpm=120,
        timeline={"start_qn": 0.0, "end_qn": 32.0},
        windows=[MusicAnalysisWindow(index=0, start_beat=0, end_beat=32, section_label="GROOVE")],
    )
    bass = [
        {
            "event_id": f"bass:{index}",
            "grid": _grid(float(index * 2)),
            "offset_s": (index * 2 + 0.5) / 2,
            "onset_qn": float(index * 2),
            "offset_qn": float(index * 2 + 0.5),
            "duration_qn": 0.5,
            "midi_note": [36, 39, 43, 36][index],
            "confidence": 1.0,
            "source_kind": "ABLETON_MIDI",
            "status": "RELIABLE",
            "evidence_refs": [f"ev:bass:{index}"],
        }
        for index in range(4)
    ]
    drums = [
        {"event_id": f"drum:{index}", "grid": _grid(float(index)), "strength": 0.8, "evidence_refs": [f"ev:drum:{index}"]}
        for index in range(8)
    ]
    understanding = MusicalUnderstanding.model_validate({
        "reference_id": "reference-real",
        "source_analysis_id": "analysis-real",
        "stem_analysis_id": "stems-real",
        "tempo_bpm": 120,
        "timeline": {},
        "bass": {
            "status": "SUPPORTED",
            "source_kind": "ABLETON_MIDI",
            "pitch_events": bass,
            "source_diagnostics": {"reconciliation": {"ok": True}},
            "rhythmic_structure": {"event_count": 4, "density_per_bar": 0.5},
        },
        "drums": {
            "status": "SUPPORTED",
            "transient_grid": drums,
            "pulse_structure": {},
            "rhythmic_structure": {"event_count": 8, "density_per_bar": 1.0},
        },
        "relationships": {},
        "provenance": {"evidence_refs": ["ev:understanding"]},
    })
    chord = ChordHypothesis(
        root="C", quality="minor", label="Cm", pitch_classes=["C", "Eb", "G"],
        score=0.9, confidence=0.9, evidence_refs=["ev:chord"],
    )
    harmonic = HarmonicUnderstanding(
        reference_id="reference-real", source_analysis_id="harmonic-real", tempo_bpm=120,
        source_kind="AUTHORITATIVE_REVIEWED_EVIDENCE",
        windows=[HarmonicWindow(
            window_id="h1", start_bar=0, end_bar=2, start_qn=0, end_qn=8,
            selected=chord, hypotheses=[chord], status="SUPPORTED", evidence_refs=["ev:harmony"],
        )],
        provenance={"evidence_refs": ["ev:harmony-pack"]},
    )
    return pack, understanding, harmonic


def test_multi_element_bundle_is_deterministic_and_evidence_bound() -> None:
    pack, understanding, harmonic = _inputs()
    first = build_multi_element_variation_bundle(
        pack, understanding, start_qn=0, length_beats=8,
        variation_index=1, variation_count=3, harmonic_understanding=harmonic,
    )
    second = build_multi_element_variation_bundle(
        pack, understanding, start_qn=0, length_beats=8,
        variation_index=1, variation_count=3, harmonic_understanding=harmonic,
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.status == "READY"
    assert {"BASS", "DRUMS", "HARMONIC"} == set(first.elements)
    assert first.elements["BASS"].notes
    assert first.elements["DRUMS"].notes
    assert first.elements["HARMONIC"].notes
    assert first.elements["DRUMS"].pitch_semantics == "GENERIC_TRANSIENT_ONLY"
    assert first.no_write is True


def test_multi_element_bundle_abstains_from_missing_harmony_instead_of_inventing_it() -> None:
    pack, understanding, _ = _inputs()
    bundle = build_multi_element_variation_bundle(
        pack, understanding, start_qn=0, length_beats=8,
        variation_index=1, variation_count=1,
    )
    assert bundle.status == "PARTIAL"
    assert bundle.elements["HARMONIC"].status == "INSUFFICIENT_EVIDENCE"
    assert bundle.elements["HARMONIC"].notes == []
    assert "HARMONIC_ARTIFACT_NOT_ATTACHED" in bundle.limitations
