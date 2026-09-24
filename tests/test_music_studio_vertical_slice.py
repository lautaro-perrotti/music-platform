from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import soundfile as sf

from copilot.music_generation.schemas import (
    GeneratedAsset,
    GenerationBatch,
    GeneratorCapability,
    GeneratorDescriptor,
    GeneratorHealth,
    GeneratorRequest,
    ModelManifest,
    PerformanceManifest,
    RightsClassification,
    RightsManifest,
)
from copilot.studio.service import StudioService


class FakeStudioProvider:
    provider_id = "test-provider"

    def describe(self):
        return GeneratorDescriptor(
            provider_id=self.provider_id,
            model=ModelManifest(provider=self.provider_id, model_id="test-real-boundary"),
            capabilities=[GeneratorCapability.TEXT_TO_MUSIC],
            health=GeneratorHealth.HEALTHY,
            local_or_remote="TEST_ONLY",
            rights_classification=RightsClassification.UNKNOWN,
        )

    def health(self):
        return GeneratorHealth.HEALTHY

    def generate(self, request: GeneratorRequest):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        path = request.output_dir / f"{request.request_id}.wav"
        samples = np.sin(np.linspace(0, np.pi * 8, 24000, dtype=np.float32)) * 0.1
        sf.write(path, samples, 24000)
        asset = GeneratedAsset(
            asset_id=f"test:{request.request_id}", path=path,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size,
            duration_s=1.0, sample_rate=24000, non_silent=True,
            model=self.describe().model, seed=request.seed, prompt=request.brief.user_intent,
            performance=PerformanceManifest(device="test-provider", actual_duration_s=1.0, sample_rate=24000),
            rights_manifest=RightsManifest(output_use=RightsClassification.UNKNOWN),
        )
        return GenerationBatch(batch_id=request.request_id, status="GENERATED", provider=self.provider_id,
                               model=self.describe().model, brief_id=request.brief.brief_id, assets=[asset])

    def cancel(self, request_id: str):
        del request_id


def test_vertical_slice_persists_generation_candidates_events_and_keep(tmp_path: Path) -> None:
    service = StudioService(tmp_path, provider_factory=lambda requested: FakeStudioProvider())
    project = service.create_project("Test Studio")
    job, created = service.submit_generation(project.project_id, {"prompt": "instrumental groove", "candidate_count": 2}, idempotency_key="same")
    assert created is True
    thread = service._threads[job.job_id]
    thread.join(timeout=5)
    data = service.get_job(job.job_id)
    assert data["job"]["status"] == "SUCCEEDED"
    assert len(data["candidates"]) == 2
    events = service.store.events_since(job.job_id)
    assert any(event.event_type == "candidate.ready" for event in events)
    again, created_again = service.submit_generation(project.project_id, {"prompt": "different"}, idempotency_key="same")
    assert again.job_id == job.job_id
    assert created_again is False
    version = service.keep_candidate(data["candidates"][0]["candidate_id"])
    assert version.manifest["ableton_writes"] == 0
    assert service.store.list_versions(project.project_id)[0].version_id == version.version_id


def test_missing_real_provider_blocks_without_fake_audio(tmp_path: Path) -> None:
    service = StudioService(tmp_path, provider_factory=lambda requested: None)
    project = service.create_project("Blocked Studio")
    job, _ = service.submit_generation(project.project_id, {"prompt": "no fake output"}, idempotency_key="blocked")
    service._threads[job.job_id].join(timeout=5)
    result = service.get_job(job.job_id)
    assert result["job"]["status"] == "BLOCKED"
    assert result["candidates"] == []
