from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.music_source import (
    CandidateDisposition,
    SeparatorCheckpointManifest,
    SeparatorManifest,
    SeparatorRuntimeManifest,
    StemCandidate,
    WeightsLicenseStatus,
    build_blind_bundle,
    build_stem_candidate_set,
    validate_stem_file,
)


def _audio(path: Path, *, duration: float = 2.0, rate: int = 8000) -> None:
    t = np.arange(int(duration * rate), dtype=np.float32) / rate
    sf.write(path, (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), rate)


def _manifest(model_id: str = "model") -> SeparatorManifest:
    return SeparatorManifest(
        provider_id="provider",
        model_id=model_id,
        runtime=SeparatorRuntimeManifest(provider_id="provider", code_license="MIT"),
        checkpoint=SeparatorCheckpointManifest(
            model_id=model_id,
            weights_license_status=WeightsLicenseStatus.NOASSERTION,
        ),
        output_roles=["drums", "bass", "other", "vocals"],
    )


def test_license_facts_are_not_collapsed():
    manifest = _manifest()
    assert manifest.runtime.code_license == "MIT"
    assert manifest.checkpoint.weights_license_status is WeightsLicenseStatus.NOASSERTION


def test_candidate_is_technically_valid_but_not_human_approved(tmp_path: Path):
    source = tmp_path / "source.wav"
    _audio(source)
    info = sf.info(source)
    validation = validate_stem_file(source, role="drums", source_info=info)
    candidate = StemCandidate(
        candidate_id="candidate-1-drums",
        run_id="run-1",
        provider_id="provider",
        model_id="model",
        role="drums",
        path=source,
        sha256=validation.sha256 or "",
        bytes=source.stat().st_size,
        validation=validation,
    )
    assert candidate.disposition is CandidateDisposition.PERCEPTUALLY_UNREVIEWED
    result = build_stem_candidate_set(benchmark_id="bench", source_asset_id="source", candidates=[candidate])
    assert result.status == "TECHNICALLY_VALID"
    assert result.human_evaluation_required is True


def test_blind_bundle_has_private_mapping_and_anonymous_names(tmp_path: Path):
    paths = {}
    for private in ("provider_a_model_x", "provider_b_model_y"):
        role_path = tmp_path / f"{private}_drums.wav"
        _audio(role_path)
        paths[private] = {"drums": role_path}
    bundle = tmp_path / "bench" / "blind"
    mapping_path = tmp_path / "bench" / "private_mapping.json"
    mapping = build_blind_bundle(
        candidate_paths_by_private_id=paths,
        output_dir=bundle,
        mapping_path=mapping_path,
        source_info=sf.info(next(iter(paths.values()))["drums"]),
    )
    public_files = [path.name for path in (bundle / "drums").glob("*.wav")]
    assert public_files
    assert all("provider" not in name and "model" not in name for name in public_files)
    saved = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert saved["public_to_private"] == mapping.public_to_private
