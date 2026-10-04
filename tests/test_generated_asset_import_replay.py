import hashlib
import json

import pytest

from copilot.music_generation.generated_asset_import import stage_generated_asset
from copilot.music_generation.schemas import (
    GeneratedAsset, ModelManifest, PerformanceManifest, RightsManifest,
)


def _setup(tmp_path):
    project = tmp_path / "controlled"
    project.mkdir()
    als = project / "Working.als"
    als.write_bytes(b"set")
    (project / "copilot_import.json").write_text(json.dumps({
        "working_als": str(als), "ORIGINAL_UNTOUCHED": True,
    }), encoding="utf-8")
    source = tmp_path / "real.wav"
    source.write_bytes(b"RIFFdemo-WAVE-real-audio-bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    asset = GeneratedAsset(
        asset_id="replay:one", path=source, sha256=digest,
        bytes=source.stat().st_size, non_silent=True,
        model=ModelManifest(provider="replay", model_id="stored"),
        seed=1, prompt="existing output",
        performance=PerformanceManifest(device="replay"),
        rights_manifest=RightsManifest(),
    )
    return als, asset


def test_stage_uses_browser_relative_samples_uri_and_reuses_identical_copy(tmp_path):
    als, asset = _setup(tmp_path)
    first = stage_generated_asset(asset, working_als=als)
    second = stage_generated_asset(asset, working_als=als)
    assert first.sample_uri == f"Samples/Imported/generated_{asset.sha256[:16]}.wav"
    assert second.staged_path == first.staged_path
    assert first.staged_path.read_bytes() == asset.path.read_bytes()


def test_stage_rejects_changed_source_and_existing_copy(tmp_path):
    als, asset = _setup(tmp_path)
    staged = stage_generated_asset(asset, working_als=als)
    staged.staged_path.write_bytes(b"different")
    with pytest.raises(ValueError, match="EXISTING_STAGE_HASH_MISMATCH"):
        stage_generated_asset(asset, working_als=als)
    asset.path.write_bytes(b"also different")
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH"):
        stage_generated_asset(asset, working_als=als)
