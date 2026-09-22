from __future__ import annotations

from pathlib import Path

from copilot.music_generation.ace_step import AceStepProvider, choose_acestep_profile
from copilot.music_generation.schemas import GenerationBrief, GeneratorHealth, GeneratorRequest
from copilot.music_generation.registry import MusicGeneratorRegistry
from copilot.music_generation.resources import StorageVolume, WorkerResources, choose_execution_route


def test_four_gb_profile_is_dit_only_and_not_xl() -> None:
    profile = choose_acestep_profile(4.0)
    assert profile["dit"] == "acestep-v15-turbo"
    assert profile["lm"] is None
    assert profile["offload"] is True


def test_ace_provider_is_truthful_when_checkpoint_is_absent(tmp_path: Path) -> None:
    provider = AceStepProvider(model_path=tmp_path / "missing")
    assert provider.health() is GeneratorHealth.UNAVAILABLE
    brief = GenerationBrief(brief_id="test", user_intent="instrumental electronic music", target_duration_s=10)
    batch = provider.generate(GeneratorRequest(request_id="candidate-a", brief=brief, seed=1, output_dir=tmp_path))
    assert batch.status == "GENERATOR_UNAVAILABLE"
    assert batch.assets == []
    assert batch.failures[0]["code"] == "MODEL_MISSING"


def test_generator_registry_does_not_conflate_reasoning_provider() -> None:
    registry = MusicGeneratorRegistry()
    provider = AceStepProvider(model_path="missing")
    registry.register(provider)
    assert registry.descriptors()[0].provider_id == "ace-step"
    assert registry.descriptors()[0].health is GeneratorHealth.UNAVAILABLE


def test_official_worker_request_is_dit_only_and_provenance_safe(tmp_path: Path, monkeypatch) -> None:
    provider = AceStepProvider(api_url="http://127.0.0.1:8001")
    brief = GenerationBrief(
        brief_id="worker-test",
        user_intent="instrumental electronic groove",
        target_duration_s=10,
        tempo_bpm=128,
        key_context="A minor",
        meter="4/4",
    )
    request = GeneratorRequest(request_id="candidate-worker", brief=brief, seed=7, output_dir=tmp_path)
    calls = []
    monkeypatch.setattr(provider, "_request_json", lambda method, path, payload, timeout: calls.append((method, path, payload)) or {"data": {"task_id": "task-1"}})

    assert provider._submit(request) == "task-1"
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/release_task")
    assert payload["model"] == "acestep-v15-turbo"
    assert payload["thinking"] is False
    assert payload["audio_format"] == "wav"
    assert payload["seed"] == 7


def test_worker_result_path_can_be_extracted_from_official_wrapped_payload() -> None:
    payload = {"data": [{"result": '[{"file": "C:\\\\audio\\\\candidate.wav"}]', "status": 1}]}
    assert AceStepProvider._result_audio_source(payload) == "C:\\audio\\candidate.wav"


def test_worker_route_uses_measured_volume_and_does_not_degrade_model() -> None:
    resources = WorkerResources(
        operating_system="Darwin",
        architecture="arm64",
        processor="Apple M3 Pro",
        memory_bytes=36 * 1024**3,
        volumes=[
            StorageVolume(
                mount="/",
                filesystem="apfs",
                total_bytes=1_000,
                free_bytes=20 * 1024**3,
                writable=True,
            )
        ],
    )
    route = choose_execution_route(resources, required_bytes=10 * 1024**3)
    assert route.route == "LOCAL"
    assert route.selected_mount == "/"


def test_worker_route_fails_to_cloud_when_mac_or_windows_volume_is_too_small() -> None:
    resources = WorkerResources(
        operating_system="Darwin",
        architecture="arm64",
        processor="Apple Silicon",
        memory_bytes=16 * 1024**3,
        volumes=[
            StorageVolume(
                mount="/",
                filesystem="apfs",
                total_bytes=1_000,
                free_bytes=5 * 1024**3,
                writable=True,
            )
        ],
    )
    route = choose_execution_route(resources, required_bytes=10 * 1024**3)
    assert route.route == "CLOUD_REQUIRED"
    assert route.selected_mount is None
