from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from copilot.audio.musical_understanding_v1 import (
    _grid_point,
    _periodicity,
    analyze_musical_understanding,
)
from copilot.schemas.musical_understanding import MusicalUnderstanding


def _write_impulses(path: Path, *, sr: int = 8_000, seconds: float = 4.0) -> None:
    audio = np.zeros(int(sr * seconds), dtype=np.float32)
    for time_s in np.arange(0.1, seconds, 0.5):
        index = int(time_s * sr)
        audio[index : index + 12] = 0.8
    sf.write(path, audio, sr)


def test_grid_preserves_raw_timing_and_nearest_subdivision() -> None:
    point = _grid_point(0.51, 120.0, evidence="fixture")
    assert point.onset_qn == 1.02
    assert point.nearest_grid_qn == 1.0
    assert point.subdivision == "quarter"
    assert point.deviation_ms == pytest.approx(10.0)


def test_periodicity_is_ranked_from_event_positions() -> None:
    candidates = _periodicity([0, 4, 8, 12, 16, 20, 24, 28], evidence="fixture")
    assert candidates
    assert candidates[0].period_bars in {1, 2, 4}
    assert 0.0 <= candidates[0].strength <= 1.0


def test_real_contract_is_read_only_and_keeps_insufficient_tonality(tmp_path: Path, monkeypatch) -> None:
    bass = tmp_path / "bass.wav"
    drums = tmp_path / "drums.wav"
    _write_impulses(bass)
    _write_impulses(drums)
    import hashlib
    import json

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    artifact = lambda role, path: {
        "role": role,
        "source_reference_id": "fixture-reference",
        "provider": "fixture",
        "provider_model": "fixture",
        "path": str(path),
        "artifact_id": f"fixture-{role}",
        "sha256": digest(path),
        "bytes": path.stat().st_size,
        "duration_s": 4.0,
        "sample_rate": 8000,
        "channels": 1,
        "non_silent": True,
        "provenance": ["TEST"],
    }
    payload = {
        "reference_id": "fixture-reference",
        "source_analysis_id": "fixture-analysis",
        "tempo_bpm": 120,
        "timeline": {"windows_reused": [{"start_qn": 0, "end_qn": 8}]},
        "stems": {
            role: {"role": role, "artifact": artifact(role, path), "observations": []}
            for role, path in (("BASS", bass), ("DRUMS", drums))
        },
        "global_limitations": [],
        "provenance": {},
        "no_write": True,
        "raw_audio_included": False,
    }
    for role in ("VOCALS", "OTHER"):
        payload["stems"][role] = {"role": role, "artifact": None, "observations": []}
    reference = tmp_path / "stem-analysis.json"
    reference.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "copilot.audio.musical_understanding_v1._pitch_events",
        lambda *args, **kwargs: ([], ["TEST_NO_PITCH"], {}),
    )
    result = analyze_musical_understanding(reference)
    assert isinstance(result, MusicalUnderstanding)
    assert result.bass.tonality_status == "INSUFFICIENT_EVIDENCE"
    assert result.provenance["model_api_calls"] == 0
    assert result.provenance["musical_writes"] == 0
    assert result.no_write is True
