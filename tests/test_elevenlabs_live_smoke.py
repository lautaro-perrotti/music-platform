"""Paid smoke test; skipped unless the caller explicitly opts in."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from copilot.music_generation.benchmark import validate_generated_audio
from copilot.music_generation.elevenlabs import ElevenLabsMusicProvider
from copilot.music_generation.schemas import GenerationBrief, GeneratorRequest


@pytest.mark.skipif(
    os.environ.get("ELEVENLABS_LIVE_SMOKE") != "1" or not os.environ.get("ELEVENLABS_API_KEY"),
    reason="paid ElevenLabs smoke requires ELEVENLABS_API_KEY and ELEVENLABS_LIVE_SMOKE=1",
)
def test_elevenlabs_one_candidate_live_smoke() -> None:
    provider = ElevenLabsMusicProvider()
    request_id = "live-smoke-" + uuid.uuid4().hex[:12]
    output_dir = Path("runtime") / "elevenlabs-live-smoke" / request_id
    brief = GenerationBrief(
        brief_id=request_id,
        user_intent="Original short instrumental electronic house groove, four-on-the-floor, warm bass, no vocals.",
        target_duration_s=3,
        tempo_bpm=124,
        instrumental=True,
        candidate_count=1,
    )
    batch = provider.generate(GeneratorRequest(
        request_id=request_id,
        brief=brief,
        seed=1,
        output_dir=output_dir,
        no_ableton_access=True,
    ))
    assert batch.status == "GENERATED", batch.failures
    asset = batch.assets[0]
    validation = validate_generated_audio(asset, expected_duration_s=3)
    assert validation.status == "VALID", validation.model_dump()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "live-smoke-provenance.json").write_text(json.dumps({
        "request_id": request_id,
        "brief": brief.model_dump(mode="json"),
        "provider_request": asset.provider_request,
        "provider_metadata": asset.provider_metadata,
        "asset_sha256": asset.sha256,
        "technical_validation": validation.model_dump(mode="json"),
        "musical_quality": "NOT_EVALUATED",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
