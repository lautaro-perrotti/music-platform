from copilot.audio.music_analyzer_v1 import build_music_analysis_pack
from copilot.music_model.canonical_view import build_canonical_music_model_view
from copilot.reasoning.producer_planner_v1 import build_astra_producer_planner_context
from copilot.schemas.canonical_music_model import CanonicalMusicModelView
from copilot.schemas.reference_analysis import ReferenceAnalysisPack, ReferenceStateTokens


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
