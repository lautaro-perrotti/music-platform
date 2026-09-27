from copilot.audio.music_analyzer_v1 import build_music_analysis_pack
from copilot.music_model import build_bass_variation_intent, build_canonical_music_model_view, build_canonical_music_model_view_v2
from copilot.reasoning.producer_planner_v1 import build_astra_producer_planner_context
from copilot.schemas.canonical_music_model import CanonicalMusicModelView
from copilot.schemas.reference_analysis import ReferenceAnalysisPack, ReferenceStateTokens
from copilot.schemas.musical_understanding import MusicalUnderstanding


def _pack():
    reference = ReferenceAnalysisPack(
        tokens=ReferenceStateTokens(reference_state_token="ref", target_state_token="target"),
        window_beats=128,
        windows=[
            {"start_beat": 0, "end_beat": 128, "section_label": "INTRO"},
            {"start_beat": 128, "end_beat": 256, "section_label": "DROP"},
        ],
    )
    return build_music_analysis_pack(
        reference,
        tempo_bpm=128,
        groove_windows=[{"onset_count": 8, "event_locations": [8.0]}],
        harmony_windows=[{"key_candidate": "F minor", "key_confidence": 0.4}],
        sections=[{"name": "INTRO", "start_beat": 0, "end_beat": 128}],
        transitions=[{"start_beat": 120, "end_beat": 128, "kind": "BUILD"}],
    )


def test_canonical_view_is_a_read_only_provenance_preserving_projection():
    pack = _pack()
    view = build_canonical_music_model_view(pack)

    assert view.no_write is True
    assert view.schema_version == "canonical-music-model-view-v1"
    assert view.timeline.tempo_bpm == 128
    assert view.timeline.project_qn_start == 0
    assert view.timeline.project_qn_end == 256
    assert view.structure.status == "SUPPORTED"
    assert view.rhythm.status == "SUPPORTED"
    assert view.bass.status == "NOT_AVAILABLE"
    assert view.harmony.status == "EVIDENCE_ONLY"
    assert set(view.sources) == {"MusicAnalysisPack"}


def test_producer_planner_consumes_canonical_music_model_view():
    pack = _pack()
    context = build_astra_producer_planner_context(pack)

    assert "music_model" in context
    assert "reference_analysis" not in context
    assert context["music_model"]["no_write"] is True
    assert context["music_model"]["identity"]["reference_state_token"] == "ref"
    assert context["music_model"]["harmony"]["status"] == "EVIDENCE_ONLY"


def test_producer_planner_rejects_a_view_bound_to_another_state():
    pack = _pack()
    mismatched = CanonicalMusicModelView(
        identity={"reference_state_token": "other-ref", "target_state_token": "other-target"},
        timeline={"tempo_bpm": 128},
    )

    try:
        build_astra_producer_planner_context(pack, music_model=mismatched)
    except ValueError as exc:
        assert str(exc) == "CANONICAL_MUSIC_MODEL_TOKEN_MISMATCH"
    else:
        raise AssertionError("expected canonical view token mismatch")


def test_canonical_view_v2_consolidates_groove_motifs_and_explicit_melody_limit():
    pack = _pack()
    understanding = MusicalUnderstanding.model_validate({
        "reference_id": "reference-test",
        "source_analysis_id": "analysis",
        "stem_analysis_id": "stems",
        "tempo_bpm": 128,
        "timeline": {"start_qn": 0, "end_qn": 32},
        "bass": {
            "status": "SUPPORTED",
            "source_kind": "ABLETON_MIDI",
            "pitch_events": [],
            "rhythmic_structure": {"event_count": 0, "density_per_bar": 0},
            "motifs": [{"phrase_id": "p1", "start_bar": 0, "end_bar": 2, "event_count": 0}],
        },
        "drums": {
            "status": "SUPPORTED",
            "transient_grid": [],
            "pulse_structure": {},
            "rhythmic_structure": {"event_count": 8, "density_per_bar": 2},
        },
        "relationships": {"bass_drums": {"bass_event_count": 0, "drum_event_count": 8, "coincidence_count": 0, "coincidence_ratio": 0}},
        "no_write": True,
    })
    view = build_canonical_music_model_view_v2(pack, musical_understanding=understanding)
    assert view.schema_version == "canonical-music-model-view-v2"
    assert view.groove.status == "SUPPORTED"
    assert view.motifs.status == "SUPPORTED"
    assert view.melody.status == "INSUFFICIENT_EVIDENCE"
    assert view.bass.status == "SUPPORTED"


def test_bass_variation_intent_is_read_only_and_evidence_bound():
    pack = _pack()
    understanding = MusicalUnderstanding.model_validate({
        "reference_id": "reference-test",
        "source_analysis_id": "analysis",
        "stem_analysis_id": "stems",
        "tempo_bpm": 128,
        "timeline": {},
        "bass": {"status": "SUPPORTED", "source_kind": "ABLETON_MIDI", "rhythmic_structure": {"event_count": 1, "density_per_bar": 1}},
        "drums": {"status": "SUPPORTED", "pulse_structure": {}, "rhythmic_structure": {"event_count": 1, "density_per_bar": 1}},
        "relationships": {},
    })
    view = build_canonical_music_model_view_v2(pack, musical_understanding=understanding)
    intent = build_bass_variation_intent(view, intent_id="intent_1", start_qn=0, length_bars=8, instruction="move the bass groove")
    assert intent.no_write is True
    assert intent.role == "BASS"
    assert "evidenced_pitch_material" in intent.preserve
    assert "produce_a_new_editable_sequence_not_an_exact_copy" in intent.transform
