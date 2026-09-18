"""Read-only validation on existing captured WAVs. Does not retune. No Live capture."""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot.audio.physical_dsp_v2.pipeline import analyze_pair, analyze_path
from copilot.schemas.dsp import AnalyzerFamily, DspObservation

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "logs" / "captures"
REGION_A = ROOT / "logs" / "live3r" / "REGION_A"


def _first(directory: Path, pattern: str) -> Path | None:
    if not directory.is_dir():
        return None
    hits = sorted(directory.glob(pattern))
    return hits[0] if hits else None


@pytest.fixture(scope="module")
def groove_kick() -> Path:
    path = _first(CAPTURES, "*Filter_Kick*.wav")
    if path is None:
        pytest.skip("Groove Rider captures not present under logs/captures")
    return path


@pytest.fixture(scope="module")
def groove_bass() -> Path:
    path = _first(CAPTURES, "*Filtered_Bassline*.wav")
    if path is None:
        pytest.skip("Groove Rider captures not present under logs/captures")
    return path


@pytest.fixture(scope="module")
def region_a_master() -> Path:
    path = _first(REGION_A, "master_capture_*.wav")
    if path is None:
        pytest.skip("live3r REGION_A master capture not present")
    return path


def test_groove_rider_read_only_structure(groove_kick: Path, tmp_path: Path) -> None:
    bundle = analyze_path(
        groove_kick,
        tempo_bpm=126.0,
        use_cache=True,
        cache_dir=tmp_path / "cache",
    )
    families = {obs.analyzer_id for obs in bundle.observations}
    for family in (
        AnalyzerFamily.LEVEL_DYNAMICS.value,
        AnalyzerFamily.SPECTRUM.value,
        AnalyzerFamily.TRANSIENTS.value,
        AnalyzerFamily.STEREO.value,
        AnalyzerFamily.RHYTHM.value,
        AnalyzerFamily.TONAL.value,
        AnalyzerFamily.TIMBRE.value,
    ):
        assert family in families
    for obs in bundle.observations:
        assert isinstance(obs, DspObservation)
        assert obs.source_artifact_hash
        assert obs.provenance.cache_key
    tonal = bundle.by_analyzer(AnalyzerFamily.TONAL.value)[0]
    assert tonal.get("selected_key").value is None
    assert len(tonal.get("key_candidates").value or []) >= 1


def test_groove_rider_pair_is_potential_overlap_not_masking(
    groove_kick: Path, groove_bass: Path, tmp_path: Path
) -> None:
    bundle = analyze_pair(groove_kick, groove_bass, use_cache=True, cache_dir=tmp_path / "cache")
    rel = bundle.by_analyzer(AnalyzerFamily.RELATIONAL.value)[0]
    assert rel.get("relationship").value in {"POTENTIAL_OVERLAP", "LOW_MEASURED_OVERLAP"}
    text = " ".join([rel.analyzer_id, *[lim.detail for lim in rel.limitations]]).lower()
    assert "muddy" not in text
    assert "masking" not in text


def test_additional_region_a_project(region_a_master: Path, tmp_path: Path) -> None:
    bundle = analyze_path(region_a_master, use_cache=True, cache_dir=tmp_path / "cache")
    assert bundle.observations
    level = bundle.by_analyzer(AnalyzerFamily.LEVEL_DYNAMICS.value)[0]
    assert level.get("sample_peak") is not None
    assert level.get("rms").value >= 0.0
