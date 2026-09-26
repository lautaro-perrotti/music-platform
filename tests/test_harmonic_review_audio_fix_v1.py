from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from copilot.audio.harmonic_review_audio_fix_v1 import repair_harmonic_review_audio
from copilot.schemas.harmonic_human_review import (
    BassRelationshipSummary,
    HarmonicHumanReview,
    HarmonicHumanReviewWindow,
    ReviewRegion,
)


def test_harmonic_review_audio_fix_creates_audible_context_and_verifies_paths(tmp_path: Path) -> None:
    region = ReviewRegion(start_qn=0.0, end_qn=4.0, start_bar=1.0, end_bar=2.0, start_seconds=0.0, end_seconds=2.0)
    review = HarmonicHumanReview(
        analysis_artifact_id="fixture",
        reference_id="fixture-reference",
        source_hash="fixture-source",
        review_windows=[HarmonicHumanReviewWindow(window_id="harmonic_window_01", analysis_region=region, listening_region=region, bass_harmony=BassRelationshipSummary(event_count=0))],
        global_tonality_status="INSUFFICIENT_EVIDENCE",
    )
    review_path = tmp_path / "review.json"
    review_path.write_text(review.model_dump_json(), encoding="utf-8")
    source = tmp_path / "source.wav"
    sf.write(source, (np.sin(np.linspace(0, 40 * np.pi, 16_000)) * 0.01).astype(np.float32), 8_000)
    output = tmp_path / "fixed"

    repaired = repair_harmonic_review_audio(review_path, output_dir=output, reference_audio_path=source)

    context = repaired.review_windows[0].audio_artifacts["context"]
    assert repaired.audio_usability_status == "VERIFIED"
    assert repaired.timeline_mapping_status == "VERIFIED_SOURCE_LOCAL_ORIGIN"
    assert context.available is True
    assert context.has_signal is True
    assert context.audible_level is True
    assert context.channel_balanced is True
    assert context.channel_balance_db == pytest.approx(0.0, abs=0.01)
    assert context.channel_mode in {"MONO_DUPLICATED_FOR_REVIEW", "CENTERED_MONO_DUPLICATED_FOR_REVIEW"}
    assert context.peak_dbfs == pytest.approx(-3.0, abs=0.01)
    audit = (output / "harmonic_review_audio_audit_v1.json").read_text(encoding="utf-8")
    assert '"files_valid": true' in audit
    assert '"browser_paths_valid": true' in audit
    html = (output / "harmonic_sanity_check_v1.html").read_text(encoding="utf-8")
    assert "Exportar evaluación" in html
    assert "localStorage" in html
    assert "UNKNOWN_SHOULD_RESOLVE" in html
    assert "¿Por qué se eligió este acorde?" in html
    assert "Contexto completo" in html
    assert "Correcto / razonable" in html
    assert 'data-verdict="ACCEPT"' in html
    assert "human_verdict: item.verdict || 'PENDING'" in html
    assert "lang='es'" in html
    assert "PENDING" in html
    assert (output / "harmonic_sanity_check_v1.html").is_file()
