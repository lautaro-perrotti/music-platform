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
    job, created = service.submit_generation(project.project_id, {"prompt": "instrumental groove", "candidate_count": 2, "target_duration_s": 1}, idempotency_key="same")
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
    assert version.manifest["selected_by"] == "HUMAN"
    assert service.store.get_state(project.project_id, "human_selection")["selected_candidate_id"] == data["candidates"][0]["candidate_id"]
    selected_event = service.store.events_since(job.job_id)[-1]
    assert selected_event.event_type == "candidate.human_selected"
    assert selected_event.payload["selected_by"] == "HUMAN"
    assert service.store.list_versions(project.project_id)[0].version_id == version.version_id


def test_missing_real_provider_blocks_without_fake_audio(tmp_path: Path) -> None:
    service = StudioService(tmp_path, provider_factory=lambda requested: None)
    project = service.create_project("Blocked Studio")
    job, _ = service.submit_generation(project.project_id, {"prompt": "no fake output"}, idempotency_key="blocked")
    service._threads[job.job_id].join(timeout=5)
    result = service.get_job(job.job_id)
    assert result["job"]["status"] == "BLOCKED"
    assert result["candidates"] == []


def test_hybrid_default_selects_elevenlabs_not_simulation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_STUDIO_MODE", "HYBRID")
    monkeypatch.delenv("MUSIC_GENERATOR_PROVIDER", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    service = StudioService(tmp_path)
    provider = service._default_provider(None)
    assert provider.provider_id == "elevenlabs-music"
    assert provider.health().value == "UNAVAILABLE"


def test_default_hybrid_generation_stops_at_typed_eleven_credential_boundary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_STUDIO_MODE", "HYBRID")
    monkeypatch.delenv("MUSIC_GENERATOR_PROVIDER", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    service = StudioService(tmp_path)
    project = service.create_project("No credentials means no fake candidates")
    job, _ = service.submit_generation(project.project_id, {"prompt": "instrumental house", "candidate_count": 5})
    service._threads[job.job_id].join(timeout=5)
    result = service.get_job(job.job_id)
    assert result["job"]["status"] == "BLOCKED"
    assert result["job"]["error"]["code"] == "CREDENTIAL_REQUIRED"
    assert result["job"]["error"]["credential"] == "ELEVENLABS_API_KEY"
    assert result["candidates"] == []


def test_simulation_provider_requires_explicit_simulation_mode_or_request(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_STUDIO_MODE", "HYBRID")
    monkeypatch.delenv("MUSIC_GENERATOR_PROVIDER", raising=False)
    service = StudioService(tmp_path)
    assert service._default_provider("simulation").provider_id == "simulated-music"
    assert service._default_provider("auto") is None


def test_stem_separation_refuses_before_human_selection(tmp_path: Path) -> None:
    service = StudioService(tmp_path, provider_factory=lambda requested: FakeStudioProvider())
    project = service.create_project("Stems require a human choice")
    job, _ = service.submit_generation(project.project_id, {"prompt": "instrumental groove", "candidate_count": 1, "target_duration_s": 1})
    service._threads[job.job_id].join(timeout=5)
    candidate = service.get_job(job.job_id)["candidates"][0]
    try:
        service.separate_selected_candidate_stems(candidate["candidate_id"])
    except ValueError as exc:
        assert str(exc) == "HUMAN_SELECTION_REQUIRED_BEFORE_STEM_SEPARATION"
    else:
        raise AssertionError("paid stem separation must require explicit human selection")


def test_only_human_selected_eleven_candidate_can_be_separated_and_repeat_is_idempotent(tmp_path: Path) -> None:
    class FakeElevenProvider(FakeStudioProvider):
        provider_id = "elevenlabs-music"
        calls = 0

        def describe(self):
            descriptor = super().describe()
            descriptor.model.model_id = "music_v2_5"
            return descriptor

        def separate_stems(self, source_asset, *, output_dir, variation_id="six_stems_v1"):
            self.calls += 1
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "stem-01.wav"
            sf.write(path, np.full((24000, 2), 0.04, dtype=np.float32), 24000)
            return [{
                "path": path,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
                "duration_s": 1.0,
                "sample_rate": 24000,
                "provider_member_name": "unclassified.wav",
                "provider": self.provider_id,
                "model_id": "music_v2_5",
                "stem_variation_id": variation_id,
                "source_asset_id": source_asset.asset_id,
                "source_asset_sha256": source_asset.sha256,
                "source_provider": self.provider_id,
            }]

    provider = FakeElevenProvider()
    service = StudioService(tmp_path, provider_factory=lambda requested: provider)
    project = service.create_project("Selected stem lineage")
    job, _ = service.submit_generation(
        project.project_id,
        {"prompt": "instrumental groove", "candidate_count": 1, "target_duration_s": 1},
    )
    service._threads[job.job_id].join(timeout=5)
    candidate = service.get_job(job.job_id)["candidates"][0]
    service.keep_candidate(candidate["candidate_id"])
    first = service.separate_selected_candidate_stems(candidate["candidate_id"])
    second = service.separate_selected_candidate_stems(candidate["candidate_id"])
    assert len(first) == 1
    assert [item.artifact_id for item in second] == [first[0].artifact_id]
    assert provider.calls == 1
    metadata = first[0].provider_metadata
    assert metadata["lineage"]["human_selected_candidate_id"] == candidate["candidate_id"]
    assert metadata["lineage"]["source_sha256"]
