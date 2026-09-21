import pytest

from copilot.audio.music_analyzer_v1 import build_music_analysis_pack
from copilot.reasoning.producer_planner_v1 import (
    ASTRA_DIAGNOSIS,
    ASTRA_PRODUCER_PLANNER,
    build_astra_diagnosis_context,
    build_astra_producer_planner_context,
    build_astra_producer_planner_prompt,
)
from copilot.schemas.reference_analysis import ReferenceAnalysisPack, ReferenceStateTokens


def _reference() -> ReferenceAnalysisPack:
    return ReferenceAnalysisPack(
        tokens=ReferenceStateTokens(reference_state_token="ref", target_state_token="target"),
        window_beats=128,
        windows=[
            {"start_beat": 0, "end_beat": 128, "section_label": "INTRO", "low_band_energy": 0.2},
            {"start_beat": 128, "end_beat": 256, "section_label": "DROP", "low_band_energy": 0.8},
        ],
    )


def test_music_analysis_composes_factual_families_and_stays_no_write():
    pack = build_music_analysis_pack(
        _reference(), tempo_bpm=128,
        lowend_windows=[{"kick_energy": 0.3, "bass_energy": 0.2}, {"kick_energy": 0.8, "bass_energy": 0.7}],
        groove_windows=[{"onset_count": 8, "event_locations": [8.0]}],
        harmony_windows=[{"key_candidate": "F minor", "key_confidence": 0.8}],
        sections=[{"name": "INTRO", "start_beat": 0, "end_beat": 128}],
        source_activity=[{"source": "kick", "start_beat": 0, "end_beat": 256, "activity": "ACTIVE"}],
        transitions=[{"start_beat": 120, "end_beat": 128, "kind": "BUILD"}],
        analyzer_ids={"fullmix": "fullmix-obs-1", "lowend": "lowend-obs-1"},
    )
    assert pack.no_write is True
    assert pack.raw_audio_included is False
    assert pack.windows[1].kick_energy == 0.8
    assert pack.windows[0].groove.onset_count == 8
    assert pack.sections[0].name == "INTRO"
    assert pack.source_activity[0].source == "kick"


def test_astra_roles_are_separate_and_prompt_is_grounded():
    pack = build_music_analysis_pack(_reference(), tempo_bpm=128)
    diagnosis = build_astra_diagnosis_context(pack)
    planner = build_astra_producer_planner_context(
        pack, diagnosis={"status": "SUPPORTED", "claims": []}, sample_set_context={"NO_WRITE": True, "candidates": {}}
    )
    assert diagnosis["role"] == ASTRA_DIAGNOSIS
    assert planner["role"] == ASTRA_PRODUCER_PLANNER
    assert diagnosis["NO_WRITE"] is planner["NO_WRITE"] is True
    assert "invent measurements" in build_astra_producer_planner_prompt(planner)


def test_invalid_write_capability_is_rejected():
    pack = build_music_analysis_pack(_reference(), tempo_bpm=128)
    with pytest.raises(ValueError, match="NO_WRITE"):
        build_astra_producer_planner_context(pack, sample_set_context={"NO_WRITE": False})
