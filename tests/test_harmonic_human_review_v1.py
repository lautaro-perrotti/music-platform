from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.audio.harmonic_human_review_v1 import build_harmonic_human_review
from copilot.schemas.harmonic_human_review import HarmonicHumanReview
from copilot.schemas.harmonic_understanding import (
    BassHarmonyRelationship,
    ChordHypothesis,
    HarmonicUnderstanding,
    HarmonicWindow,
)
from copilot.schemas.musical_understanding import (
    BassDrumsRelationship,
    BassPitchEvent,
    BassUnderstanding,
    DrumsUnderstanding,
    MusicalGridPoint,
    MusicalUnderstanding,
    PulseStructure,
    RhythmicStructure,
)


def test_harmonic_human_review_builds_exact_pending_package(tmp_path: Path) -> None:
    event = BassPitchEvent(
        event_id="review-event-1",
        grid=MusicalGridPoint(
            onset_s=0.0,
            onset_qn=0.0,
            bar=1.0,
            beat_in_bar=1.0,
            subdivision="quarter",
            nearest_grid_qn=0.0,
            deviation_qn=0.0,
            deviation_ms=0.0,
        ),
        offset_s=0.5,
        midi_float=36.0,
        midi_note=36,
        pitch_class="C",
        confidence=1.0,
        status="RELIABLE",
        onset_qn=0.0,
        offset_qn=1.0,
        duration_qn=1.0,
        source_kind="ABLETON_MIDI",
    )
    understanding = MusicalUnderstanding(
        reference_id="review-reference",
        source_analysis_id="review-analysis",
        stem_analysis_id="review-stems",
        tempo_bpm=120.0,
        timeline={"windows_reused": [{"start_qn": 0.0, "end_qn": 8.0}]},
        bass=BassUnderstanding(
            source_kind="ABLETON_MIDI",
            pitch_events=[event],
            pitch_classes={"C": 1.0},
            rhythmic_structure=RhythmicStructure(event_count=1, density_per_bar=0.5),
        ),
        drums=DrumsUnderstanding(pulse_structure=PulseStructure(), rhythmic_structure=RhythmicStructure(event_count=0, density_per_bar=0.0)),
        relationships={"bass_drums": BassDrumsRelationship(bass_event_count=1, drum_event_count=0, coincidence_count=0, coincidence_ratio=0.0)},
        provenance={"model_api_calls": 0, "musical_writes": 0},
    )
    understanding_path = tmp_path / "understanding.json"
    understanding_path.write_text(understanding.model_dump_json(), encoding="utf-8")
    selected = ChordHypothesis(root="C", quality="major", label="C", pitch_classes=["C", "E", "G"], score=0.9, confidence=0.8)
    harmonic = HarmonicUnderstanding(
        reference_id="review-reference",
        source_analysis_id="review-analysis",
        tempo_bpm=120.0,
        source_kind="OTHER_STEM_CHROMA_PLUS_AUTHORITATIVE_BASS",
        windows=[HarmonicWindow(window_id="harmonic_window_01", start_bar=1.0, end_bar=3.0, start_qn=0.0, end_qn=8.0, pitch_class_energy={"C": 0.4, "E": 0.3, "G": 0.3}, hypotheses=[selected], selected=selected, status="SUPPORTED", evidence_refs=["review"])],
        bass_harmony_relationships=[BassHarmonyRelationship(event_id=event.event_id, bass_pitch_class="C", window_id="harmonic_window_01", chord_label="C", role="ROOT", confidence=0.8)],
        provenance={"model_api_calls": 0, "musical_writes": 0},
    )
    harmonic_path = tmp_path / "harmonic.json"
    harmonic_path.write_text(harmonic.model_dump_json(), encoding="utf-8")
    audio_path = tmp_path / "reference.wav"
    sf.write(audio_path, np.ones(8_000, dtype=np.float32) * 0.1, 8_000)
    output_dir = tmp_path / "review"

    review = build_harmonic_human_review(harmonic_path, understanding_path, output_dir=output_dir, reference_audio_path=audio_path)

    assert isinstance(review, HarmonicHumanReview)
    assert len(review.review_windows) == 1
    assert review.review_windows[0].human_verdict == "PENDING"
    assert review.review_windows[0].selected_hypothesis.label == "C"
    assert review.review_windows[0].bass_harmony.counts == {"ROOT": 1}
    assert review.model_api_calls == 0
    assert review.musical_writes == 0
    assert review.ableton_mutations == 0
    assert (output_dir / "window_01_master.wav").is_file()
    assert (output_dir / "window_01_master_context.wav").is_file()
    assert (output_dir / "harmonic_sanity_check_v1.json").is_file()
    assert (output_dir / "harmonic_sanity_check_v1.txt").is_file()
    assert (output_dir / "harmonic_sanity_check_v1.html").is_file()
