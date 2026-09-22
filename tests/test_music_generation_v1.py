from __future__ import annotations

from pathlib import Path

from copilot.music_generation.ace_step import AceStepProvider, choose_acestep_profile
from copilot.music_generation.elevenlabs import ElevenLabsMusicProvider, build_elevenlabs_composition_plan
from copilot.music_generation.benchmark import (
    CandidateRecord,
    ProviderComparisonReport,
    TechnicalValidation,
    build_provider_comparison_benchmark,
    persist_human_evaluation,
    validate_generated_audio,
)
from copilot.music_generation.executive_producer import ExecutiveProducerAdapter, ExecutiveProducerContext
from copilot.music_generation.generated_asset_import import stage_generated_asset
from copilot.music_generation.schemas import GeneratedAsset, GenerationBrief, GeneratorHealth, GeneratorRequest, ModelManifest, PerformanceManifest, RightsManifest
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


def test_official_worker_repaint_uses_multipart_and_preserves_lineage(tmp_path: Path, monkeypatch) -> None:
    provider = AceStepProvider(api_url="http://127.0.0.1:8001")
    source = tmp_path / "parent.wav"
    source.write_bytes(b"RIFF-test-audio")
    brief = GenerationBrief(
        brief_id="repaint-test",
        user_intent="preserve groove and repaint transition",
        target_duration_s=10,
    )
    request = GeneratorRequest(
        request_id="repaint-request",
        brief=brief.model_copy(update={"generation_mode": "repaint"}),
        seed=9,
        output_dir=tmp_path,
        source_audio_path=source,
        edit_region_start_s=2.0,
        edit_region_end_s=4.0,
        lineage=["ace-step:parent", "parent-sha256", "repaint:2.0-4.0s"],
    )
    calls = []
    monkeypatch.setattr(
        provider,
        "_request_multipart_audio",
        lambda path, payload, *, field_name, file_path, timeout: calls.append(
            (path, payload, field_name, file_path, timeout)
        ) or {"data": {"task_id": "repaint-task"}},
    )

    assert provider._submit(request) == "repaint-task"
    path, payload, field_name, file_path, timeout = calls[0]
    assert path == "/release_task"
    assert field_name == "src_audio"
    assert file_path == source
    assert payload["task_type"] == "repaint"
    assert payload["repainting_start"] == 2.0
    assert payload["repainting_end"] == 4.0
    assert "src_audio_path" not in payload

    # The request contract carries lineage; the worker boundary cannot erase it.
    assert request.lineage == ["ace-step:parent", "parent-sha256", "repaint:2.0-4.0s"]


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


def test_worker_route_checks_mac_unified_memory_without_quality_degradation() -> None:
    resources = WorkerResources(
        operating_system="Darwin",
        architecture="arm64",
        processor="Apple M2",
        memory_bytes=16 * 1024**3,
        memory_kind="UNIFIED",
        volumes=[
            StorageVolume(
                mount="/",
                filesystem="apfs",
                total_bytes=100 * 1024**3,
                free_bytes=40 * 1024**3,
                writable=True,
            )
        ],
    )
    route = choose_execution_route(
        resources,
        required_bytes=10 * 1024**3,
        required_memory_bytes=24 * 1024**3,
    )
    assert route.route == "CLOUD_REQUIRED"
    assert route.resource_checks == {"memory": "INSUFFICIENT", "storage": "PASS"}
    assert route.required_memory_bytes == 24 * 1024**3


def test_worker_route_checks_gpu_vram_independently_of_storage() -> None:
    resources = WorkerResources(
        operating_system="Windows",
        architecture="AMD64",
        processor="GPU worker",
        memory_bytes=32 * 1024**3,
        gpu_vram_bytes=6 * 1024**3,
        volumes=[
            StorageVolume(
                mount="D:\\",
                filesystem="ntfs",
                total_bytes=100 * 1024**3,
                free_bytes=50 * 1024**3,
                writable=True,
            )
        ],
    )
    route = choose_execution_route(
        resources,
        required_bytes=10 * 1024**3,
        required_gpu_vram_bytes=8 * 1024**3,
    )
    assert route.route == "CLOUD_REQUIRED"
    assert route.resource_checks == {"gpu_vram": "INSUFFICIENT", "storage": "PASS"}


def test_generated_asset_validation_is_factual_and_hash_bound(tmp_path: Path) -> None:
    import hashlib
    import soundfile as sf
    import numpy as np

    path = tmp_path / "candidate.wav"
    sf.write(path, np.ones((4800, 1), dtype=np.float32) * 0.1, 48_000)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    asset = GeneratedAsset(
        asset_id="ace-step:candidate",
        path=path,
        sha256=digest,
        bytes=path.stat().st_size,
        duration_s=0.1,
        sample_rate=48_000,
        non_silent=True,
        model=ModelManifest(provider="ace-step", model_id="test", quality_tier="LOCAL_COST_TIER"),
        seed=7,
        prompt="instrumental test",
        performance=PerformanceManifest(device="test"),
        rights_manifest=RightsManifest(),
    )
    result = validate_generated_audio(asset, expected_duration_s=0.1)
    assert result.status == "VALID"
    assert result.hash_matches is True
    assert result.provenance_complete is True


