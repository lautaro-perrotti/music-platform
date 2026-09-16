from __future__ import annotations

from copilot.audio.session_diagnose import (
    LAB_TRACK_MARKERS,
    REAL_BASS_NAMES,
    REAL_KICK_NAMES,
    classify_bass_source,
    classify_kick_source,
)
from copilot.audio.session_run1 import (
    REAL_REGIONS,
    _capture_source_mismatch,
    select_arrangement_regions,
)
from copilot.reasoning.from_dsp import pack_from_lowend_features


def test_lab_markers_and_real_names_are_disjoint() -> None:
    assert not set(LAB_TRACK_MARKERS) & set(REAL_KICK_NAMES)
    assert not set(LAB_TRACK_MARKERS) & set(REAL_BASS_NAMES)


def test_classify_kick_source_requires_pad_not_whole_drums() -> None:
    assert classify_kick_source("Drums", "Kick 808 Deep") == "KICK_PAD_ISOLATED"
    assert classify_kick_source("Drums", "Drum Rack | Kick 808 Deep | Post Mixer") == "KICK_PAD_ISOLATED"
    assert classify_kick_source("Kick 808 Deep", "Post Mixer") == "KICK_PAD_ISOLATED"
    assert classify_kick_source("Drums", "Post Mixer") == "DRUMS_WHOLE"
    assert classify_kick_source("Drums", "") == "DRUMS_WHOLE"
    assert classify_kick_source("", "") == "UNSET"
    assert classify_kick_source("Ext. In", "1/2") == "OTHER"


def test_classify_bass_source_rejects_bassline_chain() -> None:
    assert classify_bass_source("Sub Sub Bass", "Post Mixer") == "TRACK_POST_MIXER"
    assert classify_bass_source("Sub Sub Bass", "Bassline | Post Mixer") == "BASSLINE_CHAIN"
    assert classify_bass_source("Sub Sub Bass", "Pre FX") == "TRACK_NOT_POST_MIXER"


def test_real_regions_are_unlabeled_abc() -> None:
    ids = [row["id"] for row in REAL_REGIONS]
    assert ids == ["REGION_A", "REGION_B", "REGION_C"]
    assert REAL_REGIONS[0]["start_qn"] == 256.0
    assert REAL_REGIONS[1]["start_qn"] == 96.0
    assert REAL_REGIONS[2]["start_qn"] == 32.0
    for row in REAL_REGIONS:
        assert "CLEAN" not in row["id"]
        assert "TEMPORAL" not in row["id"]
        assert "SPECTRAL" not in row["id"]


def test_capture_source_mismatch_allows_inactive_sub() -> None:
    silent = {"class": "RECORDED_SILENCE"}
    signal = {"class": "HAS_SIGNAL"}
    signals = {"master": signal, "kick": signal, "bass": silent}
    assert (
        _capture_source_mismatch(signals, REAL_REGIONS[2]["expected"]) is None
    )
    assert _capture_source_mismatch(signals, REAL_REGIONS[0]["expected"])


def test_select_arrangement_regions_are_unlabeled_and_non_overlapping() -> None:
    rows = select_arrangement_regions(loop_start=0.0, loop_length=432.0)
    assert [row["id"] for row in rows] == ["REGION_1", "REGION_2", "REGION_3"]
    for row in rows:
        assert row["end_qn"] > row["start_qn"]
        assert row["end_qn"] <= 432.0
        assert "CLEAN" not in row["id"]
        assert "TEMPORAL" not in row["id"]
    starts = [row["start_qn"] for row in rows]
    assert starts == sorted(starts)
    assert rows[0]["end_qn"] <= rows[1]["start_qn"]
    assert rows[1]["end_qn"] <= rows[2]["start_qn"]


def test_pack_from_dsp_has_no_fixture_or_expected_diagnosis() -> None:
    features = {
        "ok": True,
        "attacks": {"raw_count": 6, "count": 4, "source": "TRACK_ISOLATED"},
        "temporal": {
            "kick_events": 4,
            "events_with_overlap": 2,
            "median_persist": 0.31,
            "median_on_attack": 0.28,
            "off_kick_mean": 0.22,
        },
        "spectral": {
            "events_with_co_concentration": 1,
            "simultaneous": [],
            "dominant_attack_band": "40-60",
            "band_mean_joint": {"40-60": 0.2, "60-80": 0.1},
        },
        "kick_rms": 0.18,
        "attack_context": None,
    }
    pack = pack_from_lowend_features(
        region_id="R1",
        region="0->8qn",
        features=features,
        views={},
        project_token="proj",
        audible_token="aud",
        target_token="tgt",
        kick_name="Kick 808 Deep",
        bass_name="Bassline",
    )
    dumped = pack.model_dump_json()
    assert "CLEAR_TEMPORAL" not in dumped
    assert "CLEAR_SPECTRAL" not in dumped
    assert "we think" not in dumped.lower()
    persist = next(item for item in pack.items if item.evidence_id == "ev.persist.energy")
    assert persist.unit is None
    assert any(item.code == "DSP_PERSIST_IS_ENERGY" for item in pack.limitations)