def test_provider_comparison_benchmark_hides_identity_and_records_preference(tmp_path: Path) -> None:
    import hashlib
    import numpy as np
    import soundfile as sf

    records = {}
    for provider_id, level in (("ace-local", 0.1), ("premium-cloud", 0.2)):
        bundle = tmp_path / provider_id / "candidate_001"
        bundle.mkdir(parents=True)
        path = bundle / "raw.wav"
        sf.write(path, np.ones((4800, 1), dtype=np.float32) * level, 48_000)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        asset = GeneratedAsset(
            asset_id=f"{provider_id}:candidate-001",
            path=path,
            sha256=digest,
            bytes=path.stat().st_size,
            duration_s=0.1,
            sample_rate=48_000,
            non_silent=True,
            model=ModelManifest(provider=provider_id, model_id="test", quality_tier="TEST"),
            seed=1,
            prompt="same brief",
            performance=PerformanceManifest(device="test"),
            rights_manifest=RightsManifest(),
        )
        records[provider_id] = type("Report", (), {
            "candidates": [CandidateRecord(
                candidate_id="candidate_001",
                attempt=1,
                asset=asset,
                bundle_path=bundle,
                technical_validation=TechnicalValidation(
                    readable=True, non_empty=True, non_silent=True,
                    expected_duration=True, valid_channels=True,
                    valid_sample_rate=True, finite_samples=True,
                    hash_matches=True, provenance_complete=True,
                    status="VALID",
                ),
            )]
        })()

    report = build_provider_comparison_benchmark(records, baseline_manifest=None, output_dir=tmp_path / "benchmark")
    assert isinstance(report, ProviderComparisonReport)
    assert report.providers == ["ace-local", "premium-cloud"]
    manifest = (report.listener_dir / "listener_manifest.json").read_text(encoding="utf-8")
    assert "ace-local" not in manifest
    assert "premium-cloud" not in manifest
    evaluation_path = persist_human_evaluation(report, [{"blind_id": "blind_001", "score": 5}])
    assert evaluation_path.is_file()


def test_executive_producer_boundary_does_not_invent_writes() -> None:
    brief = GenerationBrief(brief_id="boundary", user_intent="instrumental groove", target_duration_s=10)
    decision = ExecutiveProducerAdapter().build_decision(
        ExecutiveProducerContext(user_intent="instrumental groove"),
        generation_brief=brief,
        provider_status="REASONING_PROVIDER_LIMITED",
    )
    assert decision.no_musical_invention is True
    assert decision.no_ableton_access is True
    assert decision.production_refinement_intents == []


def test_elevenlabs_plan_preserves_brief_intent_and_exact_duration() -> None:
    brief = GenerationBrief(
        brief_id="eleven-plan",
        user_intent="instrumental electronic groove with evolving texture",
        target_duration_s=60,
        tempo_bpm=127,
        key_context="A minor",
        structural_intent=["INTRO", "GROOVE", "DROP", "OUTRO"],
        energy_intent="rising then peak",
        groove_intent="four on the floor with syncopated hats",
        density_intent="sparse intro, dense peak",
    )
    plan = build_elevenlabs_composition_plan(brief)
    assert sum(chunk["duration_ms"] for chunk in plan["chunks"]) == 60_000
    assert [chunk["text"] for chunk in plan["chunks"]] == ["[INTRO]", "[GROOVE]", "[DROP]", "[OUTRO]"]
    assert "instrumental electronic groove with evolving texture" in plan["chunks"][0]["positive_styles"]
    assert "127 BPM" in plan["chunks"][0]["positive_styles"]
    assert "vocals" in plan["chunks"][0]["negative_styles"]


def test_elevenlabs_provider_stops_at_real_credential_boundary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    provider = ElevenLabsMusicProvider(api_key=None)
    assert provider.health().value == "UNAVAILABLE"
    brief = GenerationBrief(
        brief_id="eleven-credential",
        user_intent="instrumental electronic music",
        target_duration_s=10,
    )
    batch = provider.generate(
        GeneratorRequest(request_id="eleven-credential", brief=brief, seed=1, output_dir=tmp_path)
    )
    assert batch.status == "GENERATOR_UNAVAILABLE"
    assert batch.failures[0]["code"] == "CREDENTIAL_REQUIRED"


def test_elevenlabs_detailed_multipart_parser_keeps_metadata_and_audio() -> None:
    body = (
        b"--demo\r\nContent-Type: application/json\r\n\r\n"
        b'{"song_metadata":{"title":"test"}}\r\n'
        b"--demo\r\nContent-Type: audio/mpeg\r\n\r\nMP3BYTES\r\n"
        b"--demo--\r\n"
    )
    metadata, audio = ElevenLabsMusicProvider._parse_detailed_response(
        body, "multipart/mixed; boundary=demo"
    )
    assert metadata["song_metadata"]["title"] == "test"
    assert audio == b"MP3BYTES"
